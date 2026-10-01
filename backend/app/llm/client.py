import anthropic

from app.config import settings

# max_retries: the SDK retries rate-limit and server errors with backoff.
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=3)
