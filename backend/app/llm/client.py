import anthropic

from app.config import settings

# max_retries: the SDK retries rate-limit and server errors with backoff.
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=3)

CREDIT_LOW_MESSAGE = (
    "API credit is low, so Claude calls are paused. "
    "Add credit in the Anthropic Console, then sync again."
)


class CreditTooLow(Exception):
    """The Anthropic account is out of credit. Like a bad API key, every call
    would fail the same way, so runs stop instead of marking emails failed."""

    def __init__(self, message: str = CREDIT_LOW_MESSAGE):
        super().__init__(message)


def is_credit_error(e: Exception) -> bool:
    """True for billing errors: HTTP 402 / billing_error, or the 400
    "Your credit balance is too low ..." the API sends when credit runs out.
    That one is a plain invalid_request_error, so only its message tells it apart."""
    if not isinstance(e, anthropic.APIStatusError):
        return False
    if e.status_code == 402:
        return True
    error = e.body.get("error") if isinstance(e.body, dict) else None
    if isinstance(error, dict) and error.get("type") == "billing_error":
        return True
    return "credit balance" in str(e.message).lower()


def create_message(llm=None, **kwargs):
    """llm.messages.create(**kwargs), with billing errors raised as CreditTooLow."""
    try:
        return (llm or client).messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        if is_credit_error(e):
            raise CreditTooLow() from e
        raise
