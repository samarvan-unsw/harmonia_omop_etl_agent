import hmac
import os
from datetime import date
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Literal

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import OpenAIError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .contracts import AgentConfig, TargetSchemaDocument
from .costing import estimated_usage_cost_usd
from .loop import run_agent_with_specs
from .preflight import (
    build_generation_preflight,
    generation_readiness_blockers,
)
from .provider_errors import api_error_message
from .validation import (
    SpecValidationError,
    ValidatedSpecs,
    pending_review_fields,
    validate_spec_contents,
)

ROOT = Path(__file__).resolve().parent.parent
TARGET_SCHEMA_DIR = ROOT / "specs" / "target_schema"
CONFIG_PATH = ROOT / "config.yaml"
MAXIMUM_DOCUMENT_BYTES = 750 * 1024
MAXIMUM_SPECIFICATION_BYTES = 2 * 1024 * 1024
MAXIMUM_REQUEST_BYTES = 4 * 1024 * 1024
MINIMUM_API_TOKEN_LENGTH = 32
MAXIMUM_API_RUN_OUTPUT_TOKENS = 20_000
MINIMUM_OUTPUT_TOKENS_PER_REQUEST = 100
MINIMUM_INITIAL_PROMPT_CHARACTERS = 1_000
GENERATION_SEMAPHORE = BoundedSemaphore(value=1)

OmopTable = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=63,
    ),
]
YamlFileName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.yml$",
        max_length=255,
    ),
]


class StrictApiModel(BaseModel):
    """Reject unexpected API properties."""

    model_config = ConfigDict(extra="forbid")


