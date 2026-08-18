"""Define strict Pydantic contracts for configuration, schemas, and mappings."""

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .dialects import SqlDialect


ProviderName = Literal["codex", "anthropic"]


# =============================================================================
# SHARED CONTRACT BASE
# =============================================================================

class StrictModel(BaseModel):
    """Reject unexpected properties in specification YAML."""

    model_config = ConfigDict(extra="forbid")


# Reusable safe identifier for model, table and field names.
SqlIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]


# =============================================================================
# AGENT CONFIGURATION CONTRACT
# =============================================================================

class SourceConfig(StrictModel):
    """How generated SQL references source relations."""

    reference_style: Literal["relation", "dbt_ref", "dbt_source"]
    source_name: SqlIdentifier | None = None

    @model_validator(mode="after")
    def validate_source_name(self) -> "SourceConfig":
        """Require a dbt source name when source() syntax is selected."""
        if self.reference_style == "dbt_source" and not self.source_name:
            raise ValueError("dbt_source requires source.source_name")
        return self


class OutputConfig(StrictModel):
    """Generated artifact format and SQL dialect."""

    format: Literal["sql", "dbt"]
    dialect: SqlDialect


class ProjectLimitsConfig(StrictModel):
    """Server-owned bounds for user-selectable generation settings."""

    allowed_models: list[str] = Field(min_length=1)
    model_providers: dict[str, ProviderName] = Field(min_length=1)
    maximum_output_tokens_per_request: int = Field(ge=100, le=20_000)
    maximum_initial_prompt_characters: int = Field(
        ge=1_000,
        le=1_000_000,
    )
    maximum_generation_attempts: int = Field(ge=1, le=2)
    maximum_api_retries: int = Field(ge=0, le=2)

    @field_validator("allowed_models")
    @classmethod
    def validate_allowed_models(cls, value: list[str]) -> list[str]:
        """Reject blank or duplicate model identifiers."""
        normalized = [model.strip() for model in value]
        if (
            any(not model or len(model) > 100 for model in normalized)
            or len(normalized) != len(set(normalized))
        ):
            raise ValueError("allowed_models must be unique non-empty values")
        return normalized

    @field_validator("model_providers")
    @classmethod
    def validate_model_provider_names(
        cls,
        value: dict[str, ProviderName],
    ) -> dict[str, ProviderName]:
        """Reject malformed model identifiers in the provider registry."""
        if any(
            not model
            or len(model) > 100
            or not model[0].isalnum()
            or any(
                not (character.isalnum() or character in "._-")
                for character in model
            )
            for model in value
        ):
            raise ValueError("model_providers contains invalid model names")
        return value


class ModelPricingConfig(StrictModel):
    """Standard API token rates for one allowed model."""

    input_usd_per_million_tokens: float = Field(gt=0, le=1_000)
    cached_input_usd_per_million_tokens: float = Field(gt=0, le=1_000)
    cache_write_input_usd_per_million_tokens: float = Field(
        gt=0,
        le=1_000,
    )
    output_usd_per_million_tokens: float = Field(gt=0, le=1_000)


class PricingConfig(StrictModel):
    """Versioned pricing snapshot used only for cost estimates."""

    currency: Literal["USD"]
    verified_on: date
    models: dict[str, ModelPricingConfig] = Field(min_length=1)

    @field_validator("models")
    @classmethod
    def validate_model_names(
        cls,
        value: dict[str, ModelPricingConfig],
    ) -> dict[str, ModelPricingConfig]:
        """Reject malformed model identifiers in the pricing registry."""
        if any(
            not model
            or len(model) > 100
            or not model[0].isalnum()
            or any(
                not (character.isalnum() or character in "._-")
                for character in model
            )
            for model in value
        ):
            raise ValueError("pricing model identifiers are invalid")
        return value


