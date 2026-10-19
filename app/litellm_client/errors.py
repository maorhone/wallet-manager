class LiteLLMError(Exception):
    """Base class for LiteLLM management API failures."""


class LiteLLMTransientError(LiteLLMError):
    """Connect error, timeout or 5xx: the outbox should retry."""


class LiteLLMPermanentError(LiteLLMError):
    """4xx: retrying the same payload will not help."""

    def __init__(self, status: int, body: object):
        super().__init__(f"LiteLLM returned {status}: {body}")
        self.status = status
        self.body = body
