# Filesystem tools — read, write, edit
#
# Core file operations with path normalization, binary refusal,
# token-aware truncation, and size caps.
#
# Each tool is registered via build_tool() with full metadata:
#   - Input schema (JSON)
#   - Semantic validation hooks
#   - Safety flags (read_only / destructive)
#   - Categories ("core")

from __future__ import annotations

import base64
import binascii
import difflib
import logging
import os
from pathlib import Path
from typing import Any

from api.tools.registry import (
    ToolContract,
    build_tool,
    ValidationResult,
    CATEGORY_CORE,
    PermissionDecision,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

# Maximum file size to read (5 MB)
MAX_READ_BYTES = 5 * 1024 * 1024

# Maximum characters to return (token-aware truncation)
# ~12500 chars ≈ ~3125 tokens (4:1 ratio)
MAX_READ_CHARS = 12_500

# Maximum file size to write (1 MB)
MAX_WRITE_BYTES = 1 * 1024 * 1024

# Binary file signatures (magic bytes)
BINARY_SIGNATURES: list[bytes] = [
    b'\x00',  # null byte → likely binary
]

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def normalize_path(raw_path: str, *, workspace: str | None = None) -> str:
    """Normalize a file path: resolve ~, expand variables, make absolute.

    Args:
        raw_path: User-supplied path string.
        workspace: If set, treat relative paths as relative to workspace.

    Returns:
        Absolute normalized path string.
    """
    p = Path(raw_path).expanduser()
    if not p.is_absolute() and workspace:
        p = Path(workspace) / p
    return str(p.resolve())


def normalize_allowed_paths(
    allowed_paths: list[str] | None,
    *,
    workspace: str | None = None,
) -> list[str]:
    """Normalize configured allowed path roots for permission checks."""
    normalized: list[str] = []
    for raw_path in allowed_paths or []:
        if not raw_path:
            continue
        normalized.append(normalize_path(raw_path, workspace=workspace))
    return list(dict.fromkeys(normalized))


def is_path_within_scope(path: str, allowed_paths: list[str] | None) -> bool:
    """Return True when path is equal to or nested under an allowed root."""
    candidate = Path(path).resolve()
    for allowed in allowed_paths or []:
        allowed_path = Path(allowed).resolve()
        if candidate == allowed_path:
            return True
        try:
            candidate.relative_to(allowed_path)
            return True
        except ValueError:
            continue
    return False


def make_filesystem_permission_check(
    *,
    workspace: str | None = None,
    allowed_paths: list[str] | None = None,
):
    """Build a permission hook that constrains filesystem tools to allowed paths."""
    normalized_allowed_paths = normalize_allowed_paths(allowed_paths, workspace=workspace)

    def permission_check(*, input_data: dict[str, Any], **_: Any) -> PermissionDecision:
        raw_path = input_data.get("path", "")
        if not isinstance(raw_path, str) or not raw_path:
            return PermissionDecision.DENY
        if not normalized_allowed_paths:
            return PermissionDecision.ALLOW
        normalized_target = normalize_path(raw_path, workspace=workspace)
        if is_path_within_scope(normalized_target, normalized_allowed_paths):
            return PermissionDecision.ALLOW
        return PermissionDecision.DENY

    return permission_check


def is_binary_file(path: str, sample_size: int = 8192) -> bool:
    """Check if a file is binary by reading a sample of bytes.

    Returns True if null bytes or other binary signatures are found.
    """
    try:
        with open(path, "rb") as f:
            sample = f.read(sample_size)
        for sig in BINARY_SIGNATURES:
            if sig in sample:
                return True
        return False
    except (OSError, IOError):
        return False  # if we can't read it, assume not binary (will fail later)


def estimate_file_size(path: str) -> int | None:
    """Return file size in bytes, or None if file doesn't exist."""
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _display_path(path: str, *, workspace: str | None = None) -> str:
    try:
        target = Path(path).resolve()
        if workspace:
            workspace_path = Path(workspace).resolve()
            try:
                return target.relative_to(workspace_path).as_posix()
            except ValueError:
                pass
        return target.name or str(target)
    except OSError:
        return Path(path).name or path


def _build_diff_stat(before: str, after: str, *, path: str, workspace: str | None = None) -> str:
    added = 0
    removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return f"+{added} -{removed} {_display_path(path, workspace=workspace)}"


def _syntax_check(path: str, content: str) -> str:
    """Deterministic post-write verify (the detector pattern, for code). Cheap, stdlib only.

    The model can't claim a write 'succeeded' if it broke the file — determinism reports the
    truth, and the loop can re-ask with the concrete error instead of moving on blind.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".py", ".pyw"):
            compile(content, path, "exec")
        elif ext == ".json":
            import json as _json
            _json.loads(content)
        else:
            return ""  # only the languages we can verify with stdlib, no false confidence
    except SyntaxError as e:
        return f"\n[VERIFY] SYNTAX ERROR line {e.lineno}: {e.msg} — the bytes landed but the file does not parse. Fix it before continuing."
    except ValueError as e:
        return f"\n[VERIFY] JSON ERROR: {e} — the bytes landed but the file is not valid JSON. Fix it before continuing."
    return "\n[VERIFY] syntax OK"


def _closest_region_hint(content: str, anchor: str) -> str:
    """On an edit-anchor miss, point the model at the nearest real line so it self-corrects
    in one shot instead of guessing blind (the #1 way small models burn turns on edits)."""
    head = next((ln for ln in anchor.strip().splitlines() if ln.strip()), "")
    if not head:
        return ""
    lines = content.splitlines()
    best = difflib.get_close_matches(head.strip(), [ln.strip() for ln in lines], n=1, cutoff=0.5)
    if not best:
        return " No close match in the file — re-read it to get the exact current text."
    for i, ln in enumerate(lines):
        if ln.strip() == best[0]:
            return f" Closest existing line is {i + 1}: {ln.strip()[:100]!r} — match the file's exact text (indentation included)."
    return ""


def validate_list_dir_input(data: dict[str, Any], *, workspace: str | None = None) -> ValidationResult:
    raw_path = data.get("path", "")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ValidationResult(valid=False, errors=["path is required"])

    normalized = normalize_path(raw_path, workspace=workspace)
    if not os.path.exists(normalized):
        return ValidationResult(valid=False, errors=[f"Path not found: {normalized}"])
    if not os.path.isdir(normalized):
        return ValidationResult(valid=False, errors=[f"Path is not a directory: {normalized}"])

    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        return ValidationResult(valid=False, errors=["limit must be a positive integer"])

    return ValidationResult(valid=True)


def exec_list_dir(args: dict[str, Any], *, workspace: str | None = None) -> str:
    raw_path = args.get("path", "")
    limit = args.get("limit", 200)
    normalized = normalize_path(raw_path, workspace=workspace)

    try:
        entries = sorted(os.scandir(normalized), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
    except OSError as e:
        return f"Error listing directory: {e}"

    visible = entries[:limit]
    lines = [f"Directory: {normalized}"]
    for entry in visible:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"- {entry.name}{suffix}")

    if len(entries) > len(visible):
        lines.append(f"... TRUNCATED ({len(entries) - len(visible)} more entries). Re-run with a higher limit if needed.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Semantic validators
# ---------------------------------------------------------------------------


def validate_read_input(data: dict[str, Any], *, workspace: str | None = None) -> ValidationResult:
    """Semantic validation for 'read' tool beyond schema checks."""
    path_str = data.get("path", "")
    if not path_str:
        return ValidationResult(valid=False, errors=["Empty path"])

    normalized = normalize_path(path_str, workspace=workspace)

    # Check file exists
    if not os.path.exists(normalized):
        return ValidationResult(
            valid=False,
            errors=[f"File not found: {normalized}"],
        )

    # Check it's a file, not a directory
    if os.path.isdir(normalized):
        return ValidationResult(
            valid=False,
            errors=[f"Path is a directory, not a file: {normalized}"],
        )

    # Check file size
    size = estimate_file_size(normalized)
    if size is not None and size > MAX_READ_BYTES:
        return ValidationResult(
            valid=False,
            errors=[f"File too large ({size} bytes, max {MAX_READ_BYTES})"],
        )

    # Check binary
    if is_binary_file(normalized):
        return ValidationResult(
            valid=False,
            errors=[f"Binary file refused: {normalized}"],
        )

    return ValidationResult(valid=True)


def validate_write_input(data: dict[str, Any]) -> ValidationResult:
    """Semantic validation for 'write' tool beyond schema checks."""
    content, error = _resolve_write_content(data)
    if error:
        return ValidationResult(valid=False, errors=[error])

    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return ValidationResult(
            valid=False,
            errors=[
                f"Content too large "
                f"({len(content.encode('utf-8'))} bytes, max {MAX_WRITE_BYTES})"
            ],
        )
    return ValidationResult(valid=True)


def _resolve_write_content(data: dict[str, Any]) -> tuple[str, str | None]:
    has_content = "content" in data
    has_content_base64 = "content_base64" in data

    if has_content == has_content_base64:
        return "", "Provide exactly one of content or content_base64"

    if has_content:
        content = data.get("content")
        if not isinstance(content, str):
            return "", "content must be a string"
        return content, None

    encoded = data.get("content_base64")
    if not isinstance(encoded, str):
        return "", "content_base64 must be a string"

    collapsed = "".join(encoded.split())
    try:
        decoded_bytes = base64.b64decode(collapsed, validate=True)
    except (binascii.Error, ValueError):
        return "", "content_base64 must be valid base64"

    try:
        return decoded_bytes.decode("utf-8"), None
    except UnicodeDecodeError:
        return "", "content_base64 must decode to UTF-8 text"


def validate_edit_input(data: dict[str, Any]) -> ValidationResult:
    """Semantic validation for 'edit' tool beyond schema checks."""
    edits = data.get("edits", [])
    for edit in edits:
        new_text = edit.get("newText", "")
        anchors = [
            key
            for key in ("oldText", "insertAfter", "insertBefore")
            if isinstance(edit.get(key), str) and edit.get(key, "").strip()
        ]
        if len(anchors) != 1:
            return ValidationResult(
                valid=False,
                errors=["Each edit must provide exactly one anchor: oldText, insertAfter, or insertBefore"],
            )
        if not isinstance(new_text, str):
            return ValidationResult(valid=False, errors=["newText must be a string"])

    # Combined old+new size cap (50KB)
    combined = sum(
        len(str(e.get("oldText", "") or e.get("insertAfter", "") or e.get("insertBefore", "")).encode("utf-8"))
        + len(str(e.get("newText", "")).encode("utf-8"))
        for e in edits
    )
    if combined > 50 * 1024:
        return ValidationResult(
            valid=False,
            errors=[
                f"Combined old+new text too large "
                f"({combined} bytes, max 50KB)"
            ],
        )

    return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Execute functions
# ---------------------------------------------------------------------------


def exec_read(args: dict[str, Any], *, workspace: str | None = None) -> str:
    """Read file contents with truncation.

    Args:
        args: {"path": str, "offset": int (1-indexed, optional),
               "limit": int (optional)}
        workspace: Base path for relative paths.

    Returns:
        File content as string, truncated if needed.
    """
    raw_path = args["path"]
    offset = args.get("offset", 0)  # 0 means no offset (from start)
    limit = args.get("limit", None)  # None means use default max

    normalized = normalize_path(raw_path, workspace=workspace)

    try:
        with open(normalized, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except UnicodeDecodeError:
        return f"Error: Could not decode {normalized} as UTF-8"
    except OSError as e:
        return f"Error reading file: {e}"

    # Apply line-based offset/limit if specified
    lines = content.split("\n")
    if offset and offset > 0:
        offset_idx = offset - 1  # 1-indexed → 0-indexed
        if offset_idx >= len(lines):
            return (
                f"Warning: offset {offset} exceeds file length ({len(lines)} lines). "
                f"File: {normalized}"
            )
        lines = lines[offset_idx:]

    if limit is not None:
        lines = lines[:limit]

    content = "\n".join(lines)

    # Token-aware truncation
    if len(content) > MAX_READ_CHARS:
        truncated = content[:MAX_READ_CHARS]
        # Try to cut at a line boundary
        last_newline = truncated.rfind("\n")
        if last_newline > MAX_READ_CHARS * 0.8:
            truncated = truncated[:last_newline]
        total_lines = content.count("\n") + 1
        return (
            f"{truncated}\n\n"
            f"--- [TRUNCATED] {total_lines - truncated.count(chr(10)) - 1} more lines "
            f"(total: {total_lines}) ---\n"
            f"Use offset/limit to read remaining content."
        )

    return content


def exec_write(args: dict[str, Any], *, workspace: str | None = None) -> str:
    """Write content to a file. Creates parent directories if needed.

    Args:
        args: {"path": str, "content": str}
        workspace: Base path for relative paths.

    Returns:
        Confirmation message with path and byte count.
    """
    raw_path = args["path"]
    content, error = _resolve_write_content(args)
    if error:
        return f"Error writing file: {error}"

    normalized = normalize_path(raw_path, workspace=workspace)
    prior_content = ""
    if os.path.exists(normalized):
        try:
            with open(normalized, "r", encoding="utf-8", errors="replace") as f:
                prior_content = f.read()
        except OSError as e:
            return f"Error writing file: {e}"

    # Create parent directories
    parent = Path(normalized).parent
    parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(normalized, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"Error writing file: {e}"

    byte_count = len(content.encode("utf-8"))
    diff_stat = _build_diff_stat(prior_content, content, path=normalized, workspace=workspace)
    return f"{diff_stat}\nWrote {byte_count} bytes to {normalized}{_syntax_check(normalized, content)}"


def exec_edit(args: dict[str, Any], *, workspace: str | None = None) -> str:
    """Edit a file by replacing exact text regions.

    Supports three compact operations per edit item:
    - replace exact text via oldText + newText
    - insert after a unique anchor via insertAfter + newText
    - insert before a unique anchor via insertBefore + newText

    Every anchor must match a unique, non-overlapping region in the file.
    Edits are applied in order against the updated content.

    Args:
        args: {"path": str, "edits": [{"oldText": str, "newText": str}, ...]}
        workspace: Base path for relative paths.

    Returns:
        Confirmation message describing applied edits.
    """
    raw_path = args["path"]
    edits = args.get("edits", [])
    normalized = normalize_path(raw_path, workspace=workspace)

    if not os.path.exists(normalized):
        return f"Error: File not found: {normalized}"

    try:
        with open(normalized, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return f"Error reading file: {e}"

    original_content = content

    applied = 0
    errors = []

    for edit in edits:
        old_text = edit.get("oldText", "")
        insert_after = edit.get("insertAfter", "")
        insert_before = edit.get("insertBefore", "")
        new_text = edit.get("newText", "")
        anchor_text = old_text or insert_after or insert_before

        # Find exact match
        idx = content.find(anchor_text)
        if idx == -1:
            errors.append(
                f"edit anchor not found in file."
                f"{_closest_region_hint(content, anchor_text)}"
            )
            continue

        # Check for duplicate occurrences (would be ambiguous)
        second_idx = content.find(anchor_text, idx + len(anchor_text))
        if second_idx != -1:
            errors.append(
                f"edit anchor appears multiple times in file; cannot apply edit. "
                f"Make the anchor more specific to match a unique region."
            )
            continue

        if old_text:
            content = content[:idx] + new_text + content[idx + len(old_text):]
        elif insert_after:
            insertion_idx = idx + len(insert_after)
            content = content[:insertion_idx] + new_text + content[insertion_idx:]
        else:
            content = content[:idx] + new_text + content[idx:]
        applied += 1

    if not applied and errors:
        return f"Error: No edits applied to {normalized}.\n" + "\n".join(errors)

    # Write back
    try:
        with open(normalized, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"Error writing file: {e}"

    diff_stat = _build_diff_stat(original_content, content, path=normalized, workspace=workspace)
    return f"{diff_stat}\nApplied {applied} edit(s) to {normalized}{_syntax_check(normalized, content)}"


# ---------------------------------------------------------------------------
# Tool contracts
# ---------------------------------------------------------------------------


def make_read_tool(*, workspace: str | None = None, allowed_paths: list[str] | None = None) -> ToolContract:
    """Build the 'read' tool contract."""
    return build_tool(
        name="read",
        description=(
            "Read file contents. Supports text files only (binary refused). "
            "Use offset (1-indexed) and limit to read partial files. "
            "Large files are truncated with guidance to continue reading."
        ),
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start from (1-indexed)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                    "minimum": 1,
                },
            },
        },
        execute=lambda args: exec_read(args, workspace=workspace),
        validate_input=lambda data: validate_read_input(data, workspace=workspace),
        permission_check=make_filesystem_permission_check(workspace=workspace, allowed_paths=allowed_paths),
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_CORE],
    )


def make_list_dir_tool(*, workspace: str | None = None, allowed_paths: list[str] | None = None) -> ToolContract:
    return build_tool(
        name="list_dir",
        description=(
            "List the immediate contents of a directory. Use this to inspect folders and discover file names "
            "before calling read or edit."
        ),
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Directory path to inspect"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return",
                    "minimum": 1,
                },
            },
        },
        execute=lambda args: exec_list_dir(args, workspace=workspace),
        validate_input=lambda data: validate_list_dir_input(data, workspace=workspace),
        permission_check=make_filesystem_permission_check(workspace=workspace, allowed_paths=allowed_paths),
        read_only=True,
        concurrency_safe=True,
        categories=[CATEGORY_CORE],
    )


