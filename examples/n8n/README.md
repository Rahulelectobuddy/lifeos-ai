# Existing n8n setup

Create two workflows in your existing n8n instance. Each starts with a POST
Webhook node using Header Auth: header `Authorization`, value `Bearer <N8N_TOKEN>`.

- `lifeos/planning-reminder`: accepts `plan_id`, `user_id`, `proposal`.
  Schedule the approved plan's reminders in your existing scheduling workflow.
- `lifeos/content-completed`: accepts `run_id`, `artifacts`.
  Notify the owner of the reviewed final export; do not publish to YouTube.

Set `N8N_WORKFLOWS` to a JSON mapping of these fixed logical names to webhook paths.
Do not expose arbitrary workflow IDs. Keep webhook transport on HTTPS or your
private trusted network. Use n8n's production webhook URLs, not test URLs.

The caller sends `Idempotency-Key`. Implement a first-stage deduplication check
in your existing PostgreSQL/n8n state before scheduling any action. This header
is not automatically enforced by n8n. Outbox retries are at least once.
Respond with 2xx only after the action has been durably recorded. Add a response
node for synchronous webhooks. Test with the fixtures in `tests/unit/test_integrations.py`.
