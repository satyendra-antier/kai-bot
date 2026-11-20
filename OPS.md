# Kai ops — verify everything

## Quick links

| Surface | URL | What you verify |
| --- | --- | --- |
| Ops hub | http://localhost:3000/ops | Links + health + how Temporal/DB fit |
| Ops JSON | `GET /v1/ops` (`X-Api-Key: demo-key`) | Endpoints, counts, workflow list |
| Chat | http://localhost:3000/chat | Live customer chat (Guest / Logged in); follows backoffice drafts |
| Backoffice | http://localhost:3000/admin/ | Drafts, Try-it, Intelligence |
| API docs | http://localhost:3000/docs | Every FastAPI route |
| Health | http://localhost:3000/health | api / postgres / redis / temporal / worker |
| Temporal UI | http://localhost:8081 | Live `ConversationWorkflow` + `RunWorkflow` (namespace **kai**) |
| DB GUI (Adminer) | http://localhost:8082 | Browse `kai` tables |

Bring the stack up:

```bash
docker compose up --build -d
docker compose run --rm api python -m kai.seed
```

## Database GUI (Adminer)

1. Open http://localhost:8082  
2. System: **PostgreSQL**  
3. Server: **`postgresql`** (from Adminer’s container network)  
4. Login:

| DB | User | Password | Database |
| --- | --- | --- | --- |
| Kai app | `kai` | `kai` | `kai` |
| Temporal | `temporal` | `temporal` | list Temporal DBs |

Host access without Adminer: `localhost:5433`.

### Tables that matter (`kai`)

| Table | Contents |
| --- | --- |
| `workflows` | Catalog + demos; `draft` JSONB is what chat loads |
| `workflow_versions` | Published snapshots (rules / eval) |
| `runs` | One graph walk; `temporal_id`, `trace`, `slots`, `messages` |
| `run_events` | Step audit: start / condition / fetch / action / reply |
| `run_scores` | Chat + outcome checks (incl. **guardrails / Kai never**) |
| `messages` | Chat transcript; user rows carry `payload.ai_steps` |
| `conversations` | Maps chat id → Temporal `conv:…` id |
| `customer_rules` | Live rules fired into `RunWorkflow` |

## Temporal — are “workflows” there?

**Two different meanings:**

1. **Product / catalog workflows** (`low-balance-top-up`, `low-balance-alert`, …) live in **Postgres** `workflows`. They are graphs + chat contracts, not Temporal workflow types.
2. **Temporal workflow types** (worker-registered):
   - `ConversationWorkflow` — chat router (`conv:demo:<conversation_id>`)
   - `RunWorkflow` — executes one graph (`run:demo:<uuid>`)

In Temporal UI (namespace **kai**, task queue **kai-main**):

- Send a chat message → you should see `ConversationWorkflow` running / completed.
- When slots are filled and a graph starts → a **child** `RunWorkflow` appears.
- Rule fire → `RunWorkflow` only (no conversation parent).

Runs **execute** in Temporal; the **durable audit** (events, scores, AI steps) is written to Postgres by worker activities (`persist_run`, `record_event`, `append_message`).

## API endpoint catalog

Auth header unless noted: `X-Api-Key: demo-key`

| Method | Path | Use |
| --- | --- | --- |
| GET | `/health` | Service checks |
| GET | `/ops` | Ops HTML |
| GET | `/v1/ops` | Ops JSON |
| GET | `/docs` | OpenAPI |
| GET | `/chat` | Widget |
| GET | `/admin/` | Backoffice |
| GET/POST/PUT/DELETE | `/v1/workflows…` | Catalog CRUD, draft, publish, try, run |
| POST | `/v1/conversations/{id}/messages` | Chat → Temporal |
| GET | `/v1/conversations/{id}` | Messages **with `ai_steps`** + live Temporal query |
| WS | `/v1/conversations/{id}/ws` | Live replies |
| POST | `/v1/rules/{id}/fire` | Rule → `RunWorkflow` |
| GET | `/v1/runs` | Persisted runs |
| GET | `/v1/runs/{id}` | Events + flow + turns/ai_steps + scores |
| GET | `/v1/intelligence/runs` | Intelligence board |
| GET | `/v1/intelligence/rules` | Rules board |

## Where AI steps show up

| Place | What you see |
| --- | --- |
| Intelligence run drawer | Chat audit (redact → local → Gemini → route → decision) + graph hops + score bars |
| `GET /v1/conversations/{id}` | Per-user-message `ai_steps`, `confidence` |
| `GET /v1/runs/{id}` | `turns[].ai_steps`, `events[]`, `flow[]`, `score.checks` |
| LangSmith (optional) | LangGraph turn graph + Gemini `_json` spans |

### Optional LangSmith

In `.env`:

```env
LANGSMITH_API_KEY=lsv2_…
LANGSMITH_PROJECT=kai-bot
LANGCHAIN_TRACING_V2=true
```

Restart `api` + `worker`. Then open https://smith.langchain.com — project **kai-bot**. Traces cover the LangGraph conversation turn and Gemini JSON calls when the key is set.

Without LangSmith, AI steps still persist in Postgres message payloads and Intelligence (deterministic trail always; Gemini step marked skipped if no key).

## Verify checklist

1. `curl -s http://localhost:3000/health` → all `ok`  
2. Adminer → `kai.workflows` has catalog + demos  
3. Chat → Temporal UI shows `conv:demo:…`  
4. Complete a top-up → child `run:demo:…` + rows in `runs` / `run_events` / `run_scores`  
5. Intelligence → open run → chat audit + scores including **guardrails**  
6. Save a draft example in backoffice → next chat turn uses it (no publish required)  
