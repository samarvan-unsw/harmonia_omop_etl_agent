"""Normalize supported schema documents into strict Harmonia source YAML."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .contracts import SourceSchemaDocument
from .yaml_loader import load_yaml


ImportFormat = Literal["harmonia", "dbt", "dbt_source", "generic_yaml", "generic_json"]
ImportConfidence = Literal["high", "medium", "low"]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF = re.compile(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)")
_SOURCE = re.compile(
    r"source\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
)


class SourceSchemaImportError(ValueError):
    """Raised when a schema file cannot be safely normalized."""


class ImportEvidence(BaseModel):
    """Trace one normalized decision back to its input location."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=500)
    source_path: str = Field(min_length=1, max_length=500)
    method: str = Field(min_length=1, max_length=100)
    confidence: ImportConfidence


class NormalizedSourceSchema(BaseModel):
    """One normalized model plus a bounded conversion report."""

    model_config = ConfigDict(extra="forbid")

    source_file_name: str
    file_name: str
    content: str
    detected_format: ImportFormat
    model_name: str
    field_count: int
    primary_key_count: int
    foreign_key_count: int
    semantic_field_count: int
    normalized_sha256: str
    evidence: list[ImportEvidence] = Field(default_factory=list, max_length=1_000)
    ignored_paths: list[str] = Field(default_factory=list, max_length=500)
    warnings: list[str] = Field(default_factory=list, max_length=500)


class SourceSchemaImportResult(BaseModel):
    """Normalized documents returned by the deterministic import boundary."""

    model_config = ConfigDict(extra="forbid")

    documents: list[NormalizedSourceSchema] = Field(min_length=1, max_length=500)
    source_file_count: int
    model_count: int
    field_count: int
    warnings: list[str] = Field(default_factory=list, max_length=500)


class _NormalizedModel:
    def __init__(
        self,
        *,
        source_file_name: str,
        detected_format: ImportFormat,
        source_path: str,
        model: dict[str, object],
        evidence: list[ImportEvidence],
        ignored_paths: list[str],
        warnings: list[str],
    ) -> None:
        self.source_file_name = source_file_name
        self.detected_format = detected_format
        self.source_path = source_path
        self.model = model
        self.evidence = evidence
        self.ignored_paths = ignored_paths
        self.warnings = warnings


def normalize_source_schema_files(
    files: list[tuple[str, str]],
) -> SourceSchemaImportResult:
    """Normalize bounded YAML/JSON files without invoking an AI provider."""

    if not files:
        raise SourceSchemaImportError("Choose at least one source-schema file.")
    models: list[_NormalizedModel] = []
    for file_name, content in files:
        models.extend(_normalize_file(file_name, content))
    if not models:
        raise SourceSchemaImportError("No source models were found.")
    if len(models) > 500:
        raise SourceSchemaImportError(
            "The selected files contain more than 500 source models."
        )

    by_name: dict[str, _NormalizedModel] = {}
    for item in models:
        name = str(item.model["name"])
        if name in by_name:
            raise SourceSchemaImportError(
                f"Source model is declared more than once: {name}"
            )
        by_name[name] = item
    _apply_incoming_references(by_name)

    documents: list[NormalizedSourceSchema] = []
    for item in sorted(models, key=lambda value: str(value.model["name"])):
        document = SourceSchemaDocument.model_validate(
            {"version": 2, "models": [item.model]}
        )
        content = yaml.safe_dump(
            document.model_dump(
                mode="json",
                exclude_defaults=True,
                exclude_none=True,
            ),
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )
        model = document.models[0]
        documents.append(
            NormalizedSourceSchema(
                source_file_name=item.source_file_name,
                file_name=f"{model.name}.yml",
                content=content,
                detected_format=item.detected_format,
                model_name=model.name,
                field_count=len(model.columns),
                primary_key_count=sum(column.primary_key for column in model.columns),
                foreign_key_count=sum(
                    column.foreign_key is not None for column in model.columns
                ),
                semantic_field_count=sum(
                    column.semantic is not None for column in model.columns
                ),
                normalized_sha256=hashlib.sha256(content.encode()).hexdigest(),
                evidence=item.evidence[:1_000],
                ignored_paths=sorted(set(item.ignored_paths))[:500],
                warnings=sorted(set(item.warnings))[:500],
            )
        )
    return SourceSchemaImportResult(
        documents=documents,
        source_file_count=len(files),
        model_count=len(documents),
        field_count=sum(document.field_count for document in documents),
        warnings=[],
    )


