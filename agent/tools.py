import re
from pathlib import Path
from typing import Any, Callable


# Generated SQL files are written directly into this directory.
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = (ROOT_DIR / "output").resolve()

# Only simple top-level SQL filenames are allowed.
SQL_FILENAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.sql$")
CANDIDATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _resolve_sql_file(path: str) -> Path:
    """Resolve one SQL filename while preventing directories and traversal."""
    if not SQL_FILENAME_PATTERN.fullmatch(path):
        raise ValueError(
            "Path must be a top-level SQL filename, for example person.sql"
        )

    target = (OUTPUT_DIR / path).resolve()
    if target.parent != OUTPUT_DIR:
        raise ValueError(f"Path escapes output directory: {path}")
    return target


def _resolve_candidate_file(path: str, candidate_id: str | None) -> Path:
    """Resolve an internal candidate file for one generation run."""
    target = _resolve_sql_file(path)
    safe_candidate_id = candidate_id or "default"
    if not CANDIDATE_ID_PATTERN.fullmatch(safe_candidate_id):
        raise ValueError("Invalid internal candidate identifier")

    return OUTPUT_DIR / f".{target.stem}.{safe_candidate_id}.candidate"


def read_file(path: str, candidate_id: str | None = None) -> str:
    """Read the current candidate, falling back to the promoted SQL file."""
    candidate = _resolve_candidate_file(path, candidate_id)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")

    target = _resolve_sql_file(path)
    if not target.is_file():
        return f"ERROR: file does not exist: {path}"
    return target.read_text(encoding="utf-8")


def write_file(
    path: str,
    content: str,
    candidate_id: str | None = None,
) -> str:
    """Write SQL to an isolated candidate without changing the final file."""
    if not content.strip():
        raise ValueError("SQL content cannot be empty")

    candidate = _resolve_candidate_file(path, candidate_id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidate.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to candidate for {path}"


def promote_file(path: str, candidate_id: str | None = None) -> None:
    """Atomically replace the final SQL file with its validated candidate."""
    candidate = _resolve_candidate_file(path, candidate_id)
    if not candidate.is_file():
        raise FileNotFoundError(f"Candidate file does not exist: {path}")

    target = _resolve_sql_file(path)
    candidate.replace(target)


def discard_candidate(path: str, candidate_id: str | None = None) -> None:
    """Remove an unpromoted candidate while preserving the final SQL file."""
    candidate = _resolve_candidate_file(path, candidate_id)
    candidate.unlink(missing_ok=True)


# Tool definitions supplied to the model.
TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read a generated SQL file from the output directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*\.sql$",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Write a generated SQL file into the output directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*\.sql$",
                },
                "content": {
                    "type": "string",
                    "minLength": 1,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
]


def dispatch(tool_call: Any, candidate_id: str | None = None) -> str:
    """Dispatch a provider tool call to an approved file operation."""
    handlers: dict[str, Callable[..., str]] = {
        "read_file": read_file,
        "write_file": write_file,
    }

    handler = handlers.get(tool_call.name)
    if handler is None:
        return f"ERROR: unknown tool: {tool_call.name}"
    if not isinstance(tool_call.arguments, dict):
        return f"ERROR: invalid arguments for tool: {tool_call.name}"

    try:
        return handler(**tool_call.arguments, candidate_id=candidate_id)
    except (OSError, TypeError, ValueError) as exc:
        return f"ERROR: {exc}"