class AgentConfig(StrictModel):
    """Root structure of config.yaml."""

    provider: ProviderName
    model: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    max_initial_prompt_characters: int = Field(gt=0)
    max_api_retries: int = Field(ge=0, le=2)
    source: SourceConfig
    output: OutputConfig
    project_limits: ProjectLimitsConfig
    pricing: PricingConfig

    @model_validator(mode="after")
    def validate_compatibility(self) -> "AgentConfig":
        """Reject source syntax that cannot appear in plain SQL."""
        if (
            self.output.format == "sql"
            and self.source.reference_style != "relation"
        ):
            raise ValueError(
                "plain SQL output requires source.reference_style=relation"
            )
        if self.model not in self.project_limits.allowed_models:
            raise ValueError("model must be included in allowed_models")
        if set(self.project_limits.allowed_models) != set(
            self.project_limits.model_providers
        ):
            raise ValueError(
                "model_providers must exactly match allowed_models"
            )
        if set(self.project_limits.allowed_models) != set(
            self.pricing.models
        ):
            raise ValueError(
                "pricing models must exactly match allowed_models"
            )
        if (
            self.project_limits.model_providers[self.model]
            != self.provider
        ):
            raise ValueError("model is not available from the provider")
        if (
            self.max_output_tokens
            > self.project_limits.maximum_output_tokens_per_request
        ):
            raise ValueError(
                "max_output_tokens exceeds the project setting maximum"
            )
        if (
            self.max_initial_prompt_characters
            > self.project_limits.maximum_initial_prompt_characters
        ):
            raise ValueError(
                "max_initial_prompt_characters exceeds the project setting "
                "maximum"
            )
        if (
            self.max_api_retries
            > self.project_limits.maximum_api_retries
        ):
            raise ValueError(
                "max_api_retries exceeds the project setting maximum"
            )
        return self


# =============================================================================
# SOURCE-SCHEMA YAML CONTRACT
# =============================================================================

class SourceForeignKey(StrictModel):
    """A declared relationship to another source-model field."""

    model: SqlIdentifier
    field: SqlIdentifier


class SourceLineage(StrictModel):
    """Optional, non-authoritative lineage retained from imported metadata."""

    model: SqlIdentifier | None = None
    field: SqlIdentifier | None = None
    expression: str | None = Field(default=None, max_length=5_000)
    transformation: str | None = Field(default=None, max_length=5_000)


class SourceTerminology(StrictModel):
    """Optional terminology hints used only to improve mapping review."""

    status: str | None = Field(default=None, max_length=255)
    vocabulary: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=255)
    representation: str | None = Field(default=None, max_length=255)


class SourceColumnSemantic(StrictModel):
    """Bounded semantic hints for one source field."""

    role: str | None = Field(default=None, max_length=255)
    semantic_type: str | None = Field(default=None, max_length=255)
    value_type: str | None = Field(default=None, max_length=255)
    identifier_scope: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=255)
    sensitivity: str | None = Field(default=None, max_length=255)
    filterable: bool | None = None
    groupable: bool | None = None
    aggregatable: bool | None = None
    default_aggregation: str | None = Field(default=None, max_length=255)
    synonyms: list[str] = Field(default_factory=list, max_length=100)
    allowed_values: list[str] = Field(default_factory=list, max_length=500)
    terminology: SourceTerminology | None = None
    source: SourceLineage | None = None


class SourceAlternateKey(StrictModel):
    """A declared or inferred candidate key retained for review."""

    columns: list[SqlIdentifier] = Field(min_length=1, max_length=100)
    meaning: str = Field(default="", max_length=1_000)


class SourceIncomingReference(StrictModel):
    """An inverse relationship hint supplied by source metadata."""

    model: SqlIdentifier
    field: SqlIdentifier
    relationship: str | None = Field(default=None, max_length=255)


class SourceModelSemantic(StrictModel):
    """Bounded semantic hints for one source model."""

    entity: str | None = Field(default=None, max_length=255)
    subject_area: str | None = Field(default=None, max_length=255)
    grain: str | None = Field(default=None, max_length=1_000)
    source_model: SqlIdentifier | None = None
    alternate_keys: list[SourceAlternateKey] = Field(
        default_factory=list,
        max_length=100,
    )
    referenced_by: list[SourceIncomingReference] = Field(
        default_factory=list,
        max_length=500,
    )