def _normalize_file(file_name: str, content: str) -> list[_NormalizedModel]:
    try:
        value = load_yaml(content)
    except yaml.YAMLError as error:
        raise SourceSchemaImportError(f"{file_name} is not valid YAML or JSON.") from error
    if not isinstance(value, Mapping):
        raise SourceSchemaImportError(f"{file_name} must contain an object at its root.")
    detected_format = _detect_format(file_name, value)
    raw_models = _extract_models(value, file_name)
    return [
        _normalize_model(
            raw,
            source_file_name=file_name,
            detected_format=detected_format,
            source_path=path,
        )
        for path, raw in raw_models
    ]


def _detect_format(file_name: str, value: Mapping[object, object]) -> ImportFormat:
    try:
        SourceSchemaDocument.model_validate(value)
    except ValueError:
        pass
    else:
        return "harmonia"
    if isinstance(value.get("sources"), list):
        return "dbt_source"
    if isinstance(value.get("models"), list):
        return "dbt"
    return "generic_json" if file_name.lower().endswith(".json") else "generic_yaml"


def _extract_models(
    value: Mapping[object, object],
    file_name: str,
) -> list[tuple[str, Mapping[object, object]]]:
    result: list[tuple[str, Mapping[object, object]]] = []
    for key in ("models", "tables"):
        items = value.get(key)
        if isinstance(items, list):
            result.extend(
                (f"{key}[{index}]", item)
                for index, item in enumerate(items)
                if isinstance(item, Mapping)
            )
            if result:
                return result
    sources = value.get("sources")
    if isinstance(sources, list):
        for source_index, source in enumerate(sources):
            if not isinstance(source, Mapping):
                continue
            tables = source.get("tables")
            if not isinstance(tables, list):
                continue
            result.extend(
                (f"sources[{source_index}].tables[{table_index}]", table)
                for table_index, table in enumerate(tables)
                if isinstance(table, Mapping)
            )
        if result:
            return result
    if "name" in value and (
        isinstance(value.get("columns"), list)
        or isinstance(value.get("fields"), list)
    ):
        return [("root", value)]
    raise SourceSchemaImportError(
        f"{file_name} does not contain recognised models, tables, sources or fields."
    )


def _normalize_model(
    raw: Mapping[object, object],
    *,
    source_file_name: str,
    detected_format: ImportFormat,
    source_path: str,
) -> _NormalizedModel:
    evidence: list[ImportEvidence] = []
    ignored: list[str] = []
    warnings: list[str] = []
    name = _identifier(raw.get("name"), f"{source_path}.name", warnings)
    description = _text(raw.get("description") or raw.get("comment"), 10_000)
    semantic_raw = _semantic(raw)
    columns_raw = raw.get("columns")
    if not isinstance(columns_raw, list):
        columns_raw = raw.get("fields")
    if not isinstance(columns_raw, list):
        raise SourceSchemaImportError(f"{source_path} has no column list.")

    model_primary_keys = _key_columns(semantic_raw.get("primary_key"))
    column_catalog = _column_catalog(
        semantic_raw.get("column_catalog"),
        source_path=source_path,
        warnings=warnings,
    )
    semantic = _model_semantic(
        semantic_raw,
        source_path=source_path,
        warnings=warnings,
    )
    columns: list[dict[str, object]] = []
    candidate_keys = list(semantic.get("alternate_keys", [])) if semantic else []
    for index, column_raw in enumerate(columns_raw):
        if not isinstance(column_raw, Mapping):
            raise SourceSchemaImportError(
                f"{source_path}.columns[{index}] must be an object."
            )
        column_path = f"{source_path}.columns[{index}]"
        column, unique_not_null = _normalize_column(
            column_raw,
            model_name=name,
            source_path=column_path,
            model_primary_keys=model_primary_keys,
            catalog_semantic=column_catalog.get(str(column_raw.get("name"))),
            evidence=evidence,
            ignored=ignored,
            warnings=warnings,
        )
        columns.append(column)
        if unique_not_null and not column["primary_key"]:
            candidate_keys.append(
                {
                    "columns": [column["name"]],
                    "meaning": "Candidate key inferred from dbt unique and not-null tests.",
                }
            )
    if not columns:
        warnings.append(f"{name} contains no source fields.")
    if semantic is None and candidate_keys:
        semantic = {}
    if semantic is not None and candidate_keys:
        semantic["alternate_keys"] = _deduplicate_keys(candidate_keys)

    if "tests" in raw:
        ignored.append(f"{source_path}.tests")
    meta = raw.get("meta")
    if isinstance(meta, Mapping):
        for key in meta:
            if key != "semantic":
                ignored.append(f"{source_path}.meta.{key}")
    model = {
        "name": name,
        "description": description,
        "semantic": semantic,
        "columns": columns,
    }
    return _NormalizedModel(
        source_file_name=source_file_name,
        detected_format=detected_format,
        source_path=source_path,
        model=model,
        evidence=evidence,
        ignored_paths=ignored,
        warnings=warnings,
    )