def make_write_tool(*, workspace: str | None = None, allowed_paths: list[str] | None = None) -> ToolContract:
    """Build the 'write' tool contract."""
    return build_tool(
        name="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Automatically creates parent directories. "
            "Provide exactly one of content or content_base64. For multiline text or Windows-heavy paths, "
            "prefer content_base64 and forward-slash paths."
        ),
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to. Prefer forward slashes on Windows.",
                },
                "content": {
                    "type": "string",
                    "description": "Raw UTF-8 text to write to the file",
                },
                "content_base64": {
                    "type": "string",
                    "description": "Base64-encoded UTF-8 text to write to the file",
                },
            },
        },
        execute=lambda args: exec_write(args, workspace=workspace),
        validate_input=validate_write_input,
        permission_check=make_filesystem_permission_check(workspace=workspace, allowed_paths=allowed_paths),
        destructive=True,
        categories=[CATEGORY_CORE],
    )


def make_edit_tool(*, workspace: str | None = None, allowed_paths: list[str] | None = None) -> ToolContract:
    """Build the 'edit' tool contract."""
    return build_tool(
        name="edit",
        description=(
            "Edit a file by replacing exact text regions. "
            "Each edit item must provide exactly one anchor: oldText, insertAfter, or insertBefore. "
            "Use insertAfter/insertBefore for compact helper additions so you do not have to restate large unchanged blocks. "
            "Anchors must match unique, non-overlapping regions in the file. Edits are applied in order."
        ),
        input_schema={
            "type": "object",
            "required": ["path", "edits"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to edit",
                },
                "edits": {
                    "type": "array",
                    "description": "List of compact file edits to apply",
                    "items": {
                        "type": "object",
                        "required": ["newText"],
                        "properties": {
                            "oldText": {
                                "type": "string",
                                "description": "Exact text to find and replace",
                            },
                            "insertAfter": {
                                "type": "string",
                                "description": "Unique anchor text after which newText should be inserted",
                            },
                            "insertBefore": {
                                "type": "string",
                                "description": "Unique anchor text before which newText should be inserted",
                            },
                            "newText": {
                                "type": "string",
                                "description": "Replacement or inserted text",
                            },
                        },
                    },
                },
            },
        },
        execute=lambda args: exec_edit(args, workspace=workspace),
        validate_input=validate_edit_input,
        permission_check=make_filesystem_permission_check(workspace=workspace, allowed_paths=allowed_paths),
        destructive=True,
        categories=[CATEGORY_CORE],
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_filesystem_tools(
    registry,
    *,
    workspace: str | None = None,
    allowed_paths: list[str] | None = None,
) -> list[ToolContract]:
    """Register all filesystem tools (list_dir, read, write, edit) in the registry.

    Returns the list of registered ToolContracts.
    """
    tools = [
        make_list_dir_tool(workspace=workspace, allowed_paths=allowed_paths or ([workspace] if workspace else None)),
        make_read_tool(workspace=workspace, allowed_paths=allowed_paths or ([workspace] if workspace else None)),
        make_write_tool(workspace=workspace, allowed_paths=allowed_paths or ([workspace] if workspace else None)),
        make_edit_tool(workspace=workspace, allowed_paths=allowed_paths or ([workspace] if workspace else None)),
    ]
    registry.register_many(tools)
    return tools
