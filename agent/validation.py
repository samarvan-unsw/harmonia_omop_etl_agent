"""Load and cross-validate source, mapping, and target specification documents."""

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
from .yaml_loader import load_yaml


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
        return load_yaml(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecValidationError(f"Invalid YAML in {path}: {exc}") from exc


def _load_yaml_text(content: str, label: str) -> Any:
    """Parse one in-memory YAML document for API-backed validation."""
    try:
        return load_yaml(content)
    except (RecursionError, yaml.YAMLError) as exc:
        raise SpecValidationError(f"Invalid YAML in {label}: {exc}") from exc


def _format_contract_error(
    contract_name: str,
    label: str,
    error: ValidationError,
) -> SpecValidationError:
    """Format contract errors without echoing submitted specification values."""
    details = []
    for item in error.errors(
        include_input=False,
        include_url=False,
    ):
        location = ".".join(str(part) for part in item["loc"])
        prefix = f"{location}: " if location else ""
        details.append(prefix + item["msg"])

    return SpecValidationError(
        f"Invalid {contract_name} contract in {label}:\n"
        + "\n".join(details)
    )


def _validate_mapping_document(
    omop_table: str,
    document: Any,
    label: str,
) -> MappingDocument:
    """Validate one parsed mapping document and its target-table identity."""
    try:
        mapping = MappingDocument.model_validate(document)
    except ValidationError as exc:
        raise _format_contract_error("mapping", label, exc) from exc

    if mapping.target_table != omop_table:
        raise SpecValidationError(
            f"Mapping target_table '{mapping.target_table}' does not match "
            f"requested table '{omop_table}'"
        )
    return mapping


def _validate_source_document(
    model_name: str,
    document: Any,
    label: str,
) -> SourceModel:
    """Validate one parsed source-schema document and select its model."""
    try:
        source_schema = SourceSchemaDocument.model_validate(document)
    except ValidationError as exc:
        raise _format_contract_error(
            "source-schema",
            label,
            exc,
        ) from exc

    matches = [
        model for model in source_schema.models
        if model.name == model_name
    ]
    if len(matches) != 1:
        raise SpecValidationError(
            f"{label} must contain exactly one model named '{model_name}'"
        )
    return matches[0]


def _validate_target_document(
    omop_table: str,
    document: Any,
    label: str,
) -> TargetSchemaDocument:
    """Validate one parsed OMOP target-schema document."""
    try:
        target_schema = TargetSchemaDocument.model_validate(document)
    except ValidationError as exc:
        raise _format_contract_error(
            "target-schema",
            label,
            exc,
        ) from exc

    if target_schema.target_table != omop_table:
        raise SpecValidationError(
            f"Target schema '{target_schema.target_table}' does not match "
            f"requested table '{omop_table}'"
        )
    return target_schema


def _load_mapping(omop_table: str, specs_dir: Path) -> MappingDocument:
    """Load and validate one OMOP mapping document."""
    path = specs_dir / "mappings" / f"{omop_table}.yml"
    return _validate_mapping_document(
        omop_table,
        _load_yaml(path),
        str(path),
    )


def _load_source_model(model_name: str, specs_dir: Path) -> SourceModel:
    """Load one source model using the one-file-per-model convention."""
    path = specs_dir / "source_schema" / f"{model_name}.yml"
    return _validate_source_document(
        model_name,
        _load_yaml(path),
        str(path),
    )


def _load_target_schema(
    omop_table: str,
    specs_dir: Path,
) -> TargetSchemaDocument:
    """Load and validate one OMOP target-schema document."""
    path = specs_dir / "target_schema" / f"{omop_table}.yml"
    return _validate_target_document(
        omop_table,
        _load_yaml(path),
        str(path),
    )


def _validate_omop_table(omop_table: str) -> None:
    """Reject target-table values that are unsafe as file identifiers."""
    if not OMOP_TABLE_PATTERN.fullmatch(omop_table):
        raise SpecValidationError(
            "OMOP table must use lowercase letters, numbers and underscores"
        )


def _validate_references(
    mapping: MappingDocument,
    source_models: dict[str, SourceModel],
    target_schema: TargetSchemaDocument,
) -> ValidatedSpecs:
    """Validate cross-file source fields and OMOP target coverage."""
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


def validate_spec_contents(
    omop_table: str,
    mapping_content: str,
    source_contents: dict[str, str],
    target_schema_content: str,
) -> ValidatedSpecs:
    """Validate in-memory API inputs using the authoritative agent contracts."""
    _validate_omop_table(omop_table)
    mapping_label = f"mappings/{omop_table}.yml"
    mapping = _validate_mapping_document(
        omop_table,
        _load_yaml_text(mapping_content, mapping_label),
        mapping_label,
    )
    target_label = f"target_schema/{omop_table}.yml"
    target_schema = _validate_target_document(
        omop_table,
        _load_yaml_text(target_schema_content, target_label),
        target_label,
    )
    source_models = {}

    for model_name in mapping.source_models:
        file_name = f"{model_name}.yml"
        label = f"source_schema/{file_name}"
        content = source_contents.get(file_name)
        if content is None:
            raise SpecValidationError(
                f"Specification file not found: {label}"
            )
        source_models[model_name] = _validate_source_document(
            model_name,
            _load_yaml_text(content, label),
            label,
        )

    return _validate_references(
        mapping,
        source_models,
        target_schema,
    )


def validate_specs(omop_table: str, specs_dir: Path) -> ValidatedSpecs:
    """Validate local specification files for CLI and VS Code workflows."""
    _validate_omop_table(omop_table)
    mapping = _load_mapping(omop_table, specs_dir)
    target_schema = _load_target_schema(omop_table, specs_dir)
    source_models = {
        model_name: _load_source_model(model_name, specs_dir)
        for model_name in mapping.source_models
    }
    return _validate_references(
        mapping,
        source_models,
        target_schema,
    )