def _normalize_column(
    raw: Mapping[object, object],
    *,
    model_name: str,
    source_path: str,
    model_primary_keys: set[str],
    catalog_semantic: tuple[Mapping[object, object], str] | None,
    evidence: list[ImportEvidence],
    ignored: list[str],
    warnings: list[str],
) -> tuple[dict[str, object], bool]:
    name = _identifier(raw.get("name"), f"{source_path}.name", warnings)
    semantic_raw = dict(catalog_semantic[0]) if catalog_semantic else {}
    semantic_raw.update(_semantic(raw))
    semantic_source_path = (
        catalog_semantic[1]
        if catalog_semantic and not _semantic(raw)
        else f"{source_path}.meta.semantic"
    )
    description = _text(raw.get("description") or raw.get("comment"), 10_000)

    data_type, type_path = _first_text(
        (
            (raw.get("data_type"), f"{source_path}.data_type"),
            (raw.get("datatype"), f"{source_path}.datatype"),
            (raw.get("type"), f"{source_path}.type"),
            (semantic_raw.get("data_type"), f"{source_path}.meta.semantic.data_type"),
        ),
        maximum=255,
    )
    if data_type and type_path:
        evidence.append(
            _evidence(
                f"models.{model_name}.columns.{name}.data_type",
                type_path,
                "known_alias",
                "high" if type_path.endswith(".data_type") else "medium",
            )
        )

    explicit_primary, primary_path = _primary_key(raw, semantic_raw, source_path)
    model_primary = name in model_primary_keys
    primary_key = explicit_primary if explicit_primary is not None else model_primary
    if explicit_primary is False and model_primary:
        warnings.append(
            f"{model_name}.{name} explicitly disables primary_key but model metadata lists it."
        )
    if primary_path:
        evidence.append(
            _evidence(
                f"models.{model_name}.columns.{name}.primary_key",
                primary_path,
                "explicit_metadata",
                "high",
            )
        )
    elif model_primary:
        evidence.append(
            _evidence(
                f"models.{model_name}.columns.{name}.primary_key",
                f"{source_path.rsplit('.columns[', 1)[0]}.meta.semantic.primary_key.columns",
                "model_key_declaration",
                "high",
            )
        )

    foreign_key, foreign_path = _foreign_key(raw, semantic_raw, source_path, warnings)
    tests = _combined_tests(raw)
    relationship_key = _relationship_foreign_key(tests, source_path, warnings)
    if foreign_key is None and relationship_key is not None:
        foreign_key, foreign_path = relationship_key
    elif foreign_key and relationship_key and foreign_key != relationship_key[0]:
        warnings.append(f"{model_name}.{name} has conflicting foreign-key declarations.")
    if foreign_key and foreign_path:
        evidence.append(
            _evidence(
                f"models.{model_name}.columns.{name}.foreign_key",
                foreign_path,
                "relationship_declaration",
                "high",
            )
        )

    semantic = _column_semantic(
        semantic_raw,
        source_path=source_path,
        ignored=ignored,
        warnings=warnings,
    )
    if semantic:
        evidence.extend(
            _evidence(
                f"models.{model_name}.columns.{name}.semantic.{key}",
                f"{semantic_source_path}.{key}",
                "semantic_catalogue",
                "medium",
            )
            for key in semantic
        )
    unique_not_null = _has_test(tests, "unique") and _has_test(tests, "not_null")
    if "tests" in raw:
        ignored.append(f"{source_path}.tests (non-relationship test configuration)")
    if "data_tests" in raw:
        ignored.append(
            f"{source_path}.data_tests (non-relationship test configuration)"
        )
    meta = raw.get("meta")
    if isinstance(meta, Mapping):
        for key in meta:
            if key not in {"primary_key", "semantic"}:
                ignored.append(f"{source_path}.meta.{key}")
    return (
        {
            "name": name,
            "data_type": data_type,
            "description": description,
            "primary_key": primary_key,
            "foreign_key": foreign_key,
            "semantic": semantic,
        },
        unique_not_null,
    )


