# Verification — 20 October 2026

LifeOS builds and its core workflows operate against isolated local fixtures.
This is a development verification, not a deployment against real services.

## Checks performed

- `ruff check .`: passed.
- `pytest -q`: **43 passed, 2 skipped** after the review fixes. The default suite
  covers API authentication/ownership, approvals and rejection, persistence,
  replay, revisions, provider failures, and worker retry status. PostgreSQL and
  local FFmpeg tests skip without their prerequisites.
- `uv lock --check --offline`: passed. The runtime requirements export matches
  `requirements.lock`, excluding its generated header.
- Compose configuration with `.env.example`: passed.
- `docker build -t lifeos-ai:0.1.0 .`: passed, including packaging the reviewed
  application changes. Existing dependency installation layers were cached.
- Isolated Docker smoke test: passed against PostgreSQL 17 and mock Zulip/DGX.
  Verified migrations/checkpoint setup with the migration role, all three
  application services, API readiness, approved planning with task creation,
  manifest-only content, approval recovery after worker restart, and a runtime
  database role without schema CREATE. The script removes its own fixtures.
- Real FFmpeg test inside the built image: **1 passed**. Generated five mock
  scene images and silent voice tracks, burned captions, and verified a
  55-second 1080×1920 MP4. The test took approximately 176 seconds on this Docker
  host. Test dependencies were installed only in the disposable container.
- Evaluation dataset structure: passed for seven planning and seven content
  scenarios. This does not evaluate real model response quality.

## Review fixes

- Retried worker jobs remain queued and count toward active-run budgets. A run
  receives a terminal failure status and failure notification only when its
  retry budget is exhausted. Previously the first transient error prematurely
  marked the run failed while the job remained scheduled for retry.
- Invalid planning timezones become Pydantic validation errors instead of
  escaping as timezone lookup exceptions.
- Added this document to satisfy the verification link in the README.

## Deployment requirements and limits

There is no configured runtime `.env` in this checkout. Real PostgreSQL, Zulip,
DGX model compatibility/quality, paid image/voice providers, S3, n8n and Airflow
have not been exercised with production credentials. Configure those services
and apply migrations as described in the README before starting production.
With the example `IMAGE_PROVIDER=none` and `TTS_PROVIDER=none`, content completes
as a manifest, transcript and captions; a final video requires both providers
and explicit final review approval. Mock TTS produces silence.

The API uses a shared operator token and an asserted allowlisted Zulip user ID;
keep its loopback binding unless adding a suitable authenticated gateway.
External deliveries and paid provider calls have the retry limitations already
documented in the README.
