# AI engineering and agent development

## Core distinctions

**LangChain vs LangGraph:** LangChain adapts models/prompts/structured outputs and
safe tool definitions; LangGraph executes stateful node/edge workflows and saves
checkpointed state. ModelGateway uses LangChain, and each agent owns its graph.

**Agents vs deterministic workflows:** The model proposes plans, scripts and
scenes; deterministic Python validates capacity, permissions, ownership, approval,
timing and media operations. A model-generated answer is not permission to act.

**Tool calling vs MCP:** A tool call is a model's structured request to an exposed
function. MCP is a protocol for discovering/invoking tools supplied by a server.
This project uses an explicit local allowlist and no MCP server or unconstrained
automatic tool execution. ToolRegistry can expose LangChain StructuredTools but
these graphs choose their own fixed tool stages.

**Short-term state vs persistent memory:** Graph checkpoints store an in-flight
run's state; PostgreSQL projects/tasks/preferences store user facts across runs.
A checkpoint ID is not a global user profile. InMemorySaver is only for tests.

**RAG vs database queries:** RAG retrieves text passages for model context; planning
uses precise, ownership-scoped SQLAlchemy queries. Adding RAG later should not
replace transactional access checks. Research text is untrusted evidence.

**Human approval vs autonomy:** Thinking, listing tasks, and generating proposals
are autonomous. Planning mutations, media generation and final export require
run/stage-specific authorization. Infrastructure maintenance is a separate concern.

**Model serving vs agent orchestration:** The DGX server supplies token inference.
LangGraph, Python services and PostgreSQL supply workflow logic. Airflow is for
approved batch DAGs, never interactive graph execution.

## Component guide

| Component | Problem and rationale | Interactions | Test | Extension |
|---|---|---|---|---|
| Settings | Typed operator configuration; validates secrets, URLs and budgets | Every application service | test_security | Add bounded fields, never hardcoded URLs |
| ModelGateway | Unifies OpenAI-compatible inference, streaming, structured outputs, concurrency and metrics | Agent graph nodes | test_integrations | Implement Gateway protocol for another backend |
| ToolRegistry | Explicit schemas/permissions, safe handlers and execution audit | Deterministic graph tools | test_security | Register one reviewed handler; no arbitrary SQL/shell |
| ApprovalEngine | Durable, owner-scoped human decisions and graph interrupts | Store, graph nodes, API/Zulip | test_workflows | Add a new unique stage and reviewed payload |
| Store | Transactions, leases, ownership, deduplication and business records | API, bot, worker | test_workflows + postgres marker | Add explicit Alembic revision and scoped methods |
| Planning graph | Structured time allocation plus validated approved changes | Gateway, read tool, approvals, n8n outbox | test_planning/test_workflows | Extend proposal fields and deterministic validators |
| Content graph | Explicit production stages; truthful missing-provider mode | Gateway, research, providers, storage, FFmpeg | test_content/test_workflows | Add a bounded stage before/after approval |
| Provider interfaces | Keep external media services replaceable | ProductionService | test_integrations/mock render | Implement ImageProvider/TTSProvider, preserve atomic writes |
| Zulip adapter | Primary authenticated personal interface | Event queue, router, outbox | test_integrations + bot tests | Add parser commands with ownership rules |
| Observability | Diagnose latency, tokens, tools and failures without exposing secrets | Graph/model/tool spans | metrics/log review | Add numeric metrics and safe attributes only |

## Adding an agent

1. Create state.py (JSON-compatible TypedDict), schemas.py (strict Pydantic models),
   prompts.py, graph.py and tools.py under lifeos/agents/<name>.
2. Build fixed graph stages with START/END, never model-selected arbitrary code.
3. Register safe read/write tools. Write handlers must re-check persistent approval
   and ownership, not rely on model text or a user-controlled approved boolean.
4. Use ApprovalEngine.wait with a distinct stage and immutable reviewed payload.
   Code before interrupt can replay: make it idempotent. Put business writes after
   authorization and protect them with transactional deduplication.
5. Wire Runtime.graphs and routing, extend the allowed agent enum and tests.
6. Add deterministic fixtures, adversarial inputs, checkpoint-resume tests and
   an evaluation dataset. Document providers/cost/failure behavior.

Evaluation JSON datasets describe scenario-specific pass criteria. Default tests
are deterministic acceptance checks using mock models. Quality evaluation against
your actual DGX is a separate operator-run activity: do not confuse test fixtures
with measured model quality. See tests/evaluations/README.md.
