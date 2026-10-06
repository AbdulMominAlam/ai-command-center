# AI Personal Command Center

Personal dashboard that syncs Gmail, Google Calendar and SUCourse (Moodle iCal) into Postgres, uses Claude to extract deadlines and tasks from emails, and has a chat agent that answers questions like "What do I need to finish before Friday?"

## Stack
- Backend: FastAPI, Python 3.12, managed with uv (run everything with `uv run ...` from `backend/`)
- Database: PostgreSQL 17 + pgvector (Homebrew), database `command_center`, migrations with Alembic
- LLM: Anthropic API via the `anthropic` SDK; models set in `backend/.env` (EXTRACT_MODEL, AGENT_MODEL)
- Embeddings: nomic-embed-text via Ollama (768 dims)
- Frontend (later): React + Vite + TypeScript
- Machine: M1 MacBook Air, 8 GB RAM

## Current status
- Done: config, SQLAlchemy models (users, oauth_tokens, sync_state, items, tasks, llm_usage), initial migration, /health and /health/llm endpoints
- Done (Milestone 2): Google OAuth in `app/auth/` (Gmail + Calendar readonly scopes, Testing mode, PKCE, session cookie), refresh token encrypted with Fernet in oauth_tokens, /me endpoint, `get_google_credentials(db, user)` helper
- Done (Milestone 3): Gmail sync in `app/sync/gmail.py` (first run: last 30 days minus promotions/social; later runs: history.list from the saved historyId, full-run fallback on 404), upsert into items, POST /sync/gmail. The Cloud project's Gmail quota is ~550 units/user/minute, so calls are throttled (`UNITS_PER_MINUTE`) and a first run takes ~4 min
- Done (Milestone 4): extraction in `app/llm/extract.py` (one forced save_extraction tool call per email, Pydantic-validated, tasks saved with created_by="extraction", one-sentence summary in items.summary, usage logged to llm_usage), POST /extract/run?limit=10. Sensitive-email filter: subjects/senders matching `SENSITIVE_PATTERNS` (OTPs, passwords, bank alerts) are marked "Skipped: sensitive email" and never sent to Claude; the prompt also forbids codes, passwords, account numbers and amounts in titles/summaries. Tests in `backend/tests/` (`uv run pytest`). Full inbox run: 406 emails, 0 failures, 16 tasks, 45 skipped as sensitive, ~$1.30
- Done (Milestone 5): Google Calendar sync in `app/sync/calendar.py` (primary calendar, next 60 days, singleEvents, all-day events at Europe/Istanbul midnight, events that vanish from the window are deleted, past ones kept) and SUCourse sync in `app/sync/sucourse.py` (Moodle .ics export URL saved in sync_state via POST /sync/sucourse/url and never echoed or logged; each VEVENT becomes an assignment item plus a task with created_by="sucourse", priority high if due within 3 days). POST /sync/calendar, /sync/sucourse, /sync/all. No LLM calls. Tests in `tests/test_sync.py`
- Done (Milestone 6): chat agent in `app/llm/agent.py`, endpoints in `app/llm/agent_routes.py`. POST /chat runs a manual tool-use loop (max 5 model calls) with AGENT_MODEL. Read tools list_tasks, list_events, search_items run SQL and never return email bodies, sensitive or noise emails. create_task is saved to pending_actions and only runs on POST /actions/{id}/confirm (or /cancel). Each call is logged to llm_usage as purpose="agent". Tests in `tests/test_agent.py` use a fake Anthropic client
- Cost estimates come from the price table in `app/llm/pricing.py` (keyed by model name, dated ids map to their alias). A model missing from the table gives estimated_cost_usd = null, so add a row when changing EXTRACT_MODEL or AGENT_MODEL
- Next: frontend

## Rules
- Never commit or print `backend/.env`; secrets only come from settings in `app/config.py`
- Change the schema only through Alembic migrations
- Prefer small, targeted changes over rewrites
- Explain each change in plain language so I can understand and present it in interviews
- Ask before pushing to GitHub