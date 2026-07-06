# Tests for ToolCallNormalizer — provider output → internal ToolCall

from __future__ import annotations

import base64
import json

import pytest
from api.provider.base import ToolCall as ProviderToolCall
from api.tools.normalizer import (
    ToolCallNormalizer,
    NormalizationResult,
)
from api.tools.registry import ToolRegistry, build_tool


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_registry():
    reg = ToolRegistry(
        agent_type="main",
        trust_mode="allowlist",
        allowlist=["echo", "read_file", "write_file"],
        provider_capabilities={"supports_tool_use": True},
    )
    reg.register(build_tool(
        "echo",
        description="Echo input",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        execute=lambda args: args.get("text", ""),
        read_only=True,
        categories=["core"],
    ))
    reg.register(build_tool(
        "read_file",
        description="Read a file",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
        execute=lambda args: f"contents: {args.get('path', '')}",
        read_only=True,
        categories=["core"],
    ))
    reg.register(build_tool(
        "write_file",
        description="Write a file",
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        execute=lambda args: {"written": True},
        categories=["core"],
    ))
    return reg


# ---------------------------------------------------------------------------
# Basic normalization
# ---------------------------------------------------------------------------

class TestNormalizeBasic:
    def setup_method(self):
        self.normalizer = ToolCallNormalizer()

    def test_valid_json_args(self):
        pc = ProviderToolCall(
            id="call_abc123",
            name="echo",
            arguments='{"text": "hello"}',
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.name == "echo"
        assert result.executor_call.arguments == {"text": "hello"}

    def test_empty_args(self):
        pc = ProviderToolCall(
            id="call_1",
            name="echo",
            arguments="{}",
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments == {}

    def test_empty_string_args(self):
        pc = ProviderToolCall(
            id="call_2",
            name="echo",
            arguments="",
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments == {}

    def test_complex_args(self):
        args = '{"path": "/tmp/test.txt", "offset": 0, "limit": 100}'
        pc = ProviderToolCall(id="call_3", name="read_file", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments["path"] == "/tmp/test.txt"
        assert result.executor_call.arguments["offset"] == 0
        assert result.executor_call.arguments["limit"] == 100

    def test_nested_json_args(self):
        args = '{"data": {"nested": true, "list": [1, 2]}}'
        pc = ProviderToolCall(id="call_4", name="echo", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments["data"]["nested"] is True


# ---------------------------------------------------------------------------
# Argument repair
# ---------------------------------------------------------------------------

class TestArgumentRepair:
    def setup_method(self):
        self.normalizer = ToolCallNormalizer()

    def test_trailing_comma(self):
        args = '{"text": "hello",}'
        pc = ProviderToolCall(id="call_5", name="echo", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments == {"text": "hello"}

    def test_malformed_json_falls_back(self):
        args = '{text: "hello"}'  # unquoted key
        pc = ProviderToolCall(id="call_6", name="echo", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments == {"text": "hello"}
        assert result.executor_call.parse_error is None

    def test_non_json_string_falls_back(self):
        args = "just read the file"  # model didn't output JSON
        pc = ProviderToolCall(id="call_7", name="echo", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments == {}
        assert "JSON parse failed" in (result.executor_call.parse_error or "")

    def test_write_like_multiline_content_repairs(self):
        args = r'''{"path":"D:\openclaw\opencloset\tmp\query.py","content":"import sqlite3
c.execute("SELECT COUNT(*) FROM trades")
print(r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite")"}'''
        pc = ProviderToolCall(id="call_write", name="write_file", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments["path"] == "D:\\openclaw\\opencloset\\tmp\\query.py"
        content = base64.b64decode(result.executor_call.arguments["content_base64"]).decode("utf-8")
        assert 'c.execute("SELECT COUNT(*) FROM trades")' in content
        assert 'D:\\openclaw\\opencloset\\ft_userdata\\user_data\\tradesv3.sqlite' in content
        assert result.executor_call.parse_error is None

    def test_exec_like_multiline_command_repairs(self):
        args = r'''{"command":"@"
import sqlite3
Write-Host "hi"
"@ | Set-Content D:\openclaw\opencloset\tmp\query.ps1"}'''
        pc = ProviderToolCall(id="call_exec", name="exec", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        decoded = base64.b64decode(result.executor_call.arguments["script_content_base64"]).decode("utf-8")
        assert 'Write-Host "hi"' in decoded
        assert 'D:\\openclaw\\opencloset\\tmp\\query.ps1' in decoded
        assert result.executor_call.arguments["runner"] == "shell"
        assert result.executor_call.parse_error is None

    def test_windows_path_list_repairs_via_sanitizer(self):
        args = r'''{"paths":["D:\tmp\alpha.py","D:\tmp\beta.py"]}'''
        pc = ProviderToolCall(id="call_paths", name="echo", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert result.executor_call.arguments["paths"] == [
            "D:\\tmp\\alpha.py",
            "D:\\tmp\\beta.py",
        ]
        assert result.executor_call.parse_error is None

    def test_fragile_write_content_is_promoted_to_base64(self):
        content = 'SELECT * FROM trades WHERE path = "D:\\openclaw\\db.sqlite"\nprint("done")'
        args = '{"path":"D:/openclaw/tmp/query.py","content":' + json.dumps(content) + '}'
        pc = ProviderToolCall(id="call_write_safe", name="write_file", arguments=args)
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok
        assert "content" not in result.executor_call.arguments
        encoded = result.executor_call.arguments["content_base64"]
        assert base64.b64decode(encoded).decode("utf-8") == content


# ---------------------------------------------------------------------------
# Validation against registry
# ---------------------------------------------------------------------------

class TestValidateKnown:
    def setup_method(self):
        self.registry = _make_registry()
        self.normalizer = ToolCallNormalizer(self.registry)

    def test_known_tool_passes(self):
        pc = ProviderToolCall(
            id="call_8",
            name="echo",
            arguments='{"text": "ok"}',
        )
        result = self.normalizer.normalize(pc, validate_known=True)
        assert result.ok

    def test_unknown_tool_rejected(self):
        pc = ProviderToolCall(
            id="call_9",
            name="nonexistent_tool",
            arguments="{}",
        )
        result = self.normalizer.normalize(pc, validate_known=True)
        assert not result.ok
        assert "Unknown tool" in result.error

    def test_no_registry_skips_validation(self):
        normalizer = ToolCallNormalizer()  # no registry
        pc = ProviderToolCall(
            id="call_10",
            name="anything",
            arguments="{}",
        )
        result = normalizer.normalize(pc, validate_known=True)
        assert result.ok  # no registry → no validation

    def test_validate_known_false(self):
        pc = ProviderToolCall(
            id="call_11",
            name="nonexistent_tool",
            arguments="{}",
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.ok  # validation skipped


# ---------------------------------------------------------------------------
# Call ID normalization
# ---------------------------------------------------------------------------

class TestCallIdNormalization:
    def setup_method(self):
        self.normalizer = ToolCallNormalizer()

    def test_valid_call_id_kept(self):
        pc = ProviderToolCall(
            id="call_abc123def456",
            name="echo",
            arguments='{"text": "x"}',
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.executor_call.call_id == "call_abc123def456"

    def test_empty_id_generated(self):
        pc = ProviderToolCall(
            id="",
            name="echo",
            arguments='{"text": "x"}',
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.executor_call.call_id.startswith("call_")

    def test_long_id_normalized(self):
        pc = ProviderToolCall(
            id="some_very_long_weird_provider_id_that_exceeds_limits",
            name="echo",
            arguments='{"text": "x"}',
        )
        result = self.normalizer.normalize(pc, validate_known=False)
        assert result.executor_call.call_id.startswith("call_")


# ---------------------------------------------------------------------------
# Batch normalization
# ---------------------------------------------------------------------------

class TestNormalizeBatch:
    def setup_method(self):
        self.registry = _make_registry()
        self.normalizer = ToolCallNormalizer(self.registry)

    def test_all_valid(self):
        calls = [
            ProviderToolCall(id="1", name="echo", arguments='{"text": "a"}'),
            ProviderToolCall(id="2", name="echo", arguments='{"text": "b"}'),
        ]
        result = self.normalizer.normalize_batch(calls, validate_known=True)
        assert len(result) == 2

    def test_mixed_known_unknown(self):
        calls = [
            ProviderToolCall(id="1", name="echo", arguments='{"text": "a"}'),
            ProviderToolCall(id="2", name="unknown", arguments="{}"),
            ProviderToolCall(id="3", name="read_file", arguments='{"path": "/x"}'),
        ]
        result = self.normalizer.normalize_batch(calls, validate_known=True)
        assert len(result) == 2  # unknown dropped

    def test_empty_batch(self):
        result = self.normalizer.normalize_batch([])
        assert result == []


# ---------------------------------------------------------------------------
# NormalizationResult helpers
# ---------------------------------------------------------------------------

class TestNormalizationResult:
    def test_ok_property(self):
        r = NormalizationResult(success=True)
        assert r.ok is True

        r2 = NormalizationResult(success=False, error="bad")
        assert r2.ok is False
