# Tests for ToolRegistry — dynamic tool pool assembly + two-layer permissions

import pytest
from api.tools.registry import (
    ToolRegistry,
    ToolContract,
    ToolManifest,
    build_tool,
    PermissionDecision,
    ValidationResult,
    create_default_registry,
)


# ---------------------------------------------------------------------------
# build_tool() factory
# ---------------------------------------------------------------------------

class TestBuildTool:
    def test_safe_defaults(self):
        tool = build_tool("test", description="A test tool")
        assert tool.name == "test"
        assert tool.description == "A test tool"
        assert tool.concurrency_safe is False
        assert tool.read_only is False
        assert tool.destructive is False
        assert tool.interruptible is False
        assert tool.execute is None

    def test_custom_flags(self):
        tool = build_tool(
            "safe_read",
            read_only=True,
            concurrency_safe=True,
            categories=["core"],
        )
        assert tool.read_only is True
        assert tool.concurrency_safe is True
        assert "core" in tool.categories

    def test_to_prompt_dict(self):
        tool = build_tool(
            "read",
            description="Read file contents",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        d = tool.to_prompt_dict()
        assert d["name"] == "read"
        assert d["description"] == "Read file contents"
        assert "path" in d["input_schema"]["properties"]

    def test_to_provider_dict(self):
        tool = build_tool(
            "read",
            description="Read file contents",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        d = tool.to_provider_dict()
        assert d["type"] == "function"
        assert d["function"]["name"] == "read"
        assert d["function"]["description"] == "Read file contents"
        assert "path" in d["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# ToolRegistry — registration
# ---------------------------------------------------------------------------

class TestRegistryRegistration:
    def setup_method(self):
        self.reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["read", "write"],
        )

    def test_register_single(self):
        tool = build_tool("read", read_only=True, categories=["core"])
        self.reg.register(tool)
        assert self.reg.all_tools[0].name == "read"

    def test_register_many(self):
        tools = [
            build_tool("read", categories=["core"]),
            build_tool("write", categories=["core"]),
        ]
        self.reg.register_many(tools)
        assert len(self.reg.all_tools) == 2

    def test_unregister(self):
        self.reg.register(build_tool("read"))
        assert self.reg.unregister("read") is True
        assert len(self.reg.all_tools) == 0
        assert self.reg.unregister("nonexistent") is False


# ---------------------------------------------------------------------------
# Layer 1: Assembly-time filtering
# ---------------------------------------------------------------------------

class TestAssemblyFiltering:
    def _make_registry(self, **kwargs):
        defaults = {
            "agent_type": "main",
            "trust_mode": "allowlist",
            "allowlist": ["read", "write", "exec"],
            "environment": {"filesystem": True},
            "provider_capabilities": {"supports_tool_use": True},
        }
        defaults.update(kwargs)
        return ToolRegistry(**defaults)

    def test_allowlist_filter(self):
        reg = self._make_registry()
        reg.register(build_tool("read", categories=["core"]))
        reg.register(build_tool("write", categories=["core"]))
        reg.register(build_tool("message", categories=["external"]))

        manifest = reg.assemble()
        assert "read" in manifest.tool_names
        assert "write" in manifest.tool_names
        assert "message" not in manifest.tool_names  # not in allowlist

    def test_sandbox_mode_only_readonly(self):
        reg = self._make_registry(trust_mode="sandbox")
        reg.register(build_tool("read", read_only=True, categories=["core"]))
        reg.register(build_tool("write", categories=["core"]))
        reg.register(build_tool("exec", categories=["core"]))

        manifest = reg.assemble()
        assert "read" in manifest.tool_names  # read_only=True
        assert "write" not in manifest.tool_names  # not read_only
        assert "exec" not in manifest.tool_names  # not read_only

    def test_full_access(self):
        reg = self._make_registry(trust_mode="full", allowlist=[])
        reg.register(build_tool("read", categories=["core"]))
        reg.register(build_tool("exec", categories=["core"]))

        manifest = reg.assemble()
        assert "read" in manifest.tool_names
        assert "exec" in manifest.tool_names

    def test_provider_no_tool_use(self):
        reg = self._make_registry(
            provider_capabilities={"supports_tool_use": False}
        )
        reg.register(build_tool("read", categories=["core"]))
        manifest = reg.assemble()
        assert manifest.tool_names == []

    def test_environment_requirements(self):
        reg = self._make_registry(
            environment={"filesystem": True},
        )
        reg.register(build_tool(
            "gpu_tool",
            categories=["service"],
            env_requirements=["gpu_available"],
        ))
        manifest = reg.assemble()
        assert "gpu_tool" not in manifest.tool_names

    def test_service_tool_not_available(self):
        reg = self._make_registry(
            service_tools_available=set(),
        )
        reg.register(build_tool("tts", categories=["service"]))
        manifest = reg.assemble()
        assert "tts" not in manifest.tool_names

    def test_agent_type_buddy(self):
        reg = self._make_registry(
            agent_type="buddy",
            trust_mode="full",
            allowlist=[],
        )
        reg.register(build_tool("read", categories=["core"]))
        reg.register(build_tool("message", categories=["external"]))
        manifest = reg.assemble()
        assert "read" in manifest.tool_names
        assert "message" not in manifest.tool_names  # external blocked for buddy

    def test_agent_type_phone(self):
        reg = self._make_registry(
            agent_type="phone",
            trust_mode="full",
            allowlist=[],
        )
        reg.register(build_tool("read", categories=["core"]))
        reg.register(build_tool("web_search", categories=["web"]))
        manifest = reg.assemble()
        assert "read" in manifest.tool_names
        assert "web_search" not in manifest.tool_names  # web blocked for phone


# ---------------------------------------------------------------------------
# Layer 2: Invocation-time permissions
# ---------------------------------------------------------------------------

class TestInvocationPermissions:
    def setup_method(self):
        self.reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["read", "write", "exec"],
            provider_capabilities={"supports_tool_use": True},
        )
        self.reg.register(build_tool("read", read_only=True, categories=["core"]))
        self.reg.register(build_tool("write", categories=["core"]))
        self.reg.register(build_tool(
            "exec",
            destructive=True,
            categories=["core"],
        ))

    def test_allowlist_tool_allowed(self):
        assert self.reg.check_permission("read") == PermissionDecision.ALLOW

    def test_destructive_tool_asks(self):
        assert self.reg.check_permission("exec") == PermissionDecision.ASK

    def test_explicit_destructive_allow_bypasses_ask(self):
        self.reg.destructive_allowlist.add("exec")
        assert self.reg.check_permission("exec") == PermissionDecision.ALLOW

    def test_unknown_tool_denied(self):
        assert self.reg.check_permission("nonexistent") == PermissionDecision.DENY

    def test_custom_permission_hook_allow(self):
        def always_allow(**kwargs):
            return PermissionDecision.ALLOW
        tool = build_tool("custom", permission_check=always_allow, categories=["core"])
        self.reg.register(tool)
        self.reg.allowlist.add("custom")
        assert self.reg.check_permission("custom") == PermissionDecision.ALLOW

    def test_custom_permission_hook_deny(self):
        def always_deny(**kwargs):
            return PermissionDecision.DENY
        tool = build_tool("blocked", permission_check=always_deny, categories=["core"])
        self.reg.register(tool)
        self.reg.allowlist.add("blocked")
        assert self.reg.check_permission("blocked") == PermissionDecision.DENY


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def setup_method(self):
        self.reg = ToolRegistry()
        self.reg.register(build_tool(
            "read",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                },
            },
        ))

    def test_valid_input(self):
        result = self.reg.validate_input("read", {"path": "/tmp/test.txt"})
        assert result.ok is True

    def test_missing_required(self):
        result = self.reg.validate_input("read", {})
        assert result.ok is False
        assert "path" in " ".join(result.errors)

    def test_type_mismatch(self):
        result = self.reg.validate_input("read", {"path": 123})
        assert result.ok is False

    def test_unknown_tool(self):
        result = self.reg.validate_input("nonexistent", {})
        assert result.ok is False

    def test_custom_semantic_validator(self):
        def validate(data):
            if data.get("path", "").startswith("/etc/shadow"):
                return ValidationResult(valid=False, errors=["Access denied to sensitive path"])
            return ValidationResult(valid=True)

        self.reg.register(build_tool(
            "secure_read",
            validate_input=validate,
        ))
        result = self.reg.validate_input("secure_read", {"path": "/etc/shadow"})
        assert result.ok is False


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class TestToolManifest:
    def test_tool_names(self):
        tools = [
            build_tool("read"),
            build_tool("write"),
        ]
        manifest = ToolManifest(tools=tools)
        assert manifest.tool_names == ["read", "write"]

    def test_get_existing(self):
        tools = [build_tool("read")]
        manifest = ToolManifest(tools=tools)
        assert manifest.get("read").name == "read"

    def test_get_missing(self):
        manifest = ToolManifest()
        assert manifest.get("read") is None


# ---------------------------------------------------------------------------
# create_default_registry
# ---------------------------------------------------------------------------

class TestCreateDefaultRegistry:
    def test_defaults(self):
        reg = create_default_registry()
        assert reg.agent_type == "main"
        assert reg.trust_mode == "allowlist"
        assert len(reg.all_tools) == 0

    def test_custom(self):
        reg = create_default_registry(
            agent_type="buddy",
            trust_mode="full",
            tools_allow=["read", "memory_search"],
        )
        assert reg.agent_type == "buddy"
        assert "memory_search" in reg.allowlist


# ---------------------------------------------------------------------------
# Registry status
# ---------------------------------------------------------------------------

class TestRegistryStatus:
    def test_status(self):
        reg = ToolRegistry(
            agent_type="main",
            trust_mode="allowlist",
            allowlist=["read"],
        )
        reg.register(build_tool("read"))
        reg.register(build_tool("write"))  # not in allowlist

        status = reg.status()
        assert status["total_registered"] == 2
        assert status["visible_count"] == 1
        assert status["visible_names"] == ["read"]
        assert status["agent_type"] == "main"
