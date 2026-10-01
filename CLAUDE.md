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
- Next: Gmail sync

## Rules
- Never commit or print `backend/.env`; secrets only come from settings in `app/config.py`
- Change the schema only through Alembic migrations
- Prefer small, targeted changes over rewrites
- Explain each change in plain language so I can understand and present it in interviews
- Ask before pushing to GitHub