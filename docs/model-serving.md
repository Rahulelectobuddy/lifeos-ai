# Model and media providers

See infrastructure/dgx/README.md for private DGX connectivity. Models are selected
by DGX_MODEL. Credentials and base URLs are environment configuration. The gateway
supports async structured output, streaming, bounded retries, a semaphore and
health checks against /models. Configured input/output pricing produces estimated
cost metrics; zero is the default when cost is unknown, not a claim of free service.

## Images

IMAGE_PROVIDER=none produces no image assets. IMAGE_PROVIDER=mock generates explicitly
marked test illustrations. IMAGE_PROVIDER=openai uses the operator's fixed
OpenAI-compatible image endpoint at IMAGE_BASE_URL/images/generations, with
IMAGE_MODEL/IMAGE_API_KEY. The chosen backend must support base64 JSON output and
1024x1536 images; FFmpeg crops to 1080x1920. Implement another adapter if your backend
needs different size parameters or a different request schema. URL-only responses
are rejected so model-selected locations are never fetched.

## Voiceover

TTS_PROVIDER=none skips audio. TTS_PROVIDER=mock creates SILENT test WAVs.
TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY and an operator-selected
ELEVENLABS_VOICE_ID. ELEVENLABS_MODEL_ID defaults to eleven_multilingual_v2.
Hindi narration is sent verbatim, with language_code=hi, separately per scene.
Provider failures fail the production stage; they never fabricate success.

## Captions/rendering

Captions are UTF-8 SRT, estimated from scene timing in eight-word chunks. For
word-accurate captions, add a forced-alignment SubtitleProvider. FFmpeg probes
real audio duration, adjusts tempo within 0.5x-2x, pads/trims to scene duration,
renders 30fps H.264/AAC portrait clips, concatenates, and burns Hindi subtitles.
Output is validated for resolution and 50-60 seconds. Larger timing mismatch
fails and requires script/TTS revision. `fade` fades through black; `cut` cuts.
All process arguments are constructed by the adapter; no shell is used.

## Research and storage

RESEARCH_PROVIDER=none explicitly labels claims as unverified. RESEARCH_PROVIDER=http
uses a fixed HTTPS RESEARCH_URL with bearer RESEARCH_TOKEN. Input JSON is
`{"queries":["..."]}`; output is `{"summary":"...","sources":["https://..."]}`.
A maximum of three queries is allowed. Research text is untrusted model context.

STORAGE_PROVIDER=local uses ARTIFACT_ROOT. S3 mode additionally requires S3_BUCKET,
optional S3_ENDPOINT_URL, S3_REGION and standard AWS credentials/role discovery.
The application volume remains the working cache shared by all three services;
S3 contains published artifact copies. Authentication is required for local API
downloads; do not expose arbitrary filesystem paths or a public artifact directory.

AirflowClient supports an authenticated configured API prefix (default /api/v2),
explicit allowlisted DAGs, approved=True and stable dag_run_id. It is an integration
client for future batch tools, not wired to a public arbitrary-DAG command. Airflow
versions may need a different configured prefix/authentication gateway.
