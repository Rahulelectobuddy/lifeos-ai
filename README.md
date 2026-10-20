# LifeOS AI

A self-hosted Python application with two independent LangGraph workflows:
personal planning and Hindi YouTube Shorts production. Zulip is the primary UI.
The API, Zulip bot, and worker reuse your existing PostgreSQL, DGX inference,
n8n, Airflow, and storage. **No infrastructure replacement containers are deployed.**

## Implemented behavior

- `/plan` retrieves user-owned records, proposes a structured daily/weekly plan,
  validates capacity/effort/dependencies, and interrupts for human approval.
  Approved plans and proposed life areas, projects, tasks, task updates, and
  preferences are persisted transactionally. Rejection causes no planning writes.
- `/short` runs idea → optional research → Hindi script → storyboard → approval
  → separate images → scene voiceovers → captions → FFmpeg → review → final MP4.
  Missing providers produce a truthful manifest-only result. Mock assets are
  explicitly labeled images and silent audio. Publishing is never automatic.
- Persistent PostgreSQL checkpoints, run-scoped approvals, authenticated owner
  decisions, expiration, leased jobs, advisory run locks, replay-safe planning
  writes, outbox notifications, structured logs, and OpenTelemetry metrics.
- `/short revise <run UUID> <scene number> <instruction>` changes only that scene
  in a **new approval workflow**. The source run remains intact.

## Essential configuration

Supply existing service credentials in `.env`: PostgreSQL **runtime** role,
Zulip bot email/key, allowed Zulip user IDs, private DGX URL/key/model, and a long
API token. Optional image, ElevenLabs, research, n8n, S3, and Airflow credentials
are only needed when those integrations are enabled. All addresses and model
identifiers are configurable; there are no homelab addresses in application code.

## Quick start

Python 3.12+, uv, Docker Compose v2, and existing PostgreSQL are required.
Run commands from the repository root.

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with your existing service configuration.
uv sync --frozen --extra dev
uv run ruff check .
uv run pytest -q

docker compose -f infrastructure/compose.yaml config --quiet
docker compose -f infrastructure/compose.yaml build
```

Provision migration/runtime roles on your existing database using
[infrastructure/provision_roles.sql](infrastructure/provision_roles.sql).
Set role passwords privately using psql's `\password`. Read
[deployment.md](docs/deployment.md) for database-specific permissions.

Supply the migration URL **only** to one-off migration commands, not `.env` or
running application services:

```bash
read -r -s -p "Migration PostgreSQL URL: " MIGRATION_DATABASE_URL
export MIGRATION_DATABASE_URL
# Format: postgresql+psycopg://lifeos_migrator:password@host:5432/database

docker compose -f infrastructure/compose.yaml run --rm -e MIGRATION_DATABASE_URL api alembic upgrade head
docker compose -f infrastructure/compose.yaml run --rm -e MIGRATION_DATABASE_URL api python -m lifeos.persistence.setup_checkpoints
unset MIGRATION_DATABASE_URL

docker compose -f infrastructure/compose.yaml up -d
docker compose -f infrastructure/compose.yaml ps
docker compose -f infrastructure/compose.yaml logs -f --tail=100 api zulip-bot worker
curl --fail http://127.0.0.1:8000/health/ready
```

The `read -p` example uses Bash; in Zsh use `read -s 'MIGRATION_DATABASE_URL?Migration PostgreSQL URL: '`.
Do not expose the API publicly: it binds to host loopback and requires a bearer
token plus an allowlisted `X-Zulip-User-ID`. That identity header is a trusted
operator interface, not a substitute for an end-user login system.

Create a Zulip generic bot and DM it:

```text
/help
/plan Plan my next week. I have 90 minutes every weekday for personal projects.
/short A relatable Hindi story about building small daily habits.
/approve <APPROVAL_UUID>
/status <RUN_UUID>
```

The bot sends proposals, progress, approval commands, manifests, and media
artifacts back to the requesting DM or allowlisted private channel/topic.
Use `/projects` and `/tasks` to inspect your records. Initially empty records
are valid: request project/task creation in `/plan`, then approve the proposal.

## Documentation

- [Architecture and reliability](docs/architecture.md)
- [Deployment, updating, backup and recovery](docs/deployment.md)
- [Agent development and learning guide](docs/agent-development.md)
- [DGX and media provider configuration](docs/model-serving.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Example Zulip requests](examples/sample_requests/commands.txt)
- [Existing n8n workflow setup](examples/n8n/README.md)

## Verification scope

Default tests run without network or paid APIs, using a temporary SQLite file
for ORM/business transactions and in-memory graph checkpoints. Optional tests
exercise a migrated isolated PostgreSQL database and real FFmpeg. These tests
are marked `postgres` and `ffmpeg` and explicitly skip without prerequisites.
SQLite is **only a test dependency**, not deployed or used in production.

```bash
# Real database test: use an isolated, already migrated database.
LIFEOS_TEST_DATABASE_URL='postgresql+psycopg://role:password@host/test_db' uv run pytest -m postgres -q
# FFmpeg and ffprobe must be on PATH; produces a full mock video.
uv run pytest -m ffmpeg -q
```

For a complete local smoke test with **disposable test fixtures**, run:

```bash
bash scripts/smoke-test.sh
```

This builds the image, creates an isolated PostgreSQL fixture and a mock
Zulip/DGX HTTP service, applies migrations with a migration role, runs all three
application containers with a runtime role, routes `/plan` and `/short`, approves
fixture proposals, and checks completion/readiness. It removes its containers,
network and volume on exit. It never calls your real Zulip/DGX services.

External delivery is at least once: Zulip notifications can duplicate after a
crash during send, and n8n must honor the supplied `Idempotency-Key`. Paid media
calls may be retried if a process dies before saving their response; consult
provider billing/idempotency guarantees. Application planning writes have
transactional replay protection. Scene captions are estimated from scene timing,
not forced alignment. Fades are fades through black, not overlapping crossfades.
See [verification.md](docs/verification.md) for checks actually performed here.
