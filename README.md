# Kai

Python API and Temporal worker. Postgres database `kai` holds workflows. Temporal keeps its own databases on the same server. The chat page and the backoffice both run the published graph.

## Run

1. `cp .env.example .env`
2. Put your Gemini key in `.env` as `GEMINI_API_KEY=...`. The file stays on this machine. Docker gives it to the worker only.
3. `docker compose up --build`
4. `docker compose run --rm api python -m kai.seed`
5. Open the bot at http://localhost:3000/chat, the backoffice at http://localhost:3000/admin/, Temporal at http://localhost:8081, and the API docs at http://localhost:3000/docs.
6. Send `Top up my USD with €100 from EUR`. In Temporal UI open `conv:demo:demo`. The child run waits on `offer`.
7. `POST /v1/rules/rule-usd/fire` with header `X-Api-Key: demo-key` starts a rule run that skips the router.

`docker compose exec postgresql psql -U kai -d kai` shows the published graph and, after a chat, the transcript. From the host the same database is on port 5433, because 5432 is left free for a local Postgres.

The widget key `demo-key` is the demo tenant's public key. It is not the Gemini key.
