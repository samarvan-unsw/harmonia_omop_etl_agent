"""Model-aware token and API cost estimates for generation runs."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from math import ceil

from .providers.base import TokenUsage


MILLION_TOKENS = Decimal("1000000")
USD_PRECISION = Decimal("0.00000001")


@dataclass(frozen=True)
class MaximumCostEstimate:
    """Conservative preflight estimate under the configured run limits."""

    initial_input_tokens: int
    maximum_input_tokens: int
    maximum_output_tokens: int
    maximum_cost_usd: float


def estimate_serialized_tokens(serialized_request: str, model: str) -> int:
    """Estimate tokens conservatively without a runtime network dependency."""
    if not model:
        raise ValueError("model is required for token estimation")
    # JSON, YAML and SQL prompts commonly average three to four UTF-8 bytes
    # per token. Three provides a conservative and deterministic estimate.
    return ceil(len(serialized_request.encode("utf-8")) / 3)


def _rounded_usd(value: Decimal) -> float:
    """Return a stable bounded decimal suitable for an estimate response."""
    return float(value.quantize(USD_PRECISION, rounding=ROUND_HALF_UP))


def _rate(value: float) -> Decimal:
    """Convert a validated configuration float without binary artefacts."""
    return Decimal(str(value))


def maximum_generation_cost(
    *,
    config: dict,
    initial_input_tokens: int,
    max_iterations: int,
    maximum_output_tokens: int,
) -> MaximumCostEstimate:
    """Estimate the maximum standard-tier cost for the bounded run."""
    if initial_input_tokens < 0 or maximum_output_tokens < 0:
        raise ValueError("token estimates cannot be negative")
    if max_iterations < 1:
        raise ValueError("max_iterations must be greater than zero")

    request_attempts = (
        max_iterations * (config["max_api_retries"] + 1)
    )
    per_response_output = config["max_output_tokens"]
    prior_output_context = (
        per_response_output
        * request_attempts
        * (request_attempts - 1)
        // 2
    )
    maximum_input_tokens = (
        initial_input_tokens * request_attempts
        + prior_output_context
    )
    pricing = config["pricing"]["models"][config["model"]]
    maximum_input_rate = max(
        _rate(pricing["input_usd_per_million_tokens"]),
        _rate(
            pricing[
                "cache_write_input_usd_per_million_tokens"
            ]
        ),
    )
    cost = (
        Decimal(maximum_input_tokens) * maximum_input_rate
        + Decimal(maximum_output_tokens)
        * _rate(pricing["output_usd_per_million_tokens"])
    ) / MILLION_TOKENS
    return MaximumCostEstimate(
        initial_input_tokens=initial_input_tokens,
        maximum_input_tokens=maximum_input_tokens,
        maximum_output_tokens=maximum_output_tokens,
        maximum_cost_usd=_rounded_usd(cost),
    )


def estimated_usage_cost_usd(
    usage: TokenUsage,
    config: dict,
) -> float:
    """Estimate actual standard-tier cost from measured provider usage."""
    cached_tokens = usage.cached_input_tokens
    cache_write_tokens = usage.cache_write_input_tokens
    if cached_tokens + cache_write_tokens > usage.input_tokens:
        # Provider detail fields should be subsets of input_tokens. If they
        # are inconsistent, charge all input at the normal rate rather than
        # risk showing an artificially low estimate.
        cached_tokens = 0
        cache_write_tokens = 0
    uncached_tokens = (
        usage.input_tokens - cached_tokens - cache_write_tokens
    )
    pricing = config["pricing"]["models"][config["model"]]
    cost = (
        Decimal(uncached_tokens)
        * _rate(pricing["input_usd_per_million_tokens"])
        + Decimal(cached_tokens)
        * _rate(pricing["cached_input_usd_per_million_tokens"])
        + Decimal(cache_write_tokens)
        * _rate(
            pricing[
                "cache_write_input_usd_per_million_tokens"
            ]
        )
        + Decimal(usage.output_tokens)
        * _rate(pricing["output_usd_per_million_tokens"])
    ) / MILLION_TOKENS
    return _rounded_usd(cost)
