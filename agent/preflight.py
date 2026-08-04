"""Assess generation readiness, request size, token limits, and estimated cost."""

from dataclasses import dataclass

from .context import build_context_from_specs
from .costing import (
    estimate_serialized_tokens,
    maximum_generation_cost,
)
from .input_guard import (
    initial_request_character_count,
    serialize_initial_request,
)
from .prompts import build_system_prompt, build_user_prompt
from .tools import TOOL_SCHEMAS
from .validation import ValidatedSpecs, pending_review_fields


@dataclass(frozen=True)
class GenerationPreflight:
    """Deterministic generation readiness without creating a provider."""

    context_characters: int
    generation_ready: bool
    initial_request_characters: int
    input_limit_exceeded: bool
    maximum_initial_prompt_characters: int
    estimated_initial_input_tokens: int
    estimated_maximum_input_tokens: int
    estimated_maximum_cost_usd: float
    output_token_ceiling: int
    pending_reviews: tuple[str, ...]


def generation_readiness_blockers(
    preflight: GenerationPreflight,
) -> tuple[str, ...]:
    """Return actionable reasons why a validated mapping cannot generate."""
    blockers: list[str] = []
    if preflight.pending_reviews:
        blockers.append(
            "Pending mapping reviews for target fields: "
            + ", ".join(preflight.pending_reviews)
            + ". Approve each review before generation."
        )
    if preflight.input_limit_exceeded:
        excess = (
            preflight.initial_request_characters
            - preflight.maximum_initial_prompt_characters
        )
        blockers.append(
            f"Initial request is {preflight.initial_request_characters} "
            "characters, exceeding the configured limit of "
            f"{preflight.maximum_initial_prompt_characters} by {excess}. "
            "Reduce the mapping or source-schema context, or adjust the "
            "agent prompt limit."
        )
    return tuple(blockers)


def configured_output_token_ceiling(
    config: dict,
    max_iterations: int,
) -> int:
    """Calculate the worst-case generated-token ceiling for one run."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be greater than zero")

    attempts_per_iteration = config["max_api_retries"] + 1
    return (
        config["max_output_tokens"]
        * max_iterations
        * attempts_per_iteration
    )


def build_generation_preflight(
    omop_table: str,
    specs: ValidatedSpecs,
    config: dict,
    max_iterations: int,
) -> GenerationPreflight:
    """Calculate prompt size, review blockers and token ceiling locally."""
    context = build_context_from_specs(specs)
    output_filename = f"{omop_table}.sql"
    system_prompt = build_system_prompt(omop_table, config)
    user_prompt = build_user_prompt(context, output_filename)
    initial_characters = initial_request_character_count(
        system_prompt,
        user_prompt,
        TOOL_SCHEMAS,
    )
    maximum_characters = config["max_initial_prompt_characters"]
    reviews = pending_review_fields(specs)
    input_limit_exceeded = initial_characters > maximum_characters
    estimated_input_tokens = estimate_serialized_tokens(
        serialize_initial_request(
            system_prompt,
            user_prompt,
            TOOL_SCHEMAS,
        ),
        config["model"],
    )
    output_token_ceiling = configured_output_token_ceiling(
        config,
        max_iterations,
    )
    cost_estimate = maximum_generation_cost(
        config=config,
        initial_input_tokens=estimated_input_tokens,
        max_iterations=max_iterations,
        maximum_output_tokens=output_token_ceiling,
    )

    return GenerationPreflight(
        context_characters=len(context),
        generation_ready=not reviews and not input_limit_exceeded,
        initial_request_characters=initial_characters,
        input_limit_exceeded=input_limit_exceeded,
        maximum_initial_prompt_characters=maximum_characters,
        estimated_initial_input_tokens=estimated_input_tokens,
        estimated_maximum_input_tokens=(
            cost_estimate.maximum_input_tokens
        ),
        estimated_maximum_cost_usd=cost_estimate.maximum_cost_usd,
        output_token_ceiling=output_token_ceiling,
        pending_reviews=reviews,
    )
