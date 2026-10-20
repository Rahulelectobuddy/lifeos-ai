#!/usr/bin/env bash
# Isolated test fixtures only; production Compose remains application-only.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
SMOKE_SUFFIX="$(date +%s)-$$"
PROJECT="lifeos-ai-smoke-$SMOKE_SUFFIX"
NETWORK="$PROJECT"
DB="$PROJECT-postgres"
MOCK="$PROJECT-mock"
TEMP_DIR=$(mktemp -d)
IMAGE=lifeos-ai:0.1.0
export LIFEOS_ENV_FILE="$TEMP_DIR/runtime.env"
cleanup() {
  docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" down --volumes >/dev/null 2>&1 || true
  docker rm -f "$MOCK" "$DB" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT
if [ "${SMOKE_SKIP_BUILD:-0}" != "1" ]; then
  docker build -t "$IMAGE" .
fi
docker network create "$NETWORK" >/dev/null
docker run -d --rm --name "$DB" --network "$NETWORK" \
  --env POSTGRES_USER=lifeos_test --env POSTGRES_PASSWORD=disposable-test-only \
  --env POSTGRES_DB=lifeos_test postgres:17-alpine >/dev/null
for attempt in $(seq 1 60); do
  # The image briefly accepts connections to its temporary initialization server.
  # Wait for init to finish before testing readiness of the final server.
  if docker logs "$DB" 2>&1 | grep -q 'PostgreSQL init process complete'; then
    if docker exec "$DB" pg_isready -U lifeos_test -d lifeos_test >/dev/null 2>&1; then break; fi
  fi
  sleep 1
done
docker exec -i "$DB" psql -U lifeos_test -d lifeos_test -v ON_ERROR_STOP=1 < infrastructure/provision_roles.sql
docker exec "$DB" psql -U lifeos_test -d lifeos_test -v ON_ERROR_STOP=1 -c \
  "ALTER ROLE lifeos_migrator PASSWORD 'disposable-migration-only'; ALTER ROLE lifeos_app PASSWORD 'disposable-runtime-only';"
MIGRATION_URL="postgresql+psycopg://lifeos_migrator:disposable-migration-only@$DB:5432/lifeos_test"
for command in migrations checkpoints; do
  if [ "$command" = migrations ]; then
    docker run --rm --network "$NETWORK" --env "MIGRATION_DATABASE_URL=$MIGRATION_URL" "$IMAGE" alembic upgrade head
  else
    docker run --rm --network "$NETWORK" --env "MIGRATION_DATABASE_URL=$MIGRATION_URL" "$IMAGE" python -m lifeos.persistence.setup_checkpoints
  fi
done
docker run -d --rm --name "$MOCK" --network "$NETWORK" \
  --mount "type=bind,source=$ROOT/tests/fixtures,target=/fixture,readonly" \
  "$IMAGE" uvicorn mock_services:app --app-dir /fixture --host 0.0.0.0 --port 8001 --no-access-log >/dev/null
cat > "$LIFEOS_ENV_FILE" <<ENV
DATABASE_URL=postgresql+psycopg://lifeos_app:disposable-runtime-only@$DB:5432/lifeos_test
API_TOKEN=local-disposable-smoke-test-token-123456789
ALLOWED_USER_IDS=[123]
ALLOWED_STREAM_IDS=[]
ZULIP_URL=http://$MOCK:8001
ZULIP_BOT_EMAIL=fixture-bot@example.invalid
ZULIP_API_KEY=disposable-test-key
DGX_BASE_URL=http://$MOCK:8001/v1
DGX_API_KEY=disposable-test-key
DGX_MODEL=test-model
DGX_PRIVATE_NETWORK=true
IMAGE_PROVIDER=none
TTS_PROVIDER=none
ENV
cat > "$TEMP_DIR/compose.yaml" <<YAML
services:
  api:
    ports: !override
      - "127.0.0.1:0:8000"
    networks: [smoke]
  zulip-bot:
    networks: [smoke]
  worker:
    networks: [smoke]
networks:
  smoke:
    external: true
    name: $NETWORK
YAML
docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" up -d --no-build
for attempt in $(seq 1 120); do
  RESULT=$(docker exec "$DB" psql -U lifeos_test -d lifeos_test -At -c \
    "SELECT count(*) FROM lifeos.agent_runs WHERE (agent_type='planning' AND status='completed') OR (agent_type='content' AND status='manifest_only');")
  if [ "$RESULT" = 2 ]; then break; fi
  if [ "$attempt" = 120 ]; then
    docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" logs --tail=100
    exit 1
  fi
  sleep 2
done
docker exec "$DB" psql -U lifeos_test -d lifeos_test -c \
  "SELECT agent_type,status FROM lifeos.agent_runs; SELECT title FROM lifeos.tasks; SELECT status,stage FROM lifeos.approval_requests;"
docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready').read().decode())"
# Hold a third run at approval, then restart the worker before deciding via API.
docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" stop zulip-bot >/dev/null
RUN_ID=$(docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" exec -T api python -c \
  'import json,os,uuid,urllib.request; body=json.dumps({"agent_type":"planning","request":"Plan a daily review","idempotency_key":str(uuid.uuid4())}).encode(); request=urllib.request.Request("http://127.0.0.1:8000/v1/runs",data=body,headers={"Content-Type":"application/json","Authorization":"Bearer "+os.environ["API_TOKEN"],"X-Zulip-User-ID":"123"}); print(json.load(urllib.request.urlopen(request))["run_id"])')
APPROVAL_ID=""
for attempt in $(seq 1 60); do
  APPROVAL_ID=$(docker exec "$DB" psql -U lifeos_test -d lifeos_test -At -c \
    "SELECT id FROM lifeos.approval_requests WHERE run_id='$RUN_ID' AND status='pending';")
  if [ -n "$APPROVAL_ID" ]; then break; fi
  sleep 1
done
[ -n "$APPROVAL_ID" ]
docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" restart worker >/dev/null
docker compose -p "$PROJECT" -f infrastructure/compose.yaml -f "$TEMP_DIR/compose.yaml" exec -T api python -c \
  'import json,os,sys,urllib.request; request=urllib.request.Request("http://127.0.0.1:8000/v1/approvals/"+sys.argv[1],data=b"{\"approve\":true}",headers={"Content-Type":"application/json","Authorization":"Bearer "+os.environ["API_TOKEN"],"X-Zulip-User-ID":"123"}); print(json.load(urllib.request.urlopen(request))["status"])' "$APPROVAL_ID"
STATUS=""
for attempt in $(seq 1 60); do
  STATUS=$(docker exec "$DB" psql -U lifeos_test -d lifeos_test -At -c "SELECT status FROM lifeos.agent_runs WHERE id='$RUN_ID';")
  if [ "$STATUS" = completed ]; then break; fi
  sleep 1
done
[ "$STATUS" = completed ]
CAN_CREATE=$(docker exec "$DB" psql -U lifeos_test -d lifeos_test -At -c "SELECT has_schema_privilege('lifeos_app','lifeos','CREATE');")
[ "$CAN_CREATE" = f ]
printf 'LifeOS smoke test passed: three services, real migrations, approved planning, manifest-only content, durable approval across worker restart, and runtime role without schema CREATE.\n'