class SourceColumn(StrictModel):
    """A source column available for OMOP mapping."""

    name: SqlIdentifier
    data_type: str | None = Field(default=None, max_length=255)
    description: str = Field(default="", max_length=10_000)
    primary_key: bool = False
    foreign_key: SourceForeignKey | None = None
    semantic: SourceColumnSemantic | None = None


class SourceModel(StrictModel):
    """A source table or dbt model."""

    name: SqlIdentifier
    description: str = Field(default="", max_length=10_000)
    semantic: SourceModelSemantic | None = None
    columns: list[SourceColumn] = Field(
        default_factory=list,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def validate_columns(self) -> "SourceModel":
        """Reject ambiguous duplicate column declarations."""
        column_names = [column.name for column in self.columns]
        if len(column_names) != len(set(column_names)):
            raise ValueError("source columns must be unique within a model")
        return self


class SourceSchemaDocument(StrictModel):
    """Root structure of a source-schema YAML file."""

    version: Literal[2]
    models: list[SourceModel] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_models(self) -> "SourceSchemaDocument":
        """Reject ambiguous duplicate model declarations."""
        model_names = [model.name for model in self.models]
        if len(model_names) != len(set(model_names)):
            raise ValueError("source models must be unique")
        return self


# =============================================================================
# SOURCE-TO-OMOP MAPPING YAML CONTRACT
# =============================================================================

class SourceFieldReference(StrictModel):
    """A field in one declared source model."""

    model: SqlIdentifier
    field: SqlIdentifier


class SourceJoin(StrictModel):
    """A join condition between two source fields."""

    join_type: Literal["inner", "left"]
    left: SourceFieldReference
    right: SourceFieldReference


class FieldMapping(StrictModel):
    """Instructions for producing one OMOP target field."""

    target_field: SqlIdentifier
    action: Literal["map", "derive", "null"]
    source_fields: list[SourceFieldReference] = Field(default_factory=list)
    transformation: str = ""
    comment: str = Field(default="", max_length=10_000)
    mapping_table_name: SqlIdentifier | None = None
    review_required: bool = False
    review_status: Literal["pending", "approved"] | None = None
    review_comment: str | None = None

    @field_validator("action", mode="before")
    @classmethod
    def normalize_retired_skip_action(cls, value):
        """Keep historical specifications readable after retiring `skip`."""
        return "null" if value == "skip" else value

    @field_validator("transformation", mode="before")
    @classmethod
    def normalize_empty_transformation(cls, value):
        """Treat an empty YAML value as omitted transformation text."""
        return "" if value is None else value

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_empty_comment(cls, value):
        """Treat an empty YAML value as omitted documentation text."""
        return "" if value is None else value

    @field_validator("mapping_table_name", mode="before")
    @classmethod
    def normalize_blank_mapping_table_name(cls, value):
        """Treat a blank lookup name as an explicit request for a default."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_action(self) -> "FieldMapping":
        """Ensure each mapping action has a coherent source definition."""
        if self.action == "map" and len(self.source_fields) != 1:
            raise ValueError("map requires exactly one source field")
        if (
            self.action in {"map", "derive"}
            and not self.transformation.strip()
            and "mapping_table_name" not in self.model_fields_set
            and not self.source_fields
        ):
            raise ValueError(
                "a blank transformation requires at least one source field"
            )
        if self.action == "null" and self.source_fields:
            raise ValueError(f"{self.action} cannot contain source fields")
        if (
            "mapping_table_name" in self.model_fields_set
            and self.action == "null"
        ):
            raise ValueError(
                f"{self.action} cannot declare a mapping_table_name"
            )
        if (
            "mapping_table_name" in self.model_fields_set
            and not self.source_fields
        ):
            raise ValueError(
                "mapping_table_name requires at least one source field"
            )
        if self.review_required:
            if not self.review_comment:
                raise ValueError("review_required requires review_comment")
            if self.review_status is None:
                raise ValueError("review_required requires review_status")
        elif self.review_status is not None:
            raise ValueError(
                "review_status is only allowed when review_required is true"
            )
        return self


class MappingChange(StrictModel):
    """One user-maintained semantic change to an ETL mapping."""

    date: date
    description: str = Field(min_length=1, max_length=5_000)
    author: str = Field(default="", max_length=255)


class MappingDocument(StrictModel):
    """Root structure of one source-to-OMOP mapping file."""

    version: Literal[1]
    target_table: SqlIdentifier
    notes: str = Field(default="", max_length=10_000)
    source_models: list[SqlIdentifier] = Field(min_length=1)
    joins: list[SourceJoin] = Field(default_factory=list)
    union_all: list[SqlIdentifier] = Field(default_factory=list)
    fields: list[FieldMapping] = Field(min_length=1)
    change_log: list[MappingChange] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_references(self) -> "MappingDocument":
        """Reject duplicate targets and undeclared source-model references."""
        for mapping in self.fields:
            if (
                "mapping_table_name" in mapping.model_fields_set
                and mapping.mapping_table_name is None
            ):
                mapping.mapping_table_name = (
                    f"mapping_{self.target_table}_{mapping.target_field}"
                )

        if len(self.source_models) != len(set(self.source_models)):
            raise ValueError("source_models contains duplicates")
        if self.union_all and len(self.union_all) < 2:
            raise ValueError("union_all requires at least two source models")
        if len(self.union_all) != len(set(self.union_all)):
            raise ValueError("union_all contains duplicate source models")
        undeclared_union_models = sorted(
            set(self.union_all) - set(self.source_models)
        )
        if undeclared_union_models:
            raise ValueError(
                "union_all source models are not declared: "
                + ", ".join(undeclared_union_models)
            )

        target_fields = [mapping.target_field for mapping in self.fields]
        if len(target_fields) != len(set(target_fields)):
            raise ValueError("target fields must be unique")

        references = [
            reference
            for mapping in self.fields
            for reference in mapping.source_fields
        ]
        references.extend(
            reference
            for join in self.joins
            for reference in (join.left, join.right)
        )

        undeclared = sorted(
            {reference.model for reference in references}
            - set(self.source_models)
        )
        if undeclared:
            raise ValueError(
                f"source models are not declared: {', '.join(undeclared)}"
            )

        union_models = set(self.union_all)
        for mapping in self.fields:
            if (
                mapping.action in {"map", "derive"}
                and not mapping.transformation.strip()
                and mapping.mapping_table_name is None
                and len(mapping.source_fields) > 1
            ):
                referenced_models = [
                    reference.model for reference in mapping.source_fields
                ]
                if (
                    not union_models
                    or not set(referenced_models).issubset(union_models)
                    or len(referenced_models) != len(set(referenced_models))
                ):
                    raise ValueError(
                        f"{mapping.target_field}: a blank transformation "
                        "with multiple source fields requires one field per "
                        "declared union_all model"
                    )
        return self


# =============================================================================
# OMOP TARGET-SCHEMA YAML CONTRACT
# =============================================================================

class TargetForeignKey(StrictModel):
    """An OMOP foreign-key target and optional vocabulary domain."""

    table: SqlIdentifier
    field: SqlIdentifier | None = None
    domain: str | None = None
    class_name: str | None = None


class TargetField(StrictModel):
    """One field required or supported by an OMOP target table."""

    name: SqlIdentifier
    data_type: str
    required: bool
    primary_key: bool = False
    foreign_key: TargetForeignKey | None = None
    description: str = ""
    etl_convention: str = ""


class TargetSchemaDocument(StrictModel):
    """Root structure of one OMOP target-schema file."""

    version: Literal[1]
    display_order: int = Field(ge=1)
    cdm_version: str
    target_table: SqlIdentifier
    cdm_schema: Literal["CDM", "VOCAB", "RESULTS"]
    required: bool
    concept_prefix: str | None = None
    measure_person_completeness: bool
    measure_person_completeness_threshold: float | None = None
    description: str = ""
    user_guidance: str = ""
    etl_convention: str = ""
    fields: list[TargetField] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fields(self) -> "TargetSchemaDocument":
        """Reject duplicate fields and invalid primary-key declarations."""
        field_names = [field.name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("target fields must be unique")

        optional_primary_keys = [
            field.name
            for field in self.fields
            if field.primary_key and not field.required
        ]
        if optional_primary_keys:
            raise ValueError(
                "primary-key fields must be required: "
                + ", ".join(optional_primary_keys)
            )
        return self
