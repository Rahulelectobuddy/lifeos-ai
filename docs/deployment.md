# Deployment and operations

## Prerequisites and isolation

Use Python 3.12, Docker Engine/Compose v2, reachable existing PostgreSQL with a
migration role and a runtime role, a Zulip generic bot, and authenticated DGX
inference over VPN or HTTPS. Existing services are not recreated. Three LifeOS
services share one named artifact volume; only the API publishes a loopback port.
The image runs UID/GID 10001, drops capabilities, uses a read-only root filesystem,
and includes FFmpeg/libass plus Noto Devanagari fonts. The artifact volume is
initialized with ownership 10001. In a host bind mount, set that ownership yourself.

## Database provisioning

Apply infrastructure/provision_roles.sql as an administrator on the **existing
application database**. Adapt example role names to local policy. Set passwords
with psql \password and grant CONNECT on the selected database if your deployment
restricts database access. The schema is owned by lifeos_migrator. The runtime role
has USAGE plus SELECT/INSERT/UPDATE/DELETE on lifeos tables, never CREATE or DDL.
Default privileges must be declared for the role that actually runs migrations.
After checkpoint setup, confirm runtime data grants cover checkpoint tables too.

The runtime database URL must use the direct/session-pooled PostgreSQL endpoint,
not transaction-mode pooling: advisory locks/checkpointer connections require
stable sessions. URL-encode passwords. Use sslmode=require/verify-full as appropriate
for your provider. With Supabase, use a direct connection or its session pooler,
not the REST API. Configure role/schema policy through your database administrator.

Follow the exact commands in README: build image, apply Alembic, set up LangGraph
checkpoints with migration-only credentials, unset them, then start application
services. Do not put MIGRATION_DATABASE_URL in the runtime `.env` file.

## Zulip configuration

Create a generic bot with access only to your personal organization/channel.
Set ZULIP_URL, ZULIP_BOT_EMAIL, ZULIP_API_KEY and numeric ALLOWED_USER_IDS JSON.
Default ALLOWED_STREAM_IDS=[] means only DMs. If enabling streams, use private
channels containing only the intended readers; Zulip has topic-level routing,
not independent topic ACLs. Configure a reverse proxy/tunnel for Zulip separately.
No Zulip container or Cloudflare Tunnel settings are installed by LifeOS.

The bot cursor is held for the lifetime of its queue and advances only after
durable enqueue. Replayed events deduplicate by message ID. If a queue expires
or the bot was offline, historical messages are not automatically backfilled;
resend a command. Already enqueued runs/approvals remain durable.

## Updates

### GitHub Actions deployment to a separate server

`.github/workflows/deploy.yml` runs on pushes to `main` or manual dispatch from
`main`. It reuses the test workflow before deploying. Install a GitHub Actions
self-hosted runner **on the LifeOS server** with labels `self-hosted`, `linux`,
and `lifeos-ai`. Give only that server the `lifeos-ai` label so the existing
homelab/homestack runners cannot pick up this deployment. Either x64 or ARM64
Linux can be used. Install Docker Engine, Docker Compose v2.24.4+ and rsync;
the runner account must have Docker access and write access to the deployment
directory.

Prepare the server as the intended runner account:

```bash
sudo mkdir -p /opt/lifeos-ai/lifeos-ai/lifeos-ai
sudo chown "$USER":"$(id -gn)" /opt/lifeos-ai/lifeos-ai/lifeos-ai
# Copy .env.example to /opt/lifeos-ai/lifeos-ai/lifeos-ai/.env, configure real service credentials,
# then restrict its permissions:
chmod 600 /opt/lifeos-ai/lifeos-ai/lifeos-ai/.env
```

Provision database roles/schema once using the instructions above. In this
repository's GitHub settings, create an environment named `lifeos-production`
and add its secret `MIGRATION_DATABASE_URL` using the migration-role SQLAlchemy
URL (`postgresql+psycopg://...`). Keep the runtime-role URL in the server's `.env`.
The migration secret is passed only to the one-off migration containers, and is
not added to the running services or saved in the deployment directory.

