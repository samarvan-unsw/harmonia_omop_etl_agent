from pathlib import Path

from .contracts import FieldMapping, SourceModel, TargetField
from .validation import validate_specs


def _format_source_model(model: SourceModel) -> list[str]:
    """Render one validated source model as compact prompt context."""
    lines = [f"## {model.name}", model.description]

    for column in model.columns:
        data_type = f" ({column.data_type})" if column.data_type else ""
        primary_key = " [primary key]" if column.primary_key else ""
        description = f": {column.description}" if column.description else ""
        lines.append(
            f"- {column.name}{data_type}{primary_key}{description}"
        )

    return lines


def _format_target_field(
    target: TargetField,
    mapping: FieldMapping | None,
) -> list[str]:
    """Render one target field together with its mapping or NULL fallback."""
    constraints = ["required" if target.required else "optional"]
    if target.primary_key:
        constraints.append("primary key")
    if target.foreign_key:
        foreign_key = f"foreign key → {target.foreign_key.table}"
        if target.foreign_key.domain:
            foreign_key += f" ({target.foreign_key.domain})"
        constraints.append(foreign_key)

    lines = [
        f"## {target.name} ({target.data_type}) [{', '.join(constraints)}]",
    ]
    if target.description:
        lines.append(f"Description: {target.description}")
    if target.etl_convention:
        lines.append(f"ETL convention: {target.etl_convention}")

    if mapping is None:
        lines.extend(
            [
                "Mapping action: null",
                "Output value: NULL because no mapping was supplied.",
            ]
        )
        return lines

    source_fields = ", ".join(
        f"{source.model}.{source.field}" for source in mapping.source_fields
    )
    lines.extend(
        [
            f"Mapping action: {mapping.action}",
            f"Sources: {source_fields or '(none)'}",
            f"Transformation: {mapping.transformation}",
        ]
    )
    if mapping.mapping_table_name:
        mapping_columns = ", ".join(
            source.field for source in mapping.source_fields
        )
        lines.extend(
            [
                f"Mapping table: {mapping.mapping_table_name}",
                "Mapping table lookup columns: "
                f"{mapping_columns}; result column: {target.name}",
            ]
        )
    if mapping.action in {"null", "skip"}:
        lines.append("Output value: NULL.")
    if mapping.review_required:
        lines.extend(
            [
                f"Review status: {mapping.review_status}",
                f"Review comment: {mapping.review_comment}",
            ]
        )
    return lines


def build_context(omop_table: str, specs_dir: Path) -> str:
    """Build compact prompt context from validated specification files."""
    specs = validate_specs(omop_table, specs_dir)
    lines = ["# Validated source models"]

    for model_name in specs.mapping.source_models:
        lines.extend(_format_source_model(specs.source_models[model_name]))

    lines.extend(
        [
            "",
            f"# OMOP {specs.target_schema.target_table} target schema",
            f"CDM version: {specs.target_schema.cdm_version}",
            "## Joins",
        ]
    )
    if specs.mapping.joins:
        for join in specs.mapping.joins:
            lines.append(
                f"- {join.join_type.upper()}: "
                f"{join.left.model}.{join.left.field} = "
                f"{join.right.model}.{join.right.field}"
            )
    else:
        lines.append("- None")

    mappings_by_target = {
        mapping.target_field: mapping for mapping in specs.mapping.fields
    }
    lines.extend(["", "# Target fields and mappings"])
    for target_field in specs.target_schema.fields:
        lines.extend(
            _format_target_field(
                target_field,
                mappings_by_target.get(target_field.name),
            )
        )

    return "\n".join(lines)
