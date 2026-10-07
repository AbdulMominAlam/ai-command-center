import os

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from app.config import settings
from app.models import GoogleAccount

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"


def granted_scopes(account: GoogleAccount) -> list[str]:
    """The scopes Google granted this account when it last consented."""
    return (account.scopes or "").split()


def has_scope(account: GoogleAccount, scope: str) -> bool:
    return scope in granted_scopes(account)
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Local development only: oauthlib refuses plain http by default, and Google
# returns scope names in a different form than we asked (e.g. "email" instead
# of ".../userinfo.email"), which oauthlib would otherwise treat as an error.
if settings.GOOGLE_REDIRECT_URI.startswith(("http://localhost", "http://127.0.0.1")):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleReconnectRequired(Exception):
    """The stored refresh token is missing, revoked or expired; sign in with Google again."""


def build_flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    """Creates the OAuth flow. Pass state and code_verifier to restore it in the callback."""
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


def _fernet() -> Fernet:
    return Fernet(settings.TOKEN_ENCRYPTION_KEY)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def get_google_credentials(account: GoogleAccount) -> Credentials:
    """Builds fresh Google credentials for one linked account from its stored refresh token."""
    creds = Credentials(
        token=None,
        refresh_token=decrypt_token(account.encrypted_refresh_token),
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        # Only what this account granted: asking for more on refresh (e.g. Tasks,
        # before you re-consent) makes Google reject the refresh with invalid_scope.
        scopes=granted_scopes(account) or None,
    )
    try:
        # Refresh now so a revoked or expired token fails here with a clear message,
        # instead of somewhere in the middle of a Gmail or Calendar sync.
        creds.refresh(Request())
    except RefreshError as e:
        if "invalid_grant" in str(e):
            raise GoogleReconnectRequired(
                f"Google access for {account.email} was revoked or expired. Reconnect Google."
            ) from e
        raise
    return creds
