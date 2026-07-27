from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)


def api_error_message(error: OpenAIError) -> str:
    """Return a safe provider error without request or specification data."""
    if isinstance(error, AuthenticationError):
        return "OpenAI authentication failed; check OPENAI_API_KEY."
    if isinstance(error, PermissionDeniedError):
        return "OpenAI denied access to the configured project or model."
    if isinstance(error, RateLimitError):
        if getattr(error, "code", None) == "insufficient_quota":
            return (
                "OpenAI API quota is unavailable; "
                "check project billing or credits."
            )
        return "OpenAI API rate limit reached; retry later."
    if isinstance(error, APITimeoutError):
        return "OpenAI API request timed out."
    if isinstance(error, APIConnectionError):
        return "Could not connect to the OpenAI API."
    if isinstance(error, BadRequestError):
        return (
            "OpenAI rejected the request; "
            "check the model and request configuration."
        )
    if isinstance(error, APIStatusError):
        return f"OpenAI API returned HTTP {error.status_code}."
    return "OpenAI API request failed."