def _semantic(raw: Mapping[object, object]) -> Mapping[object, object]:
    nested: dict[object, object] = {}
    meta = raw.get("meta")
    if isinstance(meta, Mapping) and isinstance(meta.get("semantic"), Mapping):
        nested.update(meta["semantic"])
    if isinstance(raw.get("semantic"), Mapping):
        nested.update(raw["semantic"])
    return nested


def _model_semantic(
    raw: Mapping[object, object],
    *,
    source_path: str,
    warnings: list[str],
) -> dict[str, object] | None:
    values: dict[str, object] = {}
    for key, maximum in (
        ("entity", 255),
        ("subject_area", 255),
        ("grain", 1_000),
    ):
        value = _text(raw.get(key), maximum)
        if value:
            values[key] = value
    if raw.get("source_model") is not None:
        values["source_model"] = _identifier(
            raw.get("source_model"),
            f"{source_path}.meta.semantic.source_model",
            warnings,
        )
    alternate_keys = _alternate_keys(raw.get("alternate_keys"), warnings)
    if alternate_keys:
        values["alternate_keys"] = alternate_keys
    referenced_by = _incoming_references(raw.get("referenced_by"), warnings)
    if referenced_by:
        values["referenced_by"] = referenced_by
    return values or None


def _column_semantic(
    raw: Mapping[object, object],
    *,
    source_path: str,
    ignored: list[str],
    warnings: list[str],
) -> dict[str, object] | None:
    values: dict[str, object] = {}
    for key in (
        "role",
        "semantic_type",
        "value_type",
        "identifier_scope",
        "unit",
        "sensitivity",
    ):
        value = _text(raw.get(key), 255)
        if value:
            values[key] = value
    for key in ("filterable", "groupable", "aggregatable"):
        if isinstance(raw.get(key), bool):
            values[key] = raw[key]
    counting = raw.get("counting")
    if isinstance(counting, Mapping):
        default_aggregation = _text(counting.get("default"), 255)
        if default_aggregation:
            values["default_aggregation"] = default_aggregation
    for key, maximum in (("synonyms", 100), ("allowed_values", 500)):
        items = _text_list(raw.get(key), maximum)
        if items:
            values[key] = items
    terminology = raw.get("terminology")
    if isinstance(terminology, Mapping):
        normalized = {
            key: value
            for key in ("status", "vocabulary", "version", "representation")
            if (value := _text(terminology.get(key), 255))
        }
        if normalized:
            values["terminology"] = normalized
    lineage = raw.get("source")
    if isinstance(lineage, Mapping):
        normalized_lineage: dict[str, object] = {}
        for key in ("model", "field"):
            lineage_value = (
                lineage.get("column")
                if key == "field" and lineage.get("field") is None
                else lineage.get(key)
            )
            if lineage_value is not None:
                normalized_lineage[key] = _identifier(
                    lineage_value,
                    f"{source_path}.meta.semantic.source.{key}",
                    warnings,
                )
        for key in ("expression", "transformation"):
            value = _text(lineage.get(key), 5_000)
            if value:
                normalized_lineage[key] = value
        if normalized_lineage:
            values["source"] = normalized_lineage
    known = {
        "aggregatable",
        "allowed_values",
        "counting",
        "data_type",
        "filterable",
        "foreign_key",
        "groupable",
        "identifier_scope",
        "primary_key",
        "role",
        "semantic_type",
        "sensitivity",
        "source",
        "synonyms",
        "terminology",
        "unit",
        "value_type",
    }
    ignored.extend(
        f"{source_path}.meta.semantic.{key}"
        for key in raw
        if key not in known and key != "name"
    )
    return values or None


