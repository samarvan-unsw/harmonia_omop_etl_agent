from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .context import build_context
from .input_guard import enforce_initial_request_limit
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
    pending_review_fields,
    validate_specs,
)


def run_agent(
    omop_table: str,
    specs_dir: Path,
    config: dict,
    max_iterations: int = 6,
) -> dict:
    """Generate one OMOP SQL file through a bounded tool-calling loop."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be greater than zero")

    specs = validate_specs(omop_table, specs_dir)
    pending_reviews = pending_review_fields(specs)
    if pending_reviews:
        raise SpecValidationError(
            "Generation blocked by pending mapping reviews: "
            + ", ".join(pending_reviews)
        )

    context = build_context(omop_table, specs_dir)
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
                return {
                    "status": status,
                    "message": response.text,
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
                    )
                    tool_result = (
                        f"{tool_result}\n{validation.as_tool_message()}"
                    )

                    if validation.valid:
                        promote_file(
                            output_filename,
                            candidate_id=candidate_id,
                        )
                        output_valid = True
                        output_promoted = True
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
                    return {
                        "status": "done",
                        "message": f"Generated and validated {output_filename}.",
                        "iterations": iteration,
                        "output_written": True,
                        "output_valid": True,
                        "usage": {
                            "successful_api_responses": successful_api_responses,
                            **asdict(total_usage),
                        },
                        "transcript": messages,
                    }

        return {
            "status": "max_iterations_reached",
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
