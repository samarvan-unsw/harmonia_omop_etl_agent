"""Translate provider SDK exceptions into safe user-facing messages."""

from anthropic import (
    APIConnectionError as AnthropicConnectionError,
    APIError as AnthropicError,
    APIStatusError as AnthropicStatusError,
    APITimeoutError as AnthropicTimeoutError,
    AuthenticationError as AnthropicAuthenticationError,
    BadRequestError as AnthropicBadRequestError,
    PermissionDeniedError as AnthropicPermissionDeniedError,
    RateLimitError as AnthropicRateLimitError,
)

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


PROVIDER_API_ERRORS = (OpenAIError, AnthropicError)


def api_error_message(error: Exception) -> str:
    """Return a safe provider error without request or specification data."""
    if isinstance(error, AnthropicAuthenticationError):
        return "Claude authentication failed; check the API key for this run."
    if isinstance(error, AnthropicPermissionDeniedError):
        return "Claude denied access to the selected model."
    if isinstance(error, AnthropicRateLimitError):
        return "Claude API rate limit or quota was reached; retry later."
    if isinstance(error, AnthropicTimeoutError):
        return "Claude API request timed out."
    if isinstance(error, AnthropicConnectionError):
        return "Could not connect to the Claude API."
    if isinstance(error, AnthropicBadRequestError):
        return (
            "Claude rejected the request; "
            "check the model and request configuration."
        )
    if isinstance(error, AnthropicStatusError):
        return f"Claude API returned HTTP {error.status_code}."
    if isinstance(error, AnthropicError):
        return "Claude API request failed."
    if isinstance(error, AuthenticationError):
        return "OpenAI authentication failed; check the supplied API key."
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
