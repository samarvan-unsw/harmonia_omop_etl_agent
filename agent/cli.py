"""Provide the command-line interface for validating and generating one target table."""

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from dotenv import load_dotenv
from openai import (
    OpenAIError,
)
from pydantic import ValidationError

from .contracts import AgentConfig
from .dialects import SUPPORTED_SQL_DIALECTS
from .costing import estimated_usage_cost_usd
from .loop import run_agent
from .preflight import (
    build_generation_preflight,
    configured_output_token_ceiling,
    generation_readiness_blockers,
)
from .provider_errors import api_error_message
from .providers import ProviderConfigurationError
from .providers.base import TokenUsage
from .validation import (
    SpecValidationError,
    pending_review_fields,
    validate_specs,
)
from .yaml_loader import load_yaml

ROOT = Path(__file__).resolve().parent.parent


def _api_error_message(error: OpenAIError) -> str:
    """Backward-compatible wrapper around shared safe error formatting."""
    return api_error_message(error)


def _json_default(obj):
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def _positive_int(value: str) -> int:
    """Parse a command-line integer that must be greater than zero."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _configured_output_token_ceiling(config: dict, max_iterations: int) -> int:
    """Backward-compatible wrapper for the shared preflight calculation."""
    return configured_output_token_ceiling(config, max_iterations)


def _format_usage(usage: dict) -> str:
    """Format measured provider usage without treating subsets as additive."""
    return (
        "Token usage: "
        f"input={usage['input_tokens']} "
        f"(cached={usage['cached_input_tokens']}, "
        f"cache_write={usage['cache_write_input_tokens']}), "
        f"output={usage['output_tokens']} "
        f"(reasoning={usage['reasoning_output_tokens']}), "
        f"total={usage['total_tokens']}, "
        f"responses={usage['successful_api_responses']}"
    )


def main():
    parser = argparse.ArgumentParser(description="Run the OMOP conversion agent for one target table.")
    parser.add_argument("omop_table", help="OMOP table to build, e.g. person")
    parser.add_argument(
        "--max-iterations",
        type=_positive_int,
        default=2,
        help="Maximum API generation attempts; defaults to 2.",
    )
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--generate",
        action="store_true",
        help="Allow the agent to call the configured API and generate SQL.",
    )
    execution_mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Explicitly validate without calling the API; this is the default.",
    )
    execution_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview validated generation settings without calling the API.",
    )
    execution_mode.add_argument(
        "--etl-specification",
        choices=["md", "docx", "pdf"],
        help=(
            "Create deterministic ETL documentation in the selected format "
            "without loading config.yaml or calling an API."
        ),
    )
    parser.add_argument(
        "--dialect",
        choices=SUPPORTED_SQL_DIALECTS,
        help="Override the configured SQL dialect.",
    )
    parser.add_argument(
        "--output-format",
        choices=["sql", "dbt"],
        help="Override the configured output format.",
    )
    parser.add_argument(
        "--source-style",
        choices=["relation", "dbt_ref", "dbt_source"],
        help="Override how source models are referenced.",
    )
    parser.add_argument(
        "--source-name",
        help="dbt source name; required when --source-style=dbt_source.",
    )
    parser.add_argument(
        "--max-run-output-tokens",
        type=_positive_int,
        help=(
            "Required with --generate; generation is refused when its "
            "configured worst-case output-token ceiling exceeds this value."
        ),
    )
    args = parser.parse_args()

    specs_dir = ROOT / "specs"
    if args.etl_specification:
        try:
            specs = validate_specs(args.omop_table, specs_dir)
        except SpecValidationError as exc:
            parser.error(str(exc))
        pending_reviews = pending_review_fields(specs)
        if pending_reviews:
            parser.error(
                "ETL specification blocked by pending mapping reviews: "
                + ", ".join(pending_reviews)
            )

        # Keep document libraries off ordinary validation and generation
        # startup paths.
        from .etl_specification import build_etl_specification

        try:
            artifact = build_etl_specification(
                specs,
                args.etl_specification,
            )
        except ValueError as exc:
            parser.error(str(exc))
        output_dir = ROOT / "output" / "etl_specifications"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / artifact.file_name
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=output_dir,
                prefix=f".{artifact.file_name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(artifact.content)
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        print(
            f"ETL specification created: {output_path}. "
            "No API call was made."
        )
        return

    load_dotenv(ROOT / ".env")
    try:
        raw_config = load_yaml(
            (ROOT / "config.yaml").read_text(encoding="utf-8")
        )
        if not isinstance(raw_config, dict):
            raise ValueError("config.yaml must contain a mapping")

        # Apply per-run choices before validating the final configuration.
        if args.dialect:
            raw_config["output"]["dialect"] = args.dialect
        if args.output_format:
            raw_config["output"]["format"] = args.output_format
        if args.source_style:
            raw_config["source"]["reference_style"] = args.source_style
        if args.source_name:
            raw_config["source"]["source_name"] = args.source_name

        config_document = AgentConfig.model_validate(raw_config)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        parser.error(f"Invalid config.yaml: {exc}")

    config = config_document.model_dump()
    output_token_ceiling = _configured_output_token_ceiling(
        config,
        args.max_iterations,
    )

    # Require an explicit per-run ceiling before any API-backed work begins.
    if args.generate and args.max_run_output_tokens is None:
        parser.error("--generate requires --max-run-output-tokens")
    if (
        args.generate
        and output_token_ceiling > args.max_run_output_tokens
    ):
        parser.error(
            "Configured generation can use up to "
            f"{output_token_ceiling} output tokens, exceeding "
            f"--max-run-output-tokens={args.max_run_output_tokens}. "
            "Reduce --max-iterations or max_output_tokens."
        )

    try:
        specs = validate_specs(args.omop_table, specs_dir)
    except SpecValidationError as exc:
        parser.error(str(exc))
    preflight = build_generation_preflight(
        args.omop_table,
        specs,
        config,
        args.max_iterations,
    )
    # Keep this seam patchable for CLI integrations and review-gate tests.
    pending_reviews = pending_review_fields(specs)
    output_filename = f"{args.omop_table}.sql"
    initial_request_characters = preflight.initial_request_characters
    maximum_prompt_characters = (
        preflight.maximum_initial_prompt_characters
    )
    input_limit_exceeded = preflight.input_limit_exceeded

    if args.dry_run:
        output_path = ROOT / "output" / output_filename
        print(f"Dry run passed for '{args.omop_table}'. No API call was made.")
        print(f"Provider: {config['provider']}")
        print(f"Model: {config['model']}")
        print(f"Maximum output tokens per request: {config['max_output_tokens']}")
        print(f"Automatic API retries: {config['max_api_retries']}")
        print(f"Maximum generation attempts: {args.max_iterations}")
        print(f"Worst-case run output-token ceiling: {output_token_ceiling}")
        print(f"SQL dialect: {config['output']['dialect']}")
        print(f"Output format: {config['output']['format']}")
        print(f"Source reference style: {config['source']['reference_style']}")
        print(f"Target file: {output_path}")
        if config["output"]["format"] == "dbt":
            print(
                "Companion file: "
                f"{ROOT / 'output' / f'{args.omop_table}.yml'}"
            )
        else:
            print(f"DDL directory: {ROOT / 'output' / 'ddl'}")
        print(
            f"Context size: {preflight.context_characters} characters"
        )
        print(
            "Initial request size: "
            f"{initial_request_characters} / "
            f"{maximum_prompt_characters} characters"
        )
        print(
            "Estimated initial input: "
            f"{preflight.estimated_initial_input_tokens} tokens"
        )
        print(
            "Estimated maximum input: "
            f"{preflight.estimated_maximum_input_tokens} tokens"
        )
        print(
            "Estimated maximum API cost: "
            f"${preflight.estimated_maximum_cost_usd:.6f} "
            f"{config['pricing']['currency']} "
            f"(pricing verified {config['pricing']['verified_on']})"
        )
        readiness_blockers = generation_readiness_blockers(preflight)
        if readiness_blockers:
            print(
                "Generation readiness: blocked by "
                + "; ".join(readiness_blockers)
            )
        else:
            print("Generation readiness: ready")
        return

    if not args.generate:
        message = (
            f"Validation passed for '{args.omop_table}': "
            f"{len(specs.source_models)} source model(s), "
            f"{len(specs.target_schema.fields)} target field(s), "
            f"{preflight.context_characters} context characters. "
            "No API call was made."
        )
        if pending_reviews:
            message += " Pending reviews: " + ", ".join(pending_reviews) + "."
        print(message)
        return

    if pending_reviews:
        parser.error(
            "Generation blocked by pending mapping reviews: "
            + ", ".join(pending_reviews)
        )
    if input_limit_exceeded:
        parser.error(
            "Initial API request size "
            f"{initial_request_characters} characters exceeds configured limit "
            f"{maximum_prompt_characters}"
        )

    print(
        f"Running agent for '{args.omop_table}' using "
        f"{config['provider']} ({config['model']}); "
        f"output-token ceiling: {output_token_ceiling}."
    )
    try:
        result = run_agent(
            args.omop_table,
            specs_dir,
            config,
            max_iterations=args.max_iterations,
        )
    except ProviderConfigurationError as exc:
        parser.exit(
            1,
            f"Configuration error: {exc}\n",
        )
    except OpenAIError as exc:
        parser.exit(1, f"API error: {_api_error_message(exc)}\n")

    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = logs_dir / f"{args.omop_table}_{timestamp}.json"
    log_content = json.dumps(result, indent=2, default=_json_default)

    # Create the transcript atomically with owner-only read/write permissions.
    descriptor = os.open(
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as log_file:
        log_file.write(log_content)

    print(f"Status: {result['status']} ({result['iterations']} iterations)")
    print(_format_usage(result["usage"]))
    usage = result["usage"]
    measured_usage = TokenUsage(
        input_tokens=usage["input_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        cache_write_input_tokens=usage["cache_write_input_tokens"],
        output_tokens=usage["output_tokens"],
        reasoning_output_tokens=usage["reasoning_output_tokens"],
        total_tokens=usage["total_tokens"],
    )
    print(
        "Estimated API cost: "
        f"${estimated_usage_cost_usd(measured_usage, config):.6f} "
        f"{config['pricing']['currency']} "
        f"(pricing verified {config['pricing']['verified_on']})"
    )
    print(f"Log: {log_path}")
    if result["status"] != "done":
        sys.exit(1)

if __name__ == "__main__":
    main()
