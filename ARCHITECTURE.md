# Kai architecture

Kai is a draft-first money chat bot: the backoffice edits a workflow **draft** (graph + chat contract + stories); chat classifies against loaded drafts; publish snapshots a version for rule fires and eval history.

## System context

```mermaid
flowchart LR
  Customer["Customer / Corner bot"] -->|HTTP + SSE Redis| API["FastAPI :3000"]
  Admin["Backoffice /admin"] --> API
  API --> Postgres[(Postgres kai)]
  API --> Temporal[Temporal kai ns]
  Worker["Python worker"] --> Temporal
  Worker --> Postgres
  Worker --> Redis[(Redis)]
  Worker --> Gemini["Gemini optional"]
  Rules["POST /v1/rules/:id/fire"] --> API
  API --> Temporal
```

## Runtime path — chat turn

```mermaid
sequenceDiagram
  participant C as Customer
  participant A as API
  participant T as Temporal ConversationWorkflow
  participant W as Worker activities
  participant G as Gemini optional

  C->>A: message
  A->>T: signal / start conversation
  T->>W: load_contracts (draft-first, chat-ready)
  T->>W: interpret_turn / classify_intent
  alt Gemini on
    W->>G: classify / extract
  end
  W-->>T: decision start|fill|answer|chat
  Note over T: bands: start / ask / not_this
  alt start + slots filled
    T->>T: start RunWorkflow child
  else ask band
    T-->>C: contract.setup confirmation
  else not_this / chat / greeting
    T-->>C: contract.idle ("When nothing matches")
  else missing slot
    T-->>C: slot.ask
  end
  T->>W: persist Message + RunEvent audit
  W->>Redis: publish conv:id
  Redis-->>C: live reply
```

## Chat contract fields (backoffice Chat tab)

| Field | Admin label | Runtime use |
| --- | --- | --- |
| `setup` | **Setup** | Customer-facing confirmation when confidence is in the **ask** band. Preferred over `lines[chat entry]` for that ask. |
| `idle` | **When nothing matches** | Said on **not_this**, exclusions, greetings with no start, or no workflow key. Try-it shows it for exclusion chips. |
| `description` | Description the classifier reads | Scoring / Gemini intent text (not spoken). |
| `examples` | Examples | High-confidence starts; also feed Try-it chips via `sync_try_prompts`. |
| `exclusions` | Not this | Force low score / chat; appear on Try-it as negatives. |
| `bands` | How sure Kai must be | `start_at` / `ask_at` / `not_this_below` → band decision. |
| `slots` | What Kai fills, or asks | Pre-graph questions when `fill_from` is set. |
| `lines` | What Kai says | Spoken lines keyed by step id. |
| `never` | Kai never | Guardrails: forbidden phrases blocked live on Kai lines; scored as `guardrails` on the run (0 flags). Order rules checked on the event timeline. |
| `try_prompts` | Try it chips | Always = examples + exclusions (`sync_try_prompts`). |

```mermaid
flowchart TD
  Text[Customer text] --> Score[score_intent vs each contract]
  Score --> Band{decide_band}
  Band -->|score >= start_at| Start[start workflow]
  Band -->|ask_at <= score < start_at| Ask[Say setup — confirm intent]
  Band -->|score < not_this_below or exclusion| Idle[Say idle — nothing matches]
  Start --> Slots{missing slot?}
  Slots -->|yes| AskSlot[Say slot.ask]
  Slots -->|no| Graph[Walk graph from chat entry]
```

## Draft-first vs publish

```mermaid
flowchart TB
  subgraph Backoffice
    Edit[Edit draft graph + chat_contract]
    Save[Save chat contract]
    Pub[Publish]
  end
  Edit --> Draft[(Workflow.draft)]
  Save --> Draft
  Pub --> Ver[(WorkflowVersion snapshot)]
  Draft --> ChatLoad[Chat load_contracts — prefers draft when chat-ready]
  Ver --> RuleFire[Rule fire / eval history]
```

- **Chat** loads the draft when the graph is chat-ready; otherwise the published version.
- **Rules** use the published version only.
- Creating a workflow from a template copies draft and sets `built_from`.
- Deleting a workflow removes runs/scores/events for that workflow; orphan cleanup applies where implemented.

## Catalog workflows (seeded)

```mermaid
flowchart LR
  subgraph Money
    TopUp[low-balance-top-up]
    Alert[low-balance-alert]
    Sweep[sweep-between-accounts]
    FX[fx-rate-watch]
    JIT[jit-card-topup rule-only]
  end
  subgraph Onboarding
    Ind[individual-onboarding]
    Biz[business-onboarding]
  end
  subgraph Demos
    D1[demo-balance]
    D2[demo-alert]
    D3[demo-move]
    D4[demo-login]
    D5[demo-choose]
    D6[demo-list]
  end
  Alert -->|offer top-up| TopUp
```

Intent routing (deterministic boosts + Gemini when enabled):

- Alert phrases (`tell me when` / `goes under` / `below N`) → alert, not balance listing or top-up.
- `top up` / `refill` / `add … to` → top-up.
- `Move … from … to …` → sweep.
- Exclusions and `idle` keep mismatches out of the wrong graph.

## Audit trail

Every interpret / classify / step advance persists `RunEvent` rows (start, condition, fetch, action, reply/timeout) and message payloads so Intelligence can show who decided what (deterministic vs Gemini), confidence, band, and step trace.

Finished runs get `RunScore` with chat checks (`intent`, `slots`, `turns`, `lines`, `guardrails`) and outcome checks (`right_end`, `speed`, `finished`, `no_errors`). **Kai never** feeds `guardrails`.

## Draft → live chat

`load_contracts` prefers each workflow’s **saved draft** when it has a chat entry (and a published version exists for the tenant). Saving the chat contract in the backoffice updates the draft immediately — examples, slots, Setup, idle, and Kai never apply to the next chat turn without publishing. Publish still snapshots a version for rule fires and eval history.

## Local stack

| Service | Port |
| --- | --- |
| API + admin static | 3000 |
| Temporal UI | 8081 |
| Postgres (kai) | 5433 → 5432 |
| Redis | 6380 → 6379 |
| Temporal gRPC | 7233 |

Seed: `docker compose run --rm api python -m kai.seed`  
Demo refresh only: `docker compose run --rm api python -m kai.install_demos`
