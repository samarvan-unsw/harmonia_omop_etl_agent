from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml
from pydantic import ValidationError

from .contracts import (
    MappingDocument,
    SourceModel,
    SourceSchemaDocument,
    TargetSchemaDocument,
)


class SpecValidationError(ValueError):
    """Raised when specification files are missing or inconsistent."""


OMOP_TABLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ValidatedSpecs:
    """Validated mapping and the source models it references."""

    mapping: MappingDocument
    source_models: dict[str, SourceModel]
    target_schema: TargetSchemaDocument


def pending_review_fields(specs: ValidatedSpecs) -> tuple[str, ...]:
    """Return target fields whose mapping review is not yet approved."""
    return tuple(
        mapping.target_field
        for mapping in specs.mapping.fields
        if mapping.review_required and mapping.review_status == "pending"
    )


def _load_yaml(path: Path) -> Any:
    """Read one YAML file and provide a path-aware parsing error."""
    if not path.is_file():
        raise SpecValidationError(f"Specification file not found: {path}")

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecValidationError(f"Invalid YAML in {path}: {exc}") from exc


def _load_mapping(omop_table: str, specs_dir: Path) -> MappingDocument:
    """Load and validate one OMOP mapping document."""
    path = specs_dir / "mappings" / f"{omop_table}.yml"
    try:
        mapping = MappingDocument.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise SpecValidationError(f"Invalid mapping contract in {path}:\n{exc}") from exc

    if mapping.target_table != omop_table:
        raise SpecValidationError(
            f"Mapping target_table '{mapping.target_table}' does not match "
            f"requested table '{omop_table}'"
        )
    return mapping


def _load_source_model(model_name: str, specs_dir: Path) -> SourceModel:
    """Load one source model using the one-file-per-model convention."""
    path = specs_dir / "source_schema" / f"{model_name}.yml"
    try:
        document = SourceSchemaDocument.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise SpecValidationError(
            f"Invalid source-schema contract in {path}:\n{exc}"
        ) from exc

    matches = [model for model in document.models if model.name == model_name]
    if len(matches) != 1:
        raise SpecValidationError(
            f"{path} must contain exactly one model named '{model_name}'"
        )
    return matches[0]


def _load_target_schema(
    omop_table: str,
    specs_dir: Path,
) -> TargetSchemaDocument:
    """Load and validate one OMOP target-schema document."""
    path = specs_dir / "target_schema" / f"{omop_table}.yml"
    try:
        target_schema = TargetSchemaDocument.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise SpecValidationError(
            f"Invalid target-schema contract in {path}:\n{exc}"
        ) from exc

    if target_schema.target_table != omop_table:
        raise SpecValidationError(
            f"Target schema '{target_schema.target_table}' does not match "
            f"requested table '{omop_table}'"
        )
    return target_schema


def validate_specs(omop_table: str, specs_dir: Path) -> ValidatedSpecs:
    """Validate a mapping and all source model/field references it uses."""
    if not OMOP_TABLE_PATTERN.fullmatch(omop_table):
        raise SpecValidationError(
            "OMOP table must use lowercase letters, numbers and underscores"
        )

    mapping = _load_mapping(omop_table, specs_dir)
    target_schema = _load_target_schema(omop_table, specs_dir)
    source_models = {
        model_name: _load_source_model(model_name, specs_dir)
        for model_name in mapping.source_models
    }

    available_fields = {
        model_name: {column.name for column in model.columns}
        for model_name, model in source_models.items()
    }

    references = [
        reference
        for field_mapping in mapping.fields
        for reference in field_mapping.source_fields
    ]
    references.extend(
        reference
        for join in mapping.joins
        for reference in (join.left, join.right)
    )

    missing_fields = sorted(
        {
            f"{reference.model}.{reference.field}"
            for reference in references
            if reference.field not in available_fields[reference.model]
        }
    )
    if missing_fields:
        raise SpecValidationError(
            "Mapping references unknown source fields: "
            + ", ".join(missing_fields)
        )

    target_fields = {field.name: field for field in target_schema.fields}
    mapped_fields = {field.target_field: field for field in mapping.fields}

    unknown_targets = sorted(set(mapped_fields) - set(target_fields))
    if unknown_targets:
        raise SpecValidationError(
            "Mapping references unknown OMOP fields: "
            + ", ".join(unknown_targets)
        )

    # A required field must be mentioned in the mapping. An explicit `null`
    # action counts as mapped because it records a deliberate user decision.
    missing_required = sorted(
        field.name
        for field in target_schema.fields
        if field.required and field.name not in mapped_fields
    )
    if missing_required:
        raise SpecValidationError(
            "Required OMOP fields are not mapped: "
            + ", ".join(missing_required)
        )

    return ValidatedSpecs(
        mapping=mapping,
        source_models=source_models,
        target_schema=target_schema,
    )
