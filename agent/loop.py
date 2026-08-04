"""Orchestrate the bounded generate, validate, revise, and promote workflow."""

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from . import tools as tool_module
from .context import build_context_from_specs
from .input_guard import enforce_initial_request_limit
from .output_artifacts import build_output_artifacts, write_local_artifacts
from .prompts import build_system_prompt, build_user_prompt
from .providers import load_provider
from .providers.base import TokenUsage
from .sql_validation import validate_sql
from .tools import (
    TOOL_SCHEMAS,
    discard_candidate,
    dispatch,
    promote_file,
    read_file,
)
from .validation import (
    SpecValidationError,
    ValidatedSpecs,
    pending_review_fields,
    validate_specs,
)


def run_agent(
    omop_table: str,
    specs_dir: Path,
    config: dict,
    max_iterations: int = 6,
) -> dict:
    """Generate and promote one OMOP SQL file from local specifications."""
    specs = validate_specs(omop_table, specs_dir)
    return run_agent_with_specs(
        omop_table=omop_table,
        specs=specs,
        config=config,
        max_iterations=max_iterations,
        promote_output=True,
    )


def run_agent_with_specs(
    omop_table: str,
    specs: ValidatedSpecs,
    config: dict,
    max_iterations: int = 6,
    promote_output: bool = False,
) -> dict:
    """Generate SQL from validated specs, optionally promoting it locally."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be greater than zero")

    pending_reviews = pending_review_fields(specs)
    if pending_reviews:
        raise SpecValidationError(
            "Generation blocked by pending mapping reviews: "
            + ", ".join(pending_reviews)
        )

    context = build_context_from_specs(specs)
    system_prompt = build_system_prompt(omop_table, config)
    output_filename = f"{omop_table}.sql"
    user_prompt = build_user_prompt(context, output_filename)

    # Reject unexpectedly large request material before creating the API client.
    enforce_initial_request_limit(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=TOOL_SCHEMAS,
        maximum_characters=config["max_initial_prompt_characters"],
    )
    provider = load_provider(config)

    candidate_id = uuid4().hex
    output_written = False
    output_valid = False
    output_promoted = False
    total_usage = TokenUsage()
    successful_api_responses = 0
    expected_fields = [field.name for field in specs.target_schema.fields]
    last_validation_errors: tuple[str, ...] = ()

    messages = [
        {
            "role": "user",
            "content": user_prompt,
        }
    ]

    try:
        for iteration in range(1, max_iterations + 1):
            response = provider.complete(system_prompt, messages, TOOL_SCHEMAS)
            total_usage = total_usage + response.usage
            successful_api_responses += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": response.tool_calls,
                    "usage": asdict(response.usage),
                }
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                status = "invalid_output" if output_written else "no_output_written"
                diagnostics = list(last_validation_errors)
                if not diagnostics:
                    diagnostics.append(
                        "Model did not write the required SQL file."
                    )
                return {
                    "status": status,
                    "message": response.text,
                    "diagnostics": diagnostics,
                    "iterations": iteration,
                    "output_written": output_written,
                    "output_valid": output_valid,
                    "usage": {
                        "successful_api_responses": successful_api_responses,
                        **asdict(total_usage),
                    },
                    "transcript": messages,
                }

            for tool_call in response.tool_calls:
                if (
                    tool_call.name == "write_file"
                    and tool_call.arguments.get("path") != output_filename
                ):
                    tool_result = (
                        f"ERROR: write_file path must be exactly {output_filename}"
                    )
                else:
                    tool_result = dispatch(tool_call, candidate_id=candidate_id)

                if (
                    tool_call.name == "write_file"
                    and tool_call.arguments.get("path") == output_filename
                    and tool_result.startswith("Wrote ")
                ):
                    output_written = True
                    validation = validate_sql(
                        sql=read_file(
                            output_filename,
                            candidate_id=candidate_id,
                        ),
                        dialect=config["output"]["dialect"],
                        expected_fields=expected_fields,
                        output_format=config["output"]["format"],
                        field_mappings=specs.mapping.fields,
                        target_fields=specs.target_schema.fields,
                        source_models=specs.mapping.source_models,
                        declared_joins=specs.mapping.joins,
                        union_all_models=specs.mapping.union_all,
                    )
                    tool_result = (
                        f"{tool_result}\n{validation.as_tool_message()}"
                    )
                    last_validation_errors = validation.errors

                    if validation.valid:
                        generated_sql = read_file(
                            output_filename,
                            candidate_id=candidate_id,
                        )
                        artifacts = build_output_artifacts(
                            generated_sql=generated_sql,
                            target_schema=specs.target_schema,
                            output_format=config["output"]["format"],
                            dialect=config["output"]["dialect"],
                        )
                        if promote_output:
                            write_local_artifacts(
                                artifacts,
                                output_dir=tool_module.OUTPUT_DIR,
                            )
                            promote_file(
                                output_filename,
                                candidate_id=candidate_id,
                            )
                            output_promoted = True
                        output_valid = True
                        if promote_output:
                            tool_result = (
                                f"{tool_result}\nPromoted validated SQL to "
                                f"{output_filename}."
                            )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }
                )

                # Local validation is the terminal success condition. Avoid
                # another API request solely to obtain an assistant summary.
                if output_valid:
                    result = {
                        "status": "done",
                        "message": f"Generated and validated {output_filename}.",
                        "iterations": iteration,
                        "output_written": True,
                        "output_valid": True,
                        "output_artifacts": [
                            artifact.as_dict() for artifact in artifacts
                        ],
                        "diagnostics": [],
                        "usage": {
                            "successful_api_responses": successful_api_responses,
                            **asdict(total_usage),
                        },
                        "transcript": messages,
                    }
                    if not promote_output:
                        result["output_sql"] = generated_sql
                    return result

        return {
            "status": "max_iterations_reached",
            "diagnostics": (
                list(last_validation_errors)
                or ["Maximum attempts reached without valid SQL."]
            ),
            "iterations": max_iterations,
            "output_written": output_written,
            "output_valid": output_valid,
            "usage": {
                "successful_api_responses": successful_api_responses,
                **asdict(total_usage),
            },
            "transcript": messages,
        }
    finally:
        # Failed or interrupted runs must not leave candidate SQL behind.
        if not output_promoted:
            discard_candidate(output_filename, candidate_id=candidate_id)