class YamlSpecification(StrictApiModel):
    """One user-maintained YAML specification."""

    file_name: YamlFileName
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def enforce_document_size(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAXIMUM_DOCUMENT_BYTES:
            raise ValueError(
                f"YAML content must not exceed {MAXIMUM_DOCUMENT_BYTES} bytes"
            )
        return value


class ValidationRequest(StrictApiModel):
    """Specifications required to validate one OMOP target table."""

    omop_table: OmopTable
    mapping: YamlSpecification
    source_schemas: list[YamlSpecification] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_request_files(self) -> "ValidationRequest":
        expected_mapping = f"{self.omop_table}.yml"
        if self.mapping.file_name != expected_mapping:
            raise ValueError(
                f"mapping filename must be '{expected_mapping}'"
            )

        file_names = [
            specification.file_name
            for specification in self.source_schemas
        ]
        if len(file_names) != len(set(file_names)):
            raise ValueError("source schema filenames must be unique")

        total_bytes = len(self.mapping.content.encode("utf-8")) + sum(
            len(specification.content.encode("utf-8"))
            for specification in self.source_schemas
        )
        if total_bytes > MAXIMUM_SPECIFICATION_BYTES:
            raise ValueError(
                "combined specification content exceeds the request limit"
            )
        return self


class ValidationResponse(StrictApiModel):
    """Stable response returned by the validation API."""

    valid: bool
    generation_ready: bool
    omop_table: str
    errors: list[str] = Field(default_factory=list)
    source_models: list[str] = Field(default_factory=list)
    target_field_count: int | None = None
    pending_reviews: list[str] = Field(default_factory=list)


class TargetSchemaSummaryResponse(StrictApiModel):
    """Read-only summary of one agent-owned OMOP target table."""

    target_table: str
    display_order: int
    cdm_schema: Literal["CDM", "VOCAB", "RESULTS"]
    cdm_version: str
    required: bool
    description: str
    field_count: int


class TargetSchemaCatalogResponse(StrictApiModel):
    """Read-only index of the complete agent-owned OMOP catalog."""

    cdm_version: str
    table_count: int
    field_count: int
    tables: list[TargetSchemaSummaryResponse]


class TargetForeignKeyResponse(StrictApiModel):
    """Foreign-key information suitable for tabular UI display."""

    table: str
    field: str | None = None
    domain: str | None = None
    class_name: str | None = None


class TargetFieldResponse(StrictApiModel):
    """One field in a read-only OMOP target-schema response."""

    name: str
    data_type: str
    required: bool
    primary_key: bool
    foreign_key: TargetForeignKeyResponse | None = None
    description: str
    etl_convention: str


class TargetSchemaResponse(StrictApiModel):
    """Complete validated target metadata without exposing YAML."""

    cdm_version: str
    display_order: int
    target_table: str
    cdm_schema: Literal["CDM", "VOCAB", "RESULTS"]
    required: bool
    concept_prefix: str | None = None
    measure_person_completeness: bool
    measure_person_completeness_threshold: float | None = None
    description: str
    user_guidance: str
    etl_convention: str
    fields: list[TargetFieldResponse]


class ProjectGenerationSettings(StrictApiModel):
    """Safe project choices layered over agent-owned configuration."""

    sql_dialect: Literal["snowflake", "postgres", "athena", "bigquery"]
    output_format: Literal["sql", "dbt"]
    source_reference_style: Literal[
        "relation",
        "dbt_ref",
        "dbt_source",
    ]
    source_name: Annotated[
        str,
        StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
    ] | None = None
    model: Annotated[
        str,
        StringConstraints(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
            max_length=100,
        ),
    ] | None = None
    maximum_output_tokens_per_request: int | None = Field(
        default=None,
        ge=MINIMUM_OUTPUT_TOKENS_PER_REQUEST,
        le=MAXIMUM_API_RUN_OUTPUT_TOKENS,
    )
    maximum_initial_prompt_characters: int | None = Field(
        default=None,
        ge=MINIMUM_INITIAL_PROMPT_CHARACTERS,
        le=1_000_000,
    )
    automatic_api_retries: int | None = Field(
        default=None,
        ge=0,
        le=2,
    )

    @model_validator(mode="after")
    def validate_compatibility(self) -> "ProjectGenerationSettings":
        if (
            self.output_format == "sql"
            and self.source_reference_style != "relation"
        ):
            raise ValueError(
                "plain SQL output requires relation source references"
            )
        if (
            self.source_reference_style == "dbt_source"
            and self.source_name is None
        ):
            raise ValueError(
                "dbt_source references require a source name"
            )
        if (
            self.source_reference_style != "dbt_source"
            and self.source_name is not None
        ):
            raise ValueError(
                "source name is only allowed for dbt_source references"
            )
        return self


class GenerationOptionsResponse(StrictApiModel):
    """Agent-owned allowlists and hard bounds for project settings."""

    provider: str
    allowed_models: list[str]
    minimum_output_tokens_per_request: int
    maximum_output_tokens_per_request: int
    minimum_initial_prompt_characters: int
    maximum_initial_prompt_characters: int
    maximum_generation_attempts: int
    maximum_api_retries: int
    maximum_run_output_tokens: int


class PreflightRequest(ValidationRequest):
    """Validated specifications plus bounded generation attempts."""

    max_iterations: int = Field(default=2, ge=1, le=2)
    generation_settings: ProjectGenerationSettings | None = None


class PreflightResponse(StrictApiModel):
    """Deterministic generation settings and readiness."""

    valid: bool
    generation_ready: bool
    omop_table: str
    errors: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    pending_reviews: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    sql_dialect: str | None = None
    output_format: str | None = None
    source_reference_style: str | None = None
    maximum_output_tokens_per_request: int | None = None
    automatic_api_retries: int | None = None
    maximum_generation_attempts: int | None = None
    worst_case_output_tokens: int | None = None
    context_characters: int | None = None
    initial_request_characters: int | None = None
    maximum_initial_prompt_characters: int | None = None
    estimated_initial_input_tokens: int | None = None
    estimated_maximum_input_tokens: int | None = None
    estimated_maximum_cost_usd: float | None = None
    cost_currency: str | None = None
    pricing_verified_on: date | None = None


class GenerationRequest(PreflightRequest):
    """Specifications plus an explicit current cost-ceiling confirmation."""

    confirmed_output_token_ceiling: int = Field(gt=0)


class GenerationUsage(StrictApiModel):
    """Measured provider usage returned without transcript content."""

    successful_api_responses: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0


class GenerationResponse(StrictApiModel):
    """Bounded generation result for persistence by the calling UI."""

    status: str
    completed: bool
    omop_table: str
    errors: list[str] = Field(default_factory=list)
    output_sql: str | None = None
    iterations: int = 0
    output_token_ceiling: int | None = None
    model: str | None = None
    estimated_maximum_cost_usd: float | None = None
    estimated_actual_cost_usd: float | None = None
    cost_currency: str | None = None
    pricing_verified_on: date | None = None
    usage: GenerationUsage = Field(default_factory=GenerationUsage)


app = FastAPI(
    title="CardiacAI OMOP Agent API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)


@app.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    """Reject declared oversized bodies before JSON parsing."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAXIMUM_REQUEST_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request body is too large."},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header."},
            )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    error: RequestValidationError,
):
    """Return useful request errors without echoing submitted YAML."""
    errors = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        errors.append(
            {
                "location": location,
                "message": item["msg"],
            }
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "valid": False,
            "errors": errors,
        },
    )


def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authenticate private server-to-server API calls."""
    configured_token = os.getenv("AGENT_API_TOKEN")
    if (
        not configured_token
        or len(configured_token) < MINIMUM_API_TOKEN_LENGTH
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    scheme, separator, supplied_token = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied_token, configured_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
def health() -> dict[str, str]:
    """Expose a non-sensitive liveness endpoint."""
    return {"service": "cardiac-ai-omop-agent", "status": "ok"}


@app.get(
    "/v1/generation-options",
    response_model=GenerationOptionsResponse,
    dependencies=[Depends(require_api_token)],
)
def generation_options() -> GenerationOptionsResponse:
    """Expose safe project choices without leaking secrets."""
    try:
        config = _load_agent_config()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    limits = config["project_limits"]
    return GenerationOptionsResponse(
        provider=config["provider"],
        allowed_models=limits["allowed_models"],
        minimum_output_tokens_per_request=(
            MINIMUM_OUTPUT_TOKENS_PER_REQUEST
        ),
        maximum_output_tokens_per_request=(
            limits["maximum_output_tokens_per_request"]
        ),
        minimum_initial_prompt_characters=(
            MINIMUM_INITIAL_PROMPT_CHARACTERS
        ),
        maximum_initial_prompt_characters=(
            limits["maximum_initial_prompt_characters"]
        ),
        maximum_generation_attempts=(
            limits["maximum_generation_attempts"]
        ),
        maximum_api_retries=limits["maximum_api_retries"],
        maximum_run_output_tokens=MAXIMUM_API_RUN_OUTPUT_TOKENS,
    )


def _read_target_schema(path: Path) -> TargetSchemaDocument:
    """Load and validate one agent-owned target file."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        target_schema = TargetSchemaDocument.model_validate(document)
    except (OSError, ValidationError, yaml.YAMLError) as error:
        raise RuntimeError("Target schema catalog is unavailable.") from error

    if target_schema.target_table != path.stem:
        raise RuntimeError("Target schema catalog is unavailable.")
    return target_schema


def _target_schema_response(
    target_schema: TargetSchemaDocument,
) -> TargetSchemaResponse:
    """Convert an internal contract into the stable read-only API shape."""
    return TargetSchemaResponse.model_validate(
        target_schema.model_dump(exclude={"version"})
    )


@app.get(
    "/v1/target-schemas",
    response_model=TargetSchemaCatalogResponse,
    dependencies=[Depends(require_api_token)],
)
def target_schema_catalog() -> TargetSchemaCatalogResponse:
    """List validated OMOP target tables without returning raw YAML."""
    paths = sorted(TARGET_SCHEMA_DIR.glob("*.yml"))
    if not paths:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Target schema catalog is unavailable.",
        )

    try:
        schemas = sorted(
            (_read_target_schema(path) for path in paths),
            key=lambda schema: schema.display_order,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    cdm_versions = {schema.cdm_version for schema in schemas}
    display_orders = [schema.display_order for schema in schemas]
    if (
        len(cdm_versions) != 1
        or display_orders != list(range(1, len(schemas) + 1))
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Target schema catalog is unavailable.",
        )

    tables = [
        TargetSchemaSummaryResponse(
            target_table=schema.target_table,
            display_order=schema.display_order,
            cdm_schema=schema.cdm_schema,
            cdm_version=schema.cdm_version,
            required=schema.required,
            description=schema.description,
            field_count=len(schema.fields),
        )
        for schema in schemas
    ]
    return TargetSchemaCatalogResponse(
        cdm_version=next(iter(cdm_versions)),
        table_count=len(tables),
        field_count=sum(table.field_count for table in tables),
        tables=tables,
    )


@app.get(
    "/v1/target-schemas/{omop_table}",
    response_model=TargetSchemaResponse,
    dependencies=[Depends(require_api_token)],
)
def target_schema(omop_table: OmopTable) -> TargetSchemaResponse:
    """Return one validated OMOP target table as structured JSON."""
    path = TARGET_SCHEMA_DIR / f"{omop_table}.yml"
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target schema was not found.",
        )

    try:
        document = _read_target_schema(path)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return _target_schema_response(document)


def _validate_request_specifications(
    request: ValidationRequest,
) -> ValidatedSpecs:
    """Validate request YAML against the agent-owned target schema."""
    target_schema_path = (
        TARGET_SCHEMA_DIR / f"{request.omop_table}.yml"
    )
    try:
        target_schema_content = target_schema_path.read_text(
            encoding="utf-8"
        )
    except OSError as error:
        raise SpecValidationError(
            "The agent has no target schema for "
            f"'{request.omop_table}'."
        ) from error

    source_contents = {
        specification.file_name: specification.content
        for specification in request.source_schemas
    }
    return validate_spec_contents(
        request.omop_table,
        request.mapping.content,
        source_contents,
        target_schema_content,
    )


def _load_agent_config(
    generation_settings: ProjectGenerationSettings | None = None,
) -> dict:
    """Load agent defaults and apply only validated project choices."""
    try:
        raw_config = yaml.safe_load(
            CONFIG_PATH.read_text(encoding="utf-8")
        )
        if not isinstance(raw_config, dict):
            raise ValueError("config.yaml must contain a mapping")
        config = AgentConfig.model_validate(raw_config).model_dump()
    except (
        OSError,
        ValueError,
        ValidationError,
        yaml.YAMLError,
    ) as error:
        raise ValueError("Agent configuration is invalid.") from error

    if generation_settings is None:
        return config

    limits = config["project_limits"]
    if (
        generation_settings.model is not None
        and generation_settings.model not in limits["allowed_models"]
    ):
        raise ValueError(
            "Selected model is not allowed. Choose one of: "
            + ", ".join(limits["allowed_models"])
        )
    if (
        generation_settings.maximum_output_tokens_per_request is not None
        and generation_settings.maximum_output_tokens_per_request
        > limits["maximum_output_tokens_per_request"]
    ):
        raise ValueError(
            "Maximum output tokens per request exceeds the agent limit of "
            f"{limits['maximum_output_tokens_per_request']}."
        )
    if (
        generation_settings.maximum_initial_prompt_characters is not None
        and generation_settings.maximum_initial_prompt_characters
        > limits["maximum_initial_prompt_characters"]
    ):
        raise ValueError(
            "Initial request character limit exceeds the agent maximum of "
            f"{limits['maximum_initial_prompt_characters']}."
        )
    if (
        generation_settings.automatic_api_retries is not None
        and generation_settings.automatic_api_retries
        > limits["maximum_api_retries"]
    ):
        raise ValueError(
            "Automatic API retries exceed the agent maximum of "
            f"{limits['maximum_api_retries']}."
        )

    config["source"]["reference_style"] = (
        generation_settings.source_reference_style
    )
    config["source"]["source_name"] = generation_settings.source_name
    config["output"]["format"] = generation_settings.output_format
    config["output"]["dialect"] = generation_settings.sql_dialect
    if generation_settings.model is not None:
        config["model"] = generation_settings.model
    if generation_settings.maximum_output_tokens_per_request is not None:
        config["max_output_tokens"] = (
            generation_settings.maximum_output_tokens_per_request
        )
    if generation_settings.maximum_initial_prompt_characters is not None:
        config["max_initial_prompt_characters"] = (
            generation_settings.maximum_initial_prompt_characters
        )
    if generation_settings.automatic_api_retries is not None:
        config["max_api_retries"] = (
            generation_settings.automatic_api_retries
        )
    try:
        return AgentConfig.model_validate(config).model_dump()
    except ValidationError as error:
        raise ValueError("Agent configuration is invalid.") from error


def _bounded_generation_diagnostics(result: dict) -> list[str]:
    """Return only bounded deterministic diagnostics from the local loop."""
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, list):
        return ["The agent did not produce valid SQL."]

    bounded = [
        item[:500]
        for item in diagnostics
        if isinstance(item, str) and item.strip()
    ][:20]
    return bounded or ["The agent did not produce valid SQL."]


@app.post(
    "/v1/validate",
    response_model=ValidationResponse,
    dependencies=[Depends(require_api_token)],
)
def validate(request: ValidationRequest) -> ValidationResponse:
    """Validate specifications without calling an AI provider."""
    try:
        specs = _validate_request_specifications(request)
    except SpecValidationError as error:
        return ValidationResponse(
            valid=False,
            generation_ready=False,
            omop_table=request.omop_table,
            errors=[str(error)],
        )

    pending_reviews = list(pending_review_fields(specs))
    return ValidationResponse(
        valid=True,
        generation_ready=not pending_reviews,
        omop_table=request.omop_table,
        source_models=sorted(specs.source_models),
        target_field_count=len(specs.target_schema.fields),
        pending_reviews=pending_reviews,
    )


@app.post(
    "/v1/preflight",
    response_model=PreflightResponse,
    dependencies=[Depends(require_api_token)],
)
def preflight(request: PreflightRequest) -> PreflightResponse:
    """Return generation readiness without creating an AI provider."""
    try:
        specs = _validate_request_specifications(request)
    except SpecValidationError as error:
        return PreflightResponse(
            valid=False,
            generation_ready=False,
            omop_table=request.omop_table,
            errors=[str(error)],
        )

    try:
        config = _load_agent_config(request.generation_settings)
    except ValueError as error:
        return PreflightResponse(
            valid=False,
            generation_ready=False,
            omop_table=request.omop_table,
            errors=[str(error)],
        )
    attempts_limit = config["project_limits"][
        "maximum_generation_attempts"
    ]
    if request.max_iterations > attempts_limit:
        return PreflightResponse(
            valid=False,
            generation_ready=False,
            omop_table=request.omop_table,
            errors=[
                "Generation attempts exceed the agent maximum of "
                f"{attempts_limit}."
            ],
        )

    result = build_generation_preflight(
        request.omop_table,
        specs,
        config,
        request.max_iterations,
    )
    blockers = generation_readiness_blockers(result)
    run_ceiling_exceeded = (
        result.output_token_ceiling > MAXIMUM_API_RUN_OUTPUT_TOKENS
    )
    if run_ceiling_exceeded:
        blockers += (
            "Calculated maximum run output is "
            f"{result.output_token_ceiling} tokens, exceeding the service "
            f"limit of {MAXIMUM_API_RUN_OUTPUT_TOKENS}. Reduce output tokens "
            "per response, attempts or retries.",
        )

    return PreflightResponse(
        valid=True,
        generation_ready=(
            result.generation_ready and not run_ceiling_exceeded
        ),
        omop_table=request.omop_table,
        blockers=list(blockers),
        pending_reviews=list(result.pending_reviews),
        provider=config["provider"],
        model=config["model"],
        sql_dialect=config["output"]["dialect"],
        output_format=config["output"]["format"],
        source_reference_style=config["source"]["reference_style"],
        maximum_output_tokens_per_request=config["max_output_tokens"],
        automatic_api_retries=config["max_api_retries"],
        maximum_generation_attempts=request.max_iterations,
        worst_case_output_tokens=result.output_token_ceiling,
        context_characters=result.context_characters,
        initial_request_characters=(
            result.initial_request_characters
        ),
        maximum_initial_prompt_characters=(
            result.maximum_initial_prompt_characters
        ),
        estimated_initial_input_tokens=(
            result.estimated_initial_input_tokens
        ),
        estimated_maximum_input_tokens=(
            result.estimated_maximum_input_tokens
        ),
        estimated_maximum_cost_usd=(
            result.estimated_maximum_cost_usd
        ),
        cost_currency=config["pricing"]["currency"],
        pricing_verified_on=config["pricing"]["verified_on"],
    )


@app.post(
    "/v1/generate",
    response_model=GenerationResponse,
    dependencies=[Depends(require_api_token)],
)
def generate(request: GenerationRequest) -> GenerationResponse:
    """Generate validated SQL after an exact cost-ceiling confirmation."""
    try:
        specs = _validate_request_specifications(request)
        config = _load_agent_config(request.generation_settings)
    except (SpecValidationError, ValueError) as error:
        return GenerationResponse(
            status="blocked",
            completed=False,
            omop_table=request.omop_table,
            errors=[str(error)],
        )
    attempts_limit = config["project_limits"][
        "maximum_generation_attempts"
    ]
    if request.max_iterations > attempts_limit:
        return GenerationResponse(
            status="blocked",
            completed=False,
            omop_table=request.omop_table,
            errors=[
                "Generation attempts exceed the agent maximum of "
                f"{attempts_limit}."
            ],
        )

    preflight_result = build_generation_preflight(
        request.omop_table,
        specs,
        config,
        request.max_iterations,
    )
    if not preflight_result.generation_ready:
        blockers = generation_readiness_blockers(preflight_result)
        return GenerationResponse(
            status="blocked",
            completed=False,
            omop_table=request.omop_table,
            errors=list(blockers),
            output_token_ceiling=preflight_result.output_token_ceiling,
            model=config["model"],
        )

    current_ceiling = preflight_result.output_token_ceiling
    maximum_cost = preflight_result.estimated_maximum_cost_usd
    cost_currency = config["pricing"]["currency"]
    pricing_verified_on = config["pricing"]["verified_on"]
    if request.confirmed_output_token_ceiling != current_ceiling:
        return GenerationResponse(
            status="blocked",
            completed=False,
            omop_table=request.omop_table,
            errors=[
                "The confirmed output-token ceiling no longer matches "
                f"the current ceiling of {current_ceiling}. "
                "Run preflight again."
            ],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )
    if current_ceiling > MAXIMUM_API_RUN_OUTPUT_TOKENS:
        return GenerationResponse(
            status="blocked",
            completed=False,
            omop_table=request.omop_table,
            errors=[
                "The configured output-token ceiling exceeds the "
                f"HTTP API limit of {MAXIMUM_API_RUN_OUTPUT_TOKENS}."
            ],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )

    if not GENERATION_SEMAPHORE.acquire(blocking=False):
        return GenerationResponse(
            status="busy",
            completed=False,
            omop_table=request.omop_table,
            errors=["Another generation request is already running."],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )

    try:
        result = run_agent_with_specs(
            omop_table=request.omop_table,
            specs=specs,
            config=config,
            max_iterations=request.max_iterations,
            promote_output=False,
        )
    except KeyError:
        return GenerationResponse(
            status="failed",
            completed=False,
            omop_table=request.omop_table,
            errors=["OpenAI authentication is not configured."],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )
    except OpenAIError as error:
        return GenerationResponse(
            status="failed",
            completed=False,
            omop_table=request.omop_table,
            errors=[api_error_message(error)],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )
    except (OSError, ValueError):
        return GenerationResponse(
            status="failed",
            completed=False,
            omop_table=request.omop_table,
            errors=["The agent could not process the generated output."],
            output_token_ceiling=current_ceiling,
            model=config["model"],
            estimated_maximum_cost_usd=maximum_cost,
            cost_currency=cost_currency,
            pricing_verified_on=pricing_verified_on,
        )
    finally:
        GENERATION_SEMAPHORE.release()

    usage = GenerationUsage.model_validate(result.get("usage", {}))
    completed = result.get("status") == "done"
    return GenerationResponse(
        status=result.get("status", "failed"),
        completed=completed,
        omop_table=request.omop_table,
        errors=(
            []
            if completed
            else _bounded_generation_diagnostics(result)
        ),
        output_sql=result.get("output_sql") if completed else None,
        iterations=result.get("iterations", 0),
        output_token_ceiling=current_ceiling,
        model=config["model"],
        estimated_maximum_cost_usd=maximum_cost,
        estimated_actual_cost_usd=estimated_usage_cost_usd(
            usage,
            config,
        ),
        cost_currency=cost_currency,
        pricing_verified_on=pricing_verified_on,
        usage=usage,
    )
