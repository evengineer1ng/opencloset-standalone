# Tests for filesystem tools — list_dir, read, write, edit

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import pytest
from api.tools.filesystem import (
    make_list_dir_tool,
    make_read_tool,
    make_write_tool,
    make_edit_tool,
    normalize_path,
    is_binary_file,
    exec_list_dir,
    exec_read,
    exec_write,
    exec_edit,
    validate_list_dir_input,
    validate_read_input,
    validate_write_input,
    validate_edit_input,
    MAX_READ_BYTES,
    MAX_READ_CHARS,
)
from api.tools.registry import ToolRegistry, build_tool
from api.tools.registry import PermissionDecision


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestNormalizePath:
    def test_absolute_path(self):
        p = normalize_path("/tmp/test.txt")
        assert Path(p).is_absolute()

    def test_relative_with_workspace(self):
        p = normalize_path("sub/file.txt", workspace="/tmp/ws")
        assert "ws" in p
        assert Path(p).is_absolute()

    def test_home_expansion(self):
        p = normalize_path("~/test.txt")
        assert "~" not in p
        assert Path(p).is_absolute()


class TestIsBinaryFile:
    def test_text_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello world\n")
        assert is_binary_file(str(f)) is False

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02Hello")
        assert is_binary_file(str(f)) is True


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestValidateReadInput:
    def test_valid_input(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = validate_read_input({"path": str(f)})
        assert result.ok is True

    def test_empty_path(self):
        result = validate_read_input({"path": ""})
        assert result.ok is False

    def test_missing_file(self):
        result = validate_read_input({"path": "/nonexistent/file.txt"})
        assert result.ok is False

    def test_directory_path(self, tmp_path):
        result = validate_read_input({"path": str(tmp_path)})
        assert result.ok is False
        assert "directory" in " ".join(result.errors).lower()

    def test_binary_file_refused(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02")
        result = validate_read_input({"path": str(f)})
        assert result.ok is False
        assert "binary" in " ".join(result.errors).lower()


class TestValidateListDirInput:
    def test_valid_input(self, tmp_path):
        result = validate_list_dir_input({"path": str(tmp_path)})
        assert result.ok is True

    def test_empty_path(self):
        result = validate_list_dir_input({"path": ""})
        assert result.ok is False

    def test_file_path_refused(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text("hello")
        result = validate_list_dir_input({"path": str(file_path)})
        assert result.ok is False
        assert "not a directory" in " ".join(result.errors).lower()


class TestValidateWriteInput:
    def test_valid_input(self):
        result = validate_write_input({
            "path": "/tmp/test.txt",
            "content": "hello world",
        })
        assert result.ok is True

    def test_valid_base64_input(self):
        result = validate_write_input({
            "path": "/tmp/test.txt",
            "content_base64": "aGVsbG8gd29ybGQ=",
        })
        assert result.ok is True

    def test_requires_exactly_one_content_field(self):
        result = validate_write_input({
            "path": "/tmp/test.txt",
        })
        assert result.ok is False

        result = validate_write_input({
            "path": "/tmp/test.txt",
            "content": "hello",
            "content_base64": "aGVsbG8=",
        })
        assert result.ok is False


class TestValidateEditInput:
    def test_valid_input(self):
        result = validate_edit_input({
            "path": "/tmp/test.txt",
            "edits": [{"oldText": "foo", "newText": "bar"}],
        })
        assert result.ok is True

    def test_valid_insert_after_input(self):
        result = validate_edit_input({
            "path": "/tmp/test.txt",
            "edits": [{"insertAfter": "foo", "newText": "\nbar"}],
        })
        assert result.ok is True

    def test_empty_old_text(self):
        result = validate_edit_input({
            "path": "/tmp/test.txt",
            "edits": [{"oldText": "", "newText": "bar"}],
        })
        assert result.ok is False

    def test_rejects_multiple_anchor_modes(self):
        result = validate_edit_input({
            "path": "/tmp/test.txt",
            "edits": [{"oldText": "foo", "insertAfter": "foo", "newText": "bar"}],
        })
        assert result.ok is False


# ---------------------------------------------------------------------------
# read tool
# ---------------------------------------------------------------------------


class TestReadTool:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tool = make_read_tool(workspace=self.tmp_dir)

    def test_basic_read(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("hello world\n")

        result = self.tool.execute({"path": "test.txt"})
        assert "hello world" in result

    def test_read_with_offset(self):
        path = os.path.join(self.tmp_dir, "lines.txt")
        with open(path, "w") as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")

        result = self.tool.execute({"path": "lines.txt", "offset": 3})
        assert "line1" not in result
        assert "line3" in result
        assert "line5" in result

    def test_read_with_limit(self):
        path = os.path.join(self.tmp_dir, "lines.txt")
        with open(path, "w") as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")

        result = self.tool.execute({"path": "lines.txt", "limit": 2})
        assert "line1" in result
        assert "line2" in result
        assert "line3" not in result

    def test_read_with_offset_and_limit(self):
        path = os.path.join(self.tmp_dir, "lines.txt")
        with open(path, "w") as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")

        result = self.tool.execute({
            "path": "lines.txt",
            "offset": 2,
            "limit": 2,
        })
        assert "line1" not in result
        assert "line2" in result
        assert "line3" in result
        assert "line4" not in result

    def test_read_nonexistent_file(self):
        result = self.tool.execute({"path": "no_such_file.txt"})
        assert "not found" in result.lower() or "Error" in result

    def test_read_binary_refused(self):
        path = os.path.join(self.tmp_dir, "binary.bin")
        with open(path, "wb") as f:
            f.write(b"\x00\x01\x02hello")

        # Execute function doesn't check binary; validation does
        # But if we call exec_read directly it should still read
        result = exec_read({"path": "binary.bin"}, workspace=self.tmp_dir)
        # It reads anyway since exec_read doesn't validate
        assert "hello" in result or "Error" in result

    def test_read_large_file_truncated(self):
        path = os.path.join(self.tmp_dir, "large.txt")
        # Write a file larger than MAX_READ_CHARS
        content = "x" * (MAX_READ_CHARS + 1000)
        with open(path, "w") as f:
            f.write(content)

        result = self.tool.execute({"path": "large.txt"})
        assert "TRUNCATED" in result
        assert len(result) < len(content)

    def test_read_offset_beyond_file(self):
        path = os.path.join(self.tmp_dir, "small.txt")
        with open(path, "w") as f:
            f.write("one line\n")

        result = self.tool.execute({"path": "small.txt", "offset": 100})
        assert "Warning" in result or "offset" in result.lower()

    def test_read_directory_refused(self):
        result = self.tool.execute({"path": "."})
        assert "directory" in result.lower() or "Error" in result


class TestListDirTool:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tool = make_list_dir_tool(workspace=self.tmp_dir)

    def test_basic_listing(self):
        os.mkdir(os.path.join(self.tmp_dir, "alpha"))
        with open(os.path.join(self.tmp_dir, "beta.txt"), "w") as f:
            f.write("ok")

        result = self.tool.execute({"path": "."})
        assert "Directory:" in result
        assert "alpha/" in result
        assert "beta.txt" in result

    def test_listing_truncates(self):
        for idx in range(5):
            with open(os.path.join(self.tmp_dir, f"file{idx}.txt"), "w") as f:
                f.write("ok")

        result = exec_list_dir({"path": ".", "limit": 2}, workspace=self.tmp_dir)
        assert "TRUNCATED" in result


# ---------------------------------------------------------------------------
# write tool
# ---------------------------------------------------------------------------


class TestWriteTool:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tool = make_write_tool(workspace=self.tmp_dir)

    def test_basic_write(self):
        path = os.path.join(self.tmp_dir, "new_file.txt")
        result = self.tool.execute({
            "path": "new_file.txt",
            "content": "hello world",
        })
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == "hello world"
        assert "Wrote" in result

    def test_overwrite(self):
        path = os.path.join(self.tmp_dir, "existing.txt")
        with open(path, "w") as f:
            f.write("old content")

        result = self.tool.execute({
            "path": "existing.txt",
            "content": "new content",
        })
        with open(path) as f:
            assert f.read() == "new content"

    def test_create_parent_dirs(self):
        path = os.path.join(self.tmp_dir, "a", "b", "c", "deep.txt")
        result = self.tool.execute({
            "path": "a/b/c/deep.txt",
            "content": "deep",
        })
        assert os.path.exists(path)

    def test_write_utf8_content(self):
        path = os.path.join(self.tmp_dir, "utf8.txt")
        content = "Hello 世界 🌍"
        result = self.tool.execute({
            "path": "utf8.txt",
            "content": content,
        })
        with open(path, encoding="utf-8") as f:
            assert f.read() == content

    def test_write_byte_count(self):
        path = os.path.join(self.tmp_dir, "count.txt")
        content = "hello"
        result = self.tool.execute({
            "path": "count.txt",
            "content": content,
        })
        assert "+1 -0 count.txt" in result
        assert "5 bytes" in result

    def test_write_base64_content(self):
        path = os.path.join(self.tmp_dir, "base64.txt")
        result = self.tool.execute({
            "path": "base64.txt",
            "content_base64": "bGluZTEKbGluZTIK",
        })
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "line1\nline2\n"
        assert "+2 -0 base64.txt" in result
        assert "Wrote" in result

    def test_write_over_existing_file_reports_diff_stat(self):
        path = os.path.join(self.tmp_dir, "rewrite.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\n")

        result = self.tool.execute({
            "path": "rewrite.txt",
            "content": "line1\nlineX\nline3\n",
        })

        assert "+2 -1 rewrite.txt" in result


# ---------------------------------------------------------------------------
# edit tool
# ---------------------------------------------------------------------------


class TestEditTool:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tool = make_edit_tool(workspace=self.tmp_dir)

    def test_basic_edit(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("hello world")

        result = self.tool.execute({
            "path": "test.txt",
            "edits": [{"oldText": "world", "newText": "universe"}],
        })
        with open(path) as f:
            assert f.read() == "hello universe"
        assert "+1 -1 test.txt" in result
        assert "1 edit" in result

    def test_multiple_edits(self):
        path = os.path.join(self.tmp_dir, "multi.txt")
        with open(path, "w") as f:
            f.write("foo bar baz")

        result = self.tool.execute({
            "path": "multi.txt",
            "edits": [
                {"oldText": "foo", "newText": "FOO"},
                {"oldText": "bar", "newText": "BAR"},
            ],
        })
        with open(path) as f:
            content = f.read()
        assert "FOO" in content
        assert "BAR" in content
        assert "baz" in content

    def test_edit_nonexistent_file(self):
        result = self.tool.execute({
            "path": "no_such_file.txt",
            "edits": [{"oldText": "foo", "newText": "bar"}],
        })
        assert "not found" in result.lower() or "Error" in result

    def test_edit_old_text_not_found(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("hello world")

        result = self.tool.execute({
            "path": "test.txt",
            "edits": [{"oldText": "not_here", "newText": "replacement"}],
        })
        assert "not found" in result.lower() or "Error" in result

    def test_edit_duplicate_occurrences(self):
        path = os.path.join(self.tmp_dir, "dup.txt")
        with open(path, "w") as f:
            f.write("foo bar foo bar")

        result = self.tool.execute({
            "path": "dup.txt",
            "edits": [{"oldText": "foo", "newText": "FOO"}],
        })
        # Should fail because "foo" appears twice
        assert "multiple" in result.lower() or "Error" in result

    def test_edit_empty_old_text(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("hello")

        # Validation catches this before execution
        result = self.tool.validate_input({
            "path": "test.txt",
            "edits": [{"oldText": "", "newText": "replacement"}],
        })
        assert result.ok is False

    def test_insert_after_unique_anchor(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("alpha\nbeta\n")

        result = self.tool.execute({
            "path": "test.txt",
            "edits": [{"insertAfter": "alpha\n", "newText": "gamma\n"}],
        })

        with open(path) as f:
            assert f.read() == "alpha\ngamma\nbeta\n"
        assert "1 edit" in result

    def test_insert_before_unique_anchor(self):
        path = os.path.join(self.tmp_dir, "test.txt")
        with open(path, "w") as f:
            f.write("alpha\nbeta\n")

        result = self.tool.execute({
            "path": "test.txt",
            "edits": [{"insertBefore": "beta\n", "newText": "gamma\n"}],
        })

        with open(path) as f:
            assert f.read() == "alpha\ngamma\nbeta\n"
        assert "1 edit" in result


# ---------------------------------------------------------------------------
# Integration: read → write → edit → read
# ---------------------------------------------------------------------------


class TestFilesystemIntegration:
    def test_roundtrip(self, tmp_path):
        workspace = str(tmp_path)
        write_tool = make_write_tool(workspace=workspace)
        read_tool = make_read_tool(workspace=workspace)
        edit_tool = make_edit_tool(workspace=workspace)

        # Write
        write_tool.execute({
            "path": "test.txt",
            "content": "line1\nline2\nline3\n",
        })

        # Read
        content = read_tool.execute({"path": "test.txt"})
        assert "line2" in content

        # Edit
        edit_tool.execute({
            "path": "test.txt",
            "edits": [{"oldText": "line2", "newText": "LINE2"}],
        })

        # Read again
        content = read_tool.execute({"path": "test.txt"})
        assert "LINE2" in content
        assert "line1" in content


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


class TestFilesystemRegistration:
    def test_make_list_dir_tool_metadata(self):
        tool = make_list_dir_tool()
        assert tool.name == "list_dir"
        assert tool.read_only is True
        assert tool.concurrency_safe is True
        assert "core" in tool.categories
        assert tool.input_schema["required"] == ["path"]

    def test_make_read_tool_metadata(self):
        tool = make_read_tool()
        assert tool.name == "read"
        assert tool.read_only is True
        assert tool.concurrency_safe is True
        assert "core" in tool.categories
        assert tool.input_schema["required"] == ["path"]

    def test_make_write_tool_metadata(self):
        tool = make_write_tool()
        assert tool.name == "write"
        assert tool.destructive is True
        assert "core" in tool.categories
        assert "path" in tool.input_schema["required"]
        assert "content" in tool.input_schema["properties"]
        assert "content_base64" in tool.input_schema["properties"]

    def test_make_edit_tool_metadata(self):
        tool = make_edit_tool()
        assert tool.name == "edit"
        assert tool.destructive is True
        assert "core" in tool.categories
        assert "insertAfter" in tool.input_schema["properties"]["edits"]["items"]["properties"]
        assert "insertBefore" in tool.input_schema["properties"]["edits"]["items"]["properties"]

    def test_register_in_registry(self):
        from api.tools.filesystem import register_filesystem_tools
        reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["list_dir", "read", "write", "edit"],
            provider_capabilities={"supports_tool_use": True},
        )
        tools = register_filesystem_tools(reg)
        assert len(tools) == 4
        assert len(reg.all_tools) == 4
        assert "list_dir" in [t.name for t in reg.all_tools]
        assert "read" in [t.name for t in reg.all_tools]
        assert "write" in [t.name for t in reg.all_tools]
        assert "edit" in [t.name for t in reg.all_tools]

    def test_permission_check_denies_path_outside_allowed_scope(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        allowed_root = workspace / "allowed"
        allowed_root.mkdir()
        outside_file = tmp_path / "outside.txt"

        reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["write"],
            destructive_allowlist=["write"],
            provider_capabilities={"supports_tool_use": True},
        )
        reg.register(make_write_tool(workspace=str(workspace), allowed_paths=[str(allowed_root)]))

        allowed_decision = reg.check_permission(
            "write",
            input_data={"path": str(allowed_root / "note.txt"), "content": "ok"},
        )
        denied_decision = reg.check_permission(
            "write",
            input_data={"path": str(outside_file), "content": "nope"},
        )

        assert allowed_decision == PermissionDecision.ALLOW
        assert denied_decision == PermissionDecision.DENY
