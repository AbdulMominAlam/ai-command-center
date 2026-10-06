from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from backend/.env (or real environment variables)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg://localhost/command_center"

    ANTHROPIC_API_KEY: str
    EXTRACT_MODEL: str = "claude-haiku-4-5-20251001"
    AGENT_MODEL: str = "claude-haiku-4-5-20251001"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"

    TOKEN_ENCRYPTION_KEY: str = ""
    SESSION_SECRET: str = ""

    TIMEZONE: str = "Europe/Istanbul"

    # Where the browser goes after Google login (the Vite dev server).
    FRONTEND_URL: str = "http://localhost:5173"

    # Sync now / background sync also run extraction on up to this many new emails.
    EXTRACT_ON_SYNC_LIMIT: int = 50
    # Sync + extraction every 30 minutes inside the API process.
    BACKGROUND_SYNC_ENABLED: bool = True

    # Labeling endpoints for the eval set (/evals/...), localhost only. Turn off anywhere public.
    DEV_ENDPOINTS_ENABLED: bool = True


settings = Settings()
