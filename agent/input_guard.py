"""Local size guard for the initial model request."""

import json


class InputSizeLimitError(ValueError):
    """Raised before an API call when request material exceeds its limit."""


def serialize_initial_request(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
) -> str:
    """Serialize all locally controlled initial request material."""
    request_material = {
        "instructions": system_prompt,
        "input": [{"role": "user", "content": user_prompt}],
        "tools": tools,
    }
    return json.dumps(
        request_material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def initial_request_character_count(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
) -> int:
    """Measure all locally controlled material in the initial request."""
    return len(
        serialize_initial_request(system_prompt, user_prompt, tools)
    )


def enforce_initial_request_limit(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
    maximum_characters: int,
) -> int:
    """Return request size or stop locally when the configured cap is exceeded."""
    if maximum_characters < 1:
        raise ValueError("maximum_characters must be greater than zero")

    character_count = initial_request_character_count(
        system_prompt,
        user_prompt,
        tools,
    )
    if character_count > maximum_characters:
        raise InputSizeLimitError(
            "Initial API request size "
            f"{character_count} characters exceeds configured limit "
            f"{maximum_characters}"
        )
    return character_count
