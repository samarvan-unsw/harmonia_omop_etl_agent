from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


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
    dialect: Literal["snowflake", "postgres", "athena", "bigquery"]


class AgentConfig(StrictModel):
    """Root structure of config.yaml."""

    provider: Literal["codex"]
    model: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    max_initial_prompt_characters: int = Field(gt=0)
    max_api_retries: int = Field(ge=0, le=2)
    source: SourceConfig
    output: OutputConfig

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
        return self


# =============================================================================
# SOURCE-SCHEMA YAML CONTRACT
# =============================================================================

class SourceColumn(StrictModel):
    """A source column available for OMOP mapping."""

    name: SqlIdentifier
    data_type: str | None = None
    description: str = ""
    primary_key: bool = False


class SourceModel(StrictModel):
    """A source table or dbt model."""

    name: SqlIdentifier
    description: str = ""
    columns: list[SourceColumn] = Field(default_factory=list)


class SourceSchemaDocument(StrictModel):
    """Root structure of a source-schema YAML file."""

    version: int
    models: list[SourceModel]


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
    action: Literal["map", "derive", "null", "skip"]
    source_fields: list[SourceFieldReference] = Field(default_factory=list)
    transformation: str = Field(min_length=1)
    mapping_table_name: SqlIdentifier | None = None
    review_required: bool = False
    review_status: Literal["pending", "approved"] | None = None
    review_comment: str | None = None

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
        if self.action in {"null", "skip"} and self.source_fields:
            raise ValueError(f"{self.action} cannot contain source fields")
        if (
            "mapping_table_name" in self.model_fields_set
            and self.action in {"null", "skip"}
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


class MappingDocument(StrictModel):
    """Root structure of one source-to-OMOP mapping file."""

    version: Literal[1]
    target_table: SqlIdentifier
    source_models: list[SqlIdentifier] = Field(min_length=1)
    joins: list[SourceJoin] = Field(default_factory=list)
    fields: list[FieldMapping] = Field(min_length=1)

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
