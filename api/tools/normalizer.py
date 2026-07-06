# ToolCallNormalizer — provider output → internal ToolCall
#
# Bridges provider-agnostic ToolCall events (from ProviderEvent.TOOL_USE)
# into the internal ToolCall format consumed by ToolExecutor.
#
# Responsibilities:
#   1. Parse JSON-string arguments into dict
#   2. Normalize call_id across providers
#   3. Handle malformed or incomplete arguments
#   4. Validate tool name exists in registry before passing to executor

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from api.provider.base import ToolCall as ProviderToolCall
from api.tools.executor import ToolCall as ExecutorToolCall
from api.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

FRAGILE_WRITE_CONTENT_RE = re.compile(
    r"\b(select|insert|update|delete|create|alter|drop|with|from|where|join)\b",
    re.IGNORECASE,
)
WRITE_LIKE_TOOL_NAMES = {"write", "write_file", "create_file"}
EXEC_SCRIPT_HINT_RE = re.compile(
    r"(?:@['\"]|set-content|out-file|write-output|write-host|import\s+sqlite3|python\s+[A-Za-z]:/|python\s+[A-Za-z]:\\)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalization result
# ---------------------------------------------------------------------------

@dataclass
class NormalizationResult:
    """Outcome of normalizing a provider tool call."""
    success: bool
    executor_call: ExecutorToolCall | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.success


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

class ToolCallNormalizer:
    """Normalize provider tool-call output into internal ExecutorToolCall.

    Handles:
    - JSON argument parsing (provider stores args as raw JSON strings)
    - Malformed JSON recovery (attempts repair or falls back to empty dict)
    - Unknown tool filtering (warns but does not reject)
    - Call ID normalization (ensures consistent format)
    """

    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry

    def normalize(
        self,
        provider_call: ProviderToolCall,
        *,
        validate_known: bool = True,
    ) -> NormalizationResult:
        """Convert a provider ToolCall into an ExecutorToolCall.

        Args:
            provider_call: Raw tool call from provider event.
            validate_known: If True and registry is set, reject unknown tools.

        Returns:
            NormalizationResult with parsed ExecutorToolCall or error.
        """
        # -- Step 1: Normalize call ID --
        call_id = self._normalize_call_id(provider_call.id)

        # -- Step 2: Parse arguments --
        arguments, parse_error = self._parse_arguments(
            provider_call.arguments,
            tool_name=provider_call.name,
        )
        if parse_error:
            logger.warning(
                "Tool call %s argument parse error: %s",
                provider_call.name,
                parse_error,
            )
            arguments = {}

        # -- Step 3: Validate tool name (optional) --
        if validate_known and self.registry is not None:
            if provider_call.name not in self.registry._tools:
                return NormalizationResult(
                    success=False,
                    error=f"Unknown tool: {provider_call.name}",
                )

        executor_call = ExecutorToolCall(
            call_id=call_id,
            name=provider_call.name,
            arguments=self._stabilize_arguments(arguments, provider_call.name),
            parse_error=parse_error or None,
        )

        return NormalizationResult(
            success=True,
            executor_call=executor_call,
        )

    def normalize_batch(
        self,
        provider_calls: list[ProviderToolCall],
        *,
        validate_known: bool = True,
    ) -> list[ExecutorToolCall]:
        """Normalize a batch of provider tool calls.

        Returns only successfully normalized calls; logs warnings for failures.
        """
        executor_calls = []
        for pc in provider_calls:
            result = self.normalize(pc, validate_known=validate_known)
            if result.ok and result.executor_call:
                executor_calls.append(result.executor_call)
            else:
                logger.warning(
                    "Dropped tool call '%s': %s",
                    pc.name,
                    result.error,
                )
        return executor_calls

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _normalize_call_id(raw_id: str) -> str:
        """Ensure call_id is a consistent hex format."""
        if not raw_id:
            return f"call_{uuid.uuid4().hex[:12]}"
        # If already looks like a hex call id, keep it
        if raw_id.startswith("call_") and len(raw_id) <= 20:
            return raw_id
        # Otherwise generate a stable ID from the raw value
        return f"call_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _parse_arguments(
        raw_args: str,
        *,
        tool_name: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Parse JSON-string arguments into dict.

        Returns (parsed_dict, error_string).
        Error string is empty on success.
        """
        _ = tool_name

        if not raw_args or raw_args == "{}":
            return {}, ""

        # Fast path: clean JSON
        parse_err = ""
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed, ""
            # Model returned non-dict JSON (e.g., string or array)
            return {}, f"Arguments parsed as {type(parsed).__name__}, expected object"
        except json.JSONDecodeError as e:
            parse_err = str(e)

        # Repair attempts for common model output issues

        # Attempt 1: trailing comma before closing brace
        repaired = raw_args.rstrip()
        if repaired.endswith(",}") or repaired.endswith(",]"):
            repaired = repaired[:-2] + "}"
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed, ""
            except json.JSONDecodeError:
                pass

        # Attempt 2: escape raw control characters and invalid backslashes in string literals.
        sanitized = ToolCallNormalizer._sanitize_jsonish_strings(raw_args)
        if sanitized != raw_args:
            try:
                parsed = json.loads(sanitized)
                if isinstance(parsed, dict):
                    return parsed, ""
            except json.JSONDecodeError:
                pass

        # Attempt 3: parse common json-ish objects with tolerant string handling.
        parsed = ToolCallNormalizer._parse_object_like_arguments(raw_args)
        if isinstance(parsed, dict):
            return parsed, ""

        # Attempt 4: missing quotes around keys
        # Very rough heuristic for things like {"name": "value", path: "file.txt"}
        # Skip this for now — models usually quote keys correctly

        # Attempt 5: wrap in object if it's a bare string
        if not raw_args.strip().startswith("{"):
            # Maybe the model just output a string value
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, str):
                    # Wrap as {"value": "..."}
                    return {"value": parsed}, ""
            except json.JSONDecodeError:
                pass

        return {}, f"JSON parse failed: {parse_err}"

    @staticmethod
    def _sanitize_jsonish_strings(raw_args: str) -> str:
        chars: list[str] = []
        in_string = False
        escape_next = False

        for index, char in enumerate(raw_args):
            if not in_string:
                chars.append(char)
                if char == '"':
                    in_string = True
                continue

            if escape_next:
                chars.append(char)
                escape_next = False
                continue

            if char == "\\":
                next_char = raw_args[index + 1] if index + 1 < len(raw_args) else ""
                if next_char in {'"', "\\", "/"}:
                    chars.append(char)
                    escape_next = True
                elif next_char == "u":
                    chars.append(char)
                    escape_next = True
                else:
                    chars.append("\\\\")
                continue

            if char == "\n":
                chars.append("\\n")
                continue

            if char == "\r":
                chars.append("\\r")
                continue

            if char == "\t":
                chars.append("\\t")
                continue

            chars.append(char)
            if char == '"':
                in_string = False

        return "".join(chars)

    @staticmethod
    def _stabilize_arguments(arguments: dict[str, Any], tool_name: str) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return arguments

        stabilized = dict(arguments)
        if tool_name in WRITE_LIKE_TOOL_NAMES:
            content = stabilized.get("content")
            if isinstance(content, str) and "content_base64" not in stabilized:
                if ToolCallNormalizer._should_base64_encode_write_content(content):
                    stabilized["content_base64"] = base64.b64encode(content.encode("utf-8")).decode("ascii")
                    stabilized.pop("content", None)
        if tool_name == "exec":
            command = stabilized.get("command")
            if isinstance(command, str) and "script_content" not in stabilized and "script_content_base64" not in stabilized:
                if ToolCallNormalizer._should_promote_exec_command(command):
                    stabilized["script_content_base64"] = base64.b64encode(command.encode("utf-8")).decode("ascii")
                    stabilized.setdefault("runner", "shell")
                    stabilized.pop("command", None)
        return stabilized

    @staticmethod
    def _should_base64_encode_write_content(content: str) -> bool:
        return (
            len(content) >= 200
            or "\n" in content
            or "\r" in content
            or "\\" in content
            or '"' in content
            or "'" in content
            or bool(FRAGILE_WRITE_CONTENT_RE.search(content))
        )

    @staticmethod
    def _should_promote_exec_command(command: str) -> bool:
        return (
            len(command) >= 200
            or "\n" in command
            or "\r" in command
            or bool(EXEC_SCRIPT_HINT_RE.search(command))
        )

    @staticmethod
    def _parse_object_like_arguments(raw_args: str) -> dict[str, Any] | None:
        text = raw_args.strip()
        if not text.startswith("{") or not text.endswith("}"):
            return None

        index = 1
        result: dict[str, Any] = {}

        while True:
            index = ToolCallNormalizer._skip_whitespace(text, index)
            if index >= len(text):
                return None
            if text[index] == "}":
                return result

            key, index = ToolCallNormalizer._parse_key(text, index)
            if key is None:
                return None

            index = ToolCallNormalizer._skip_whitespace(text, index)
            if index >= len(text) or text[index] != ":":
                return None
            index += 1

            index = ToolCallNormalizer._skip_whitespace(text, index)
            value, index, ok = ToolCallNormalizer._parse_value(text, index)
            if not ok:
                return None
            result[key] = value

            index = ToolCallNormalizer._skip_whitespace(text, index)
            if index >= len(text):
                return None
            if text[index] == ",":
                index += 1
                continue
            if text[index] == "}":
                return result
            return None

    @staticmethod
    def _skip_whitespace(text: str, index: int) -> int:
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    @staticmethod
    def _parse_key(text: str, index: int) -> tuple[str | None, int]:
        if index >= len(text):
            return None, index

        if text[index] == '"':
            end = index + 1
            while end < len(text):
                if text[end] == '"' and text[end - 1] != "\\":
                    return text[index + 1:end], end + 1
                end += 1
            return None, index

        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
        if not match:
            return None, index
        key = match.group(0)
        return key, index + len(key)

    @staticmethod
    def _parse_value(text: str, index: int) -> tuple[Any, int, bool]:
        if index >= len(text):
            return None, index, False

        if text[index] == '"':
            return ToolCallNormalizer._parse_tolerant_string(text, index)

        depth = 0
        in_string = False
        escape_next = False
        end = index
        while end < len(text):
            char = text[end]
            if in_string:
                if escape_next:
                    escape_next = False
                elif char == "\\":
                    escape_next = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char in "[{":
                    depth += 1
                elif char in "]}":
                    if depth == 0:
                        break
                    depth -= 1
                elif char == "," and depth == 0:
                    break
            end += 1

        fragment = text[index:end].strip()
        if not fragment:
            return None, index, False
        try:
            return json.loads(fragment), end, True
        except json.JSONDecodeError:
            return None, index, False

    @staticmethod
    def _parse_tolerant_string(text: str, index: int) -> tuple[str | None, int, bool]:
        chars: list[str] = []
        cursor = index + 1

        while cursor < len(text):
            char = text[cursor]
            if char == '"' and ToolCallNormalizer._looks_like_string_terminator(text, cursor):
                return "".join(chars), cursor + 1, True

            if char == "\\":
                next_char = text[cursor + 1] if cursor + 1 < len(text) else ""
                if next_char == '"':
                    chars.append('"')
                    cursor += 2
                    continue
                if next_char == "\\":
                    chars.append("\\")
                    cursor += 2
                    continue
                chars.append("\\")
                cursor += 1
                continue

            chars.append(char)
            cursor += 1

        return None, index, False

    @staticmethod
    def _looks_like_string_terminator(text: str, quote_index: int) -> bool:
        cursor = ToolCallNormalizer._skip_whitespace(text, quote_index + 1)
        if cursor >= len(text):
            return True
        if text[cursor] == "}":
            return True
        if text[cursor] != ",":
            return False
        cursor = ToolCallNormalizer._skip_whitespace(text, cursor + 1)
        if cursor >= len(text):
            return True
        return text[cursor] in {'"', '}'}