def _column_catalog(
    value: object,
    *,
    source_path: str,
    warnings: list[str],
) -> dict[str, tuple[Mapping[object, object], str]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, tuple[Mapping[object, object], str]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = f"{source_path}.meta.semantic.column_catalog[{index}]"
        name = item.get("name")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            warnings.append(f"{path}.name is invalid and was ignored.")
            continue
        if name in result:
            warnings.append(f"{path}.name duplicates {name!r} and was ignored.")
            continue
        result[name] = (item, path)
    return result


def _primary_key(
    raw: Mapping[object, object],
    semantic: Mapping[object, object],
    source_path: str,
) -> tuple[bool | None, str | None]:
    for value, path in (
        (raw.get("primary_key"), f"{source_path}.primary_key"),
        (raw.get("is_primary_key"), f"{source_path}.is_primary_key"),
        (raw.get("pk"), f"{source_path}.pk"),
    ):
        if isinstance(value, bool):
            return value, path
    meta = raw.get("meta")
    if isinstance(meta, Mapping) and isinstance(meta.get("primary_key"), bool):
        return bool(meta["primary_key"]), f"{source_path}.meta.primary_key"
    if isinstance(semantic.get("primary_key"), bool):
        return bool(semantic["primary_key"]), f"{source_path}.meta.semantic.primary_key"
    return None, None


def _foreign_key(
    raw: Mapping[object, object],
    semantic: Mapping[object, object],
    source_path: str,
    warnings: list[str],
) -> tuple[dict[str, str] | None, str | None]:
    for value, path in (
        (raw.get("foreign_key"), f"{source_path}.foreign_key"),
        (semantic.get("foreign_key"), f"{source_path}.meta.semantic.foreign_key"),
    ):
        if not isinstance(value, Mapping):
            continue
        model_value = value.get("model") or value.get("table")
        field_value = value.get("field") or value.get("column")
        if model_value is None or field_value is None:
            warnings.append(f"{path} is incomplete and was ignored.")
            continue
        return (
            {
                "model": _identifier(model_value, f"{path}.model", warnings),
                "field": _identifier(field_value, f"{path}.field", warnings),
            },
            path,
        )
    return None, None


def _relationship_foreign_key(
    tests: object,
    source_path: str,
    warnings: list[str],
) -> tuple[dict[str, str], str] | None:
    if not isinstance(tests, list):
        return None
    found: list[tuple[dict[str, str], str]] = []
    for index, test in enumerate(tests):
        if not isinstance(test, Mapping) or not isinstance(
            test.get("relationships"), Mapping
        ):
            continue
        config = test["relationships"]
        arguments = config.get("arguments")
        if isinstance(arguments, Mapping):
            config = arguments
        target = config.get("to")
        field = config.get("field")
        if not isinstance(target, str) or field is None:
            warnings.append(
                f"{source_path}.tests[{index}].relationships is incomplete."
            )
            continue
        match = _REF.search(target) or _SOURCE.search(target)
        if not match:
            warnings.append(
                f"{source_path}.tests[{index}].relationships.to is unsupported."
            )
            continue
        found.append(
            (
                {
                    "model": _identifier(
                        match.group(1),
                        f"{source_path}.tests[{index}].relationships.to",
                        warnings,
                    ),
                    "field": _identifier(
                        field,
                        f"{source_path}.tests[{index}].relationships.field",
                        warnings,
                    ),
                },
                f"{source_path}.tests[{index}].relationships",
            )
        )
    if len({(item[0]["model"], item[0]["field"]) for item in found}) > 1:
        warnings.append(f"{source_path} has multiple foreign-key relationship tests.")
        return None
    return found[0] if found else None


def _apply_incoming_references(models: dict[str, _NormalizedModel]) -> None:
    for parent_name, parent in models.items():
        semantic = parent.model.get("semantic")
        if not isinstance(semantic, Mapping):
            continue
        references = semantic.get("referenced_by")
        if not isinstance(references, list):
            continue
        primary_fields = [
            str(column["name"])
            for column in parent.model["columns"]
            if isinstance(column, Mapping) and column.get("primary_key") is True
        ]
        for reference in references:
            if not isinstance(reference, Mapping):
                continue
            child_name = str(reference["model"])
            child_field = str(reference["field"])
            child = models.get(child_name)
            if child is None:
                parent.warnings.append(
                    f"Incoming relationship to {child_name}.{child_field} was retained as a hint; the child model was not imported together."
                )
                continue
            child_column = next(
                (
                    column
                    for column in child.model["columns"]
                    if isinstance(column, dict) and column.get("name") == child_field
                ),
                None,
            )
            parent_field = (
                primary_fields[0]
                if len(primary_fields) == 1
                else child_field if child_field in primary_fields else None
            )
            if child_column is None or parent_field is None:
                child.warnings.append(
                    f"Incoming relationship from {parent_name} could not resolve {child_name}.{child_field}."
                )
                continue
            proposed = {"model": parent_name, "field": parent_field}
            existing = child_column.get("foreign_key")
            if existing is None:
                child_column["foreign_key"] = proposed
                child.evidence.append(
                    _evidence(
                        f"models.{child_name}.columns.{child_field}.foreign_key",
                        f"models.{parent_name}.meta.semantic.referenced_by",
                        "inverse_relationship",
                        "medium",
                    )
                )
            elif existing != proposed:
                child.warnings.append(
                    f"{child_name}.{child_field} conflicts with incoming relationship from {parent_name}."
                )


def _incoming_references(value: object, warnings: list[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        model = item.get("model")
        field = item.get("field") or item.get("column")
        if model is None or field is None:
            warnings.append(f"referenced_by[{index}] is incomplete and was ignored.")
            continue
        result.append(
            {
                "model": _identifier(model, f"referenced_by[{index}].model", warnings),
                "field": _identifier(field, f"referenced_by[{index}].field", warnings),
                "relationship": _text(item.get("relationship"), 255) or None,
            }
        )
    return result


def _alternate_keys(value: object, warnings: list[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        columns = item.get("columns")
        if not isinstance(columns, list) or not columns:
            warnings.append(f"alternate_keys[{index}] is incomplete and was ignored.")
            continue
        result.append(
            {
                "columns": [
                    _identifier(column, f"alternate_keys[{index}].columns", warnings)
                    for column in columns
                ],
                "meaning": _text(item.get("meaning"), 1_000),
            }
        )
    return result


def _key_columns(value: object) -> set[str]:
    if isinstance(value, Mapping):
        value = value.get("columns")
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if isinstance(item, str) and _IDENTIFIER.fullmatch(item)}


def _deduplicate_keys(keys: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for key in keys:
        columns = tuple(str(item) for item in key["columns"])
        if columns not in seen:
            result.append(key)
            seen.add(columns)
    return result


def _has_test(value: object, name: str) -> bool:
    if not isinstance(value, list):
        return False
    return any(item == name or (isinstance(item, Mapping) and name in item) for item in value)


def _combined_tests(raw: Mapping[object, object]) -> list[object]:
    result: list[object] = []
    for key in ("tests", "data_tests"):
        value = raw.get(key)
        if isinstance(value, list):
            result.extend(value)
    return result


def _identifier(value: object, path: str, warnings: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceSchemaImportError(f"{path} must contain a source identifier.")
    candidate = value.strip()
    if _IDENTIFIER.fullmatch(candidate):
        return candidate
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", candidate).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"source_{normalized or 'item'}"
    if not _IDENTIFIER.fullmatch(normalized):
        raise SourceSchemaImportError(f"{path} cannot form a safe identifier.")
    warnings.append(f"Renamed {path} from {candidate!r} to {normalized!r}.")
    return normalized


def _text(value: object, maximum: int) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()[:maximum]
    return ""


def _text_list(value: object, maximum_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:maximum_items]:
        text = _text(item, 1_000)
        if text and text not in result:
            result.append(text)
    return result


def _first_text(
    candidates: tuple[tuple[object, str], ...],
    *,
    maximum: int,
) -> tuple[str | None, str | None]:
    for value, path in candidates:
        text = _text(value, maximum)
        if text:
            return text, path
    return None, None


def _evidence(
    target: str,
    source_path: str,
    method: str,
    confidence: ImportConfidence,
) -> ImportEvidence:
    return ImportEvidence(
        target=target,
        source_path=source_path,
        method=method,
        confidence=confidence,
    )