The workflow syncs source to `/opt/lifeos-ai/lifeos-ai/lifeos-ai`, preserving the server's `.env`
files and local artifacts, builds the image on that server, applies Alembic and
checkpoint migrations, and starts API, worker and bot. Deployment fails if the
services do not become healthy within 180 seconds. The named artifact volume
survives deployments. Source files managed by Git are replaced during sync;
keep unrelated server files outside `/opt/lifeos-ai/lifeos-ai/lifeos-ai`.

The deployment checkout uses `clean: false` because this server keeps its `.env`
inside the runner checkout. The checkout action's default `git clean -ffdx`
deletes ignored files, including `.env`. The subsequent rsync also excludes
`.env` and `.env.*`. This does not recover an already deleted file: restore it
from your secret store or backup before rerunning. For longer-term isolation,
keep runtime secrets outside the runner workspace and point `LIFEOS_ENV_FILE`
to that absolute path; workspace replacement or manual cleanup can still
remove files stored inside it. The test workflow runs on a disposable
GitHub-hosted runner and keeps its normal clean checkout.

Migrations run while the previous application containers may still be serving,
so future schema changes must remain compatible with that version. A failed
deployment is reported without automatic database rollback. The server must
reach your existing PostgreSQL, Zulip and DGX endpoints; configure those
addresses for this server's network in its `.env`.

### Manual updates

```bash
git pull
uv sync --frozen --extra dev
uv run pytest -q
docker compose -f infrastructure/compose.yaml build
# Supply migration-only URL privately as in README.
docker compose -f infrastructure/compose.yaml run --rm -e MIGRATION_DATABASE_URL api alembic upgrade head
docker compose -f infrastructure/compose.yaml run --rm -e MIGRATION_DATABASE_URL api python -m lifeos.persistence.setup_checkpoints
unset MIGRATION_DATABASE_URL
docker compose -f infrastructure/compose.yaml up -d
```

For rolling schema changes, use backward-compatible migrations. A forced shutdown
reclaims incomplete jobs after their lease expires. Graceful stop lets current
work complete when it fits the stop timeout; longer renders can be interrupted
and resumed from durable checkpoints. Monitor run errors, failed outbox events,
provider quota, checkpoint volume and artifact storage growth.

## Backup and recovery

Stop worker and bot while taking a coordinated snapshot. Use existing PostgreSQL
backup tooling to dump the lifeos schema and preserve roles/grants securely. Example:

```bash
docker compose -f infrastructure/compose.yaml stop worker zulip-bot
# PGHOST/PGDATABASE/PGUSER/PGPASSWORD configured privately for your backup role.
pg_dump --format=custom --schema=lifeos --file=lifeos.dump
# Copy artifacts from the application volume without adding a storage container.
docker compose -f infrastructure/compose.yaml cp api:/data/artifacts ./artifacts-backup
docker compose -f infrastructure/compose.yaml start worker zulip-bot
```

Back up `.env` separately in your secret store and S3 objects with existing storage
backup/versioning. Include workflow_events, approval_requests and ALL checkpoint
tables in the same database dump. Preserve timezone/user IDs and stable artifact
keys. On recovery, provision the existing database roles/schema, restore the
schema dump with pg_restore using the migration role, restore data grants, then
copy artifacts into the named volume with UID/GID 10001 ownership. Start the API,
verify readiness, then worker and bot. Do not run unrelated schema restores.

Neither env secrets nor credentials belong in Git. Avoid logging raw requests,
transcripts, model responses or provider exceptions. The API's shared token is
for trusted operator clients that can assert X-Zulip-User-ID; for public UI access,
add a real authenticated identity gateway before exposing endpoints.


## Isolated smoke test

`bash scripts/smoke-test.sh` uses disposable local fixtures and creates no
replacement production infrastructure. It requires the local Docker engine and
internet access for first image pulls. Set SMOKE_SKIP_BUILD=1 to reuse the current
lifeos-ai:0.1.0 image after building it yourself. The test uses its own uniquely
named project/network/volume and test-only credentials. A mock HTTP service accepts
model calls and Zulip requests and automatically approves fixture proposals only.
This test automation is never enabled in the application.
