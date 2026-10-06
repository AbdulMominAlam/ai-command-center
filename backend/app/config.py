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


settings = Settings()
