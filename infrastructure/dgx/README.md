# Remote model serving

Use an existing OpenAI-compatible server on the DGX. Do not open unauthenticated
inference to the public internet. Put both homelab and DGX on WireGuard/Tailscale
(or use an authenticated HTTPS gateway). Set DGX_BASE_URL to its `/v1` endpoint,
DGX_API_KEY to the gateway credential, and DGX_MODEL to the exact served model ID.
HTTP is accepted only with DGX_PRIVATE_NETWORK=true, indicating an explicitly
configured private VPN. This flag does not create or verify your VPN.

Verify `/v1/models` from the LifeOS host with the configured Authorization header.
The served model must support the selected structured output mode. Start with
json_schema; use json_mode for endpoints lacking native JSON schema support.
Even when server-side schema support is limited, Pydantic validates all outputs.
For json_mode, prompts must instruct the model to return JSON.

ModelGateway caps async concurrency, request timeout, retry count and output tokens.
Do not rely on a GPU model for authorization or data validation. No model-generated
code, SQL, arbitrary HTTP, or shell commands are executed. Choose model precision,
context size and inference engine based on your existing DGX server and available
memory. No DGX container is added to the application deployment.
