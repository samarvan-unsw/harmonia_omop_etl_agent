import re
from collections import defaultdict
from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .contracts import (
    FieldMapping,
    SourceFieldReference,
    SourceJoin,
    TargetField,
)


@dataclass(frozen=True)
class SqlValidationResult:
    """Result of deterministic local SQL validation."""

    valid: bool
    errors: tuple[str, ...] = ()

    def as_tool_message(self) -> str:
        """Return concise feedback suitable for the generation loop."""
        if self.valid:
            return "SQL validation passed."
        return "SQL validation failed:\n- " + "\n- ".join(self.errors)


def _replace_dbt_references(sql: str) -> str:
    """Replace common dbt relation macros before dialect parsing."""
    sql = re.sub(
        r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
        r"\1",
        sql,
    )
    sql = re.sub(
        r"\{\{\s*source\(\s*['\"][^'\"]+['\"]\s*,\s*"
        r"['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
        r"\1",
        sql,
    )
    return sql


def _projection_expression(projection: exp.Expression) -> exp.Expression:
    """Remove the target alias while retaining the expression it names."""
    return projection.this if isinstance(projection, exp.Alias) else projection


def _is_null_expression(expression: exp.Expression) -> bool:
    """Recognize NULL with optional casts or parentheses."""
    current = expression
    while isinstance(current, (exp.Cast, exp.TryCast, exp.Paren)):
        current = current.this
    return isinstance(current, exp.Null)


def _null_cast_type(expression: exp.Expression) -> exp.DataType | None:
    """Return the outer cast type when an expression is a typed NULL."""
    current = expression
    while isinstance(current, exp.Paren):
        current = current.this
    if not isinstance(current, (exp.Cast, exp.TryCast)):
        return None
    if not _is_null_expression(current.this):
        return None
    return current.args.get("to")


def _normalized_data_type(
    data_type: str | exp.DataType,
    dialect: str,
) -> str:
    """Normalize generic and dialect-specific names for comparison."""
    parsed = (
        exp.DataType.build(data_type, dialect=dialect)
        if isinstance(data_type, str)
        else data_type
    )
    normalized = re.sub(
        r"\s+",
        "",
        parsed.sql(dialect=dialect),
    ).casefold()
    if dialect == "snowflake" and normalized == "datetime":
        return "timestampntz"
    return normalized


def _model_qualifiers(statement: exp.Select) -> dict[str, set[str]]:
    """Map each referenced relation to its valid SQL qualifiers."""
    qualifiers: dict[str, set[str]] = defaultdict(set)
    for table in statement.find_all(exp.Table):
        model = table.name.casefold()
        qualifiers[model].add(model)
        if table.alias:
            qualifiers[model].add(table.alias.casefold())
    return qualifiers


def _column_matches_reference(
    column: exp.Column,
    reference: SourceFieldReference,
    qualifiers: dict[str, set[str]],
    require_qualifier: bool,
) -> bool:
    """Check whether one SQL column resolves to a declared source field."""
    if column.name.casefold() != reference.field.casefold():
        return False

    qualifier = column.table
    if not qualifier:
        return not require_qualifier

    return qualifier.casefold() in qualifiers.get(
        reference.model.casefold(),
        {reference.model.casefold()},
    )


