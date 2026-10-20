# Troubleshooting

- **API exits on startup:** Configure a >=32-character API_TOKEN, allowed user IDs,
  DGX key/model and existing runtime database URL. JSON lists/dicts in `.env` must
  use valid JSON. No application services auto-create infrastructure.
- **Readiness 503:** Check PostgreSQL network/TLS, runtime grants, Alembic and
  checkpoint setup. Liveness only means the HTTP process is alive. API readiness
  checks application tables; it does not claim every external service is healthy.
- **Bot silent:** Verify bot email/API key, user ID allowlist, one-to-one DM or
  allowlisted private stream. Ensure bot is subscribed. Resend commands after an
  expired queue or offline period; historical messages are not backfilled.
- **Permission denied in PostgreSQL:** Migration and runtime roles differ. Grant
  lifeos schema USAGE and data privileges to runtime; rerun grants after checkpoint
  migrations. Do not grant runtime schema CREATE to conceal a missing migration.
- **Approval cannot resume:** Only its original user can decide; it must be pending
  and unexpired. Use the approval UUID, not just the run ID. Examine worker logs and
  workflow_events status. Do not directly edit checkpoint or approval payloads.
- **Task changed while waiting:** The reviewed task snapshot is stale. Request a new
  plan. This protects against applying a proposal to changed user records.
- **DGX unavailable:** Test the private endpoint /models with authentication, VPN
  route, firewall and exact model ID. Configure structured output mode supported
  by the endpoint. ModelUnavailable intentionally avoids logging provider secrets.
- **Manifest-only result:** Expected when image/TTS providers are absent. The
  manifest, transcript and captions still exist; no final MP4 is claimed.
- **ElevenLabs/image failure:** Check credentials, selected model/voice, quota and
  adapter request compatibility. Mock providers are for tests and produce silent
  audio/placeholder images, not real voiceover/content.
- **FFmpeg failure:** Install ffmpeg with libass and Noto Devanagari fonts. Verify
  every scene has valid local image/audio files and audio duration fits tempo limits.
  Docker installs these dependencies; host environments must supply them.
- **Upload too large:** The bot uses a 24MiB upload budget. Use GET /v1/runs/{id}
  to list artifact IDs and authenticated GET /v1/artifacts/{id} to download instead.
- **Duplicate notification:** Remote delivery is at least once. n8n must enforce
  Idempotency-Key. Business records and approval decisions remain replay protected.
- **Growing storage:** Checkpoint retention and artifact cleanup are operator policy;
  no automatic deletion is installed. Back up before pruning finished-run data.
- **Healthcheck failure:** Check heartbeat/required loops, PostgreSQL and bot network.
  A heartbeat shows process activity, not proof of an operational external service.

Safe diagnostics: `docker compose -f infrastructure/compose.yaml logs --tail=100`,
`docker compose -f infrastructure/compose.yaml ps`, and API readiness. Never paste
raw `.env`, database URLs or API keys into logs/issues. Configure an existing OTLP
collector with OTEL_EXPORTER_OTLP_ENDPOINT to receive operation spans and metrics.
