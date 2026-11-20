# Kai

Python API and Temporal worker. Postgres database `kai` holds workflows. Temporal keeps its own databases on the same server. Chat and the backoffice follow each workflow's **saved draft**. Publish snapshots a version for rule fires and eval history.

Architecture (Mermaid): see [ARCHITECTURE.md](ARCHITECTURE.md).  
Ops / DB GUI / endpoints / Temporal vs Postgres: see [OPS.md](OPS.md) or http://localhost:3000/ops after boot.

## Run

1. `cp .env.example .env`
2. Put your Gemini key in `.env` as `GEMINI_API_KEY=...`. The file stays on this machine. Docker gives it to the API and worker from that file.
3. `docker compose up --build`
4. `docker compose run --rm api python -m kai.seed`
5. Open chat at http://localhost:3000/chat (Guest or Logged in), the backoffice at http://localhost:3000/admin/, Temporal at http://localhost:8081, **DB GUI (Adminer)** at http://localhost:8082, ops hub at http://localhost:3000/ops, and the API docs at http://localhost:3000/docs.
6. Send `Top up my USD with €100 from EUR`. In Temporal UI open `conv:demo:demo`. The child run waits on `offer`.
7. `POST /v1/rules/rule-usd/fire` with header `X-Api-Key: demo-key` starts a rule run that skips the router (uses the last published version).

`docker compose exec postgresql psql -U kai -d kai` shows the draft graph and, after a chat, the transcript. From the host the same database is on port 5433, because 5432 is left free for a local Postgres.

The widget key `demo-key` is the demo tenant's public key. It is not the Gemini key.

Seed loads the seven catalog workflows plus six short demos (`demo-balance`, `demo-alert`, `demo-move`, `demo-login`, `demo-choose`, `demo-list`). To refresh only the demos and their sample runs without wiping the tenant: `docker compose run --rm api python -m kai.install_demos`.