def _validate_mapping_expressions(
    statement: exp.Select,
    expected_fields: list[str],
    field_mappings: list[FieldMapping],
    target_fields: list[TargetField] | None,
    dialect: str,
) -> list[str]:
    """Validate target expressions against mapping actions and lineage."""
    errors: list[str] = []
    mappings = {
        mapping.target_field: mapping for mapping in field_mappings
    }
    target_types = {
        target.name: target.data_type for target in target_fields or []
    }
    projections = {
        projection.alias_or_name: _projection_expression(projection)
        for projection in statement.expressions
        if projection.alias_or_name
    }
    qualifiers = _model_qualifiers(statement)
    declared_models = {
        reference.model.casefold()
        for mapping in field_mappings
        for reference in mapping.source_fields
    }
    declared_models.update(
        mapping.mapping_table_name.casefold()
        for mapping in field_mappings
        if mapping.mapping_table_name
    )
    require_qualifier = len(declared_models) > 1

    for target_field in expected_fields:
        expression = projections.get(target_field)
        if expression is None:
            # Target coverage validation reports missing projections.
            continue

        mapping = mappings.get(target_field)
        if mapping is None or mapping.action == "null":
            if not _is_null_expression(expression):
                action = mapping.action if mapping else "unmapped"
                errors.append(
                    f"{target_field} must output NULL for action {action}"
                )
                continue

            expected_type = target_types.get(target_field)
            if expected_type is not None:
                actual_type = _null_cast_type(expression)
                if actual_type is None:
                    errors.append(
                        f"{target_field} must use typed NULL: "
                        f"CAST(NULL AS {expected_type})"
                    )
                elif _normalized_data_type(
                    actual_type,
                    dialect,
                ) != _normalized_data_type(expected_type, dialect):
                    errors.append(
                        f"{target_field} NULL type must be {expected_type}, "
                        f"found {actual_type.sql(dialect=dialect)}"
                    )
            continue

        if _is_null_expression(expression):
            errors.append(
                f"{target_field} cannot output NULL for action {mapping.action}"
            )
            continue

        columns = list(expression.find_all(exp.Column))
        required_expression_references = mapping.source_fields
        allowed_expression_references = list(mapping.source_fields)
        if mapping.mapping_table_name:
            mapping_value_reference = SourceFieldReference(
                model=mapping.mapping_table_name,
                field=mapping.target_field,
            )
            required_expression_references = [mapping_value_reference]
            allowed_expression_references.append(mapping_value_reference)

        for reference in required_expression_references:
            if reference.model.casefold() not in qualifiers:
                relation_kind = (
                    "mapping table"
                    if mapping.mapping_table_name
                    and reference.model == mapping.mapping_table_name
                    else "source model"
                )
                errors.append(
                    f"{target_field} does not reference {relation_kind} "
                    f"{reference.model}"
                )
                continue

            if not any(
                _column_matches_reference(
                    column,
                    reference,
                    qualifiers,
                    require_qualifier,
                )
                for column in columns
            ):
                reference_kind = (
                    "mapping-table result"
                    if mapping.mapping_table_name
                    and reference.model == mapping.mapping_table_name
                    else "declared source field"
                )
                errors.append(
                    f"{target_field} does not use {reference_kind} "
                    f"{reference.model}.{reference.field}"
                )

        undeclared_columns = [
            column.sql()
            for column in columns
            if not any(
                _column_matches_reference(
                    column,
                    reference,
                    qualifiers,
                    require_qualifier,
                )
                for reference in allowed_expression_references
            )
        ]
        if undeclared_columns:
            errors.append(
                f"{target_field} uses undeclared source field(s): "
                + ", ".join(dict.fromkeys(undeclared_columns))
            )

    return errors


def _canonical_field_pair(
    left: tuple[str, str],
    right: tuple[str, str],
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return an order-independent representation of a field equality."""
    return tuple(sorted((left, right)))


def _declared_join_signature(
    join: SourceJoin,
) -> tuple[str, tuple[tuple[str, str], tuple[str, str]], str]:
    """Normalize one declared mapping join for deterministic comparison."""
    pair = _canonical_field_pair(
        (join.left.model.casefold(), join.left.field.casefold()),
        (join.right.model.casefold(), join.right.field.casefold()),
    )
    return join.join_type, pair, join.right.model.casefold()


def _actual_join_type(join: exp.Join) -> str | None:
    """Normalize supported SQL join syntax to the mapping contract."""
    side = (join.args.get("side") or "").upper()
    kind = (join.args.get("kind") or "").upper()

    if kind == "CROSS" or join.args.get("on") is None:
        return None
    if side == "LEFT":
        return "left"
    if not side and kind in {"", "INNER"}:
        return "inner"
    return f"unsupported:{side or kind}"


def _join_field_pair(
    join: exp.Join,
    qualifier_models: dict[str, str],
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Resolve one exact qualified field equality from a SQL JOIN."""
    condition = join.args.get("on")
    while isinstance(condition, exp.Paren):
        condition = condition.this
    if not isinstance(condition, exp.EQ):
        return None
    if not isinstance(condition.this, exp.Column):
        return None
    if not isinstance(condition.expression, exp.Column):
        return None

    references: list[tuple[str, str]] = []
    for column in (condition.this, condition.expression):
        if not column.table:
            return None
        model = qualifier_models.get(column.table.casefold())
        if model is None:
            return None
        references.append((model, column.name.casefold()))

    return _canonical_field_pair(references[0], references[1])


def _join_field_pairs(
    join: exp.Join,
    qualifier_models: dict[str, str],
) -> frozenset[tuple[tuple[str, str], tuple[str, str]]] | None:
    """Resolve qualified equalities joined exclusively with AND."""
    condition = join.args.get("on")
    if condition is None:
        return None

    comparisons: list[exp.Expression] = []

    def collect(expression: exp.Expression) -> None:
        while isinstance(expression, exp.Paren):
            expression = expression.this
        if isinstance(expression, exp.And):
            collect(expression.this)
            collect(expression.expression)
        else:
            comparisons.append(expression)

    collect(condition)
    pairs = []
    for comparison in comparisons:
        if not isinstance(comparison, exp.EQ):
            return None
        if not isinstance(comparison.this, exp.Column):
            return None
        if not isinstance(comparison.expression, exp.Column):
            return None

        references: list[tuple[str, str]] = []
        for column in (comparison.this, comparison.expression):
            if not column.table:
                return None
            model = qualifier_models.get(column.table.casefold())
            if model is None:
                return None
            references.append((model, column.name.casefold()))
        pairs.append(_canonical_field_pair(references[0], references[1]))

    return frozenset(pairs)


def _mapping_join_signature(
    mapping: FieldMapping,
) -> frozenset[tuple[tuple[str, str], tuple[str, str]]]:
    """Build the conventional source-to-mapping-table lookup equalities."""
    mapping_table = mapping.mapping_table_name
    if mapping_table is None:
        return frozenset()
    return frozenset(
        _canonical_field_pair(
            (reference.model.casefold(), reference.field.casefold()),
            (mapping_table.casefold(), reference.field.casefold()),
        )
        for reference in mapping.source_fields
    )


def _validate_source_relations_and_joins(
    statement: exp.Select,
    source_models: list[str],
    declared_joins: list[SourceJoin],
    field_mappings: list[FieldMapping],
) -> list[str]:
    """Validate physical relations and joins against the mapping contract."""
    errors: list[str] = []
    source_model_names = {model.casefold() for model in source_models}
    mapping_table_names = {
        mapping.mapping_table_name.casefold()
        for mapping in field_mappings
        if mapping.mapping_table_name
    }
    allowed_models = source_model_names | mapping_table_names
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in statement.ctes
        if cte.alias_or_name
    }
    physical_tables = [
        table
        for table in statement.find_all(exp.Table)
        if table.name.casefold() not in cte_names
    ]
    referenced_models = {table.name.casefold() for table in physical_tables}

    undeclared_models = sorted(referenced_models - allowed_models)
    if undeclared_models:
        errors.append(
            "SQL references undeclared source models: "
            + ", ".join(undeclared_models)
        )

    missing_source_models = sorted(source_model_names - referenced_models)
    if missing_source_models:
        errors.append(
            "SQL does not reference declared source models: "
            + ", ".join(missing_source_models)
        )

    missing_mapping_tables = sorted(
        mapping_table_names - referenced_models
    )
    if missing_mapping_tables:
        errors.append(
            "SQL does not reference declared mapping tables: "
            + ", ".join(missing_mapping_tables)
        )

    qualifier_models: dict[str, str] = {}
    for table in physical_tables:
        model = table.name.casefold()
        qualifier_models[model] = model
        if table.alias:
            qualifier_models[table.alias.casefold()] = model

    unmatched_declared = list(enumerate(declared_joins))
    unmatched_mapping_joins = [
        mapping
        for mapping in field_mappings
        if mapping.mapping_table_name
    ]
    for sql_join in statement.find_all(exp.Join):
        join_type = _actual_join_type(sql_join)
        if join_type is None:
            errors.append("cross joins and joins without ON are not allowed")
            continue
        if join_type.startswith("unsupported:"):
            errors.append(
                "unsupported SQL join type: "
                + join_type.removeprefix("unsupported:")
            )
            continue

        if not isinstance(sql_join.this, exp.Table):
            errors.append("joined relations must be declared source models")
            continue

        right_model = sql_join.this.name.casefold()
        if right_model not in allowed_models:
            errors.append(
                f"join references undeclared source model: {right_model}"
            )
            continue

        if right_model in mapping_table_names:
            field_pairs = _join_field_pairs(sql_join, qualifier_models)
            matched_position = None
            for position, mapping in enumerate(unmatched_mapping_joins):
                if mapping.mapping_table_name.casefold() != right_model:
                    continue
                if join_type != "left":
                    continue
                if field_pairs != _mapping_join_signature(mapping):
                    continue
                matched_position = position
                break

            if matched_position is None:
                errors.append(
                    "SQL contains invalid mapping-table join: "
                    f"{sql_join.sql()}"
                )
            else:
                unmatched_mapping_joins.pop(matched_position)
            continue

        field_pair = _join_field_pair(sql_join, qualifier_models)
        if field_pair is None:
            errors.append(
                "join condition must be one qualified source-field equality"
            )
            continue

        matched_position = None
        for position, (_, declared_join) in enumerate(unmatched_declared):
            declared_type, declared_pair, declared_right = (
                _declared_join_signature(declared_join)
            )
            if join_type != declared_type or field_pair != declared_pair:
                continue
            if join_type == "left" and right_model != declared_right:
                continue
            matched_position = position
            break

        if matched_position is None:
            errors.append(f"SQL contains undeclared join: {sql_join.sql()}")
        else:
            unmatched_declared.pop(matched_position)

    for _, declared_join in unmatched_declared:
        errors.append(
            "SQL is missing declared "
            f"{declared_join.join_type} join: "
            f"{declared_join.left.model}.{declared_join.left.field} = "
            f"{declared_join.right.model}.{declared_join.right.field}"
        )

    for mapping in unmatched_mapping_joins:
        expected_equalities = " AND ".join(
            f"{reference.model}.{reference.field} = "
            f"{mapping.mapping_table_name}.{reference.field}"
            for reference in mapping.source_fields
        )
        errors.append(
            "SQL is missing declared mapping-table left join for "
            f"{mapping.target_field}: {expected_equalities}"
        )

    return errors


def validate_sql(
    sql: str,
    dialect: str,
    expected_fields: list[str],
    output_format: str = "sql",
    field_mappings: list[FieldMapping] | None = None,
    target_fields: list[TargetField] | None = None,
    source_models: list[str] | None = None,
    declared_joins: list[SourceJoin] | None = None,
) -> SqlValidationResult:
    """Validate SQL syntax, target coverage and optional mapping lineage."""
    candidate = _replace_dbt_references(sql) if output_format == "dbt" else sql
    errors: list[str] = []

    if output_format == "dbt" and ("{{" in candidate or "{%" in candidate):
        errors.append("unsupported dbt/Jinja expression remains after preprocessing")

    try:
        statements = parse(candidate, read=dialect)
    except (ParseError, ValueError) as exc:
        return SqlValidationResult(False, (f"invalid {dialect} SQL: {exc}",))

    if len(statements) != 1:
        errors.append(f"expected one SQL statement, found {len(statements)}")
        return SqlValidationResult(False, tuple(errors))

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        errors.append("output must be one SELECT query")
        return SqlValidationResult(False, tuple(errors))

    actual_fields = statement.named_selects
    if "*" in actual_fields:
        errors.append("SELECT * is not allowed; target columns must be explicit")
    elif actual_fields != expected_fields:
        missing = [field for field in expected_fields if field not in actual_fields]
        extra = [field for field in actual_fields if field not in expected_fields]
        if missing:
            errors.append("missing target fields: " + ", ".join(missing))
        if extra:
            errors.append("unexpected target fields: " + ", ".join(extra))
        if not missing and not extra:
            errors.append("target fields are not in target-schema order")

    if field_mappings is not None:
        errors.extend(
            _validate_mapping_expressions(
                statement,
                expected_fields,
                field_mappings,
                target_fields,
                dialect,
            )
        )

    if source_models is not None and declared_joins is not None:
        errors.extend(
            _validate_source_relations_and_joins(
                statement,
                source_models,
                declared_joins,
                field_mappings or [],
            )
        )

    return SqlValidationResult(not errors, tuple(errors))
