# Tool Registry — dynamic tool pool assembly + two-layer permissions
#
# Assembles the visible tool pool at runtime based on agent type, trust mode,
# environment, provider capabilities, and explicit allowlist.
#
# Two-layer permission model (from Claude Code research):
#   Layer 1 — Assembly time: filter disallowed tools from model-visible pool
#   Layer 2 — Invocation time: validate specific input, decide allow/ask/deny
#
# Each tool is a rich contract with schema, permissions, concurrency metadata,
# interrupt behavior, and result serialization.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission decisions (Layer 2: invocation time)
# ---------------------------------------------------------------------------

class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"          # prompt user for approval
    DENY = "deny"        # reject invocation


# ---------------------------------------------------------------------------
# Input validation result (Layer 2)
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of validating tool input against schema + semantic checks."""
    valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.valid and not self.errors


# ---------------------------------------------------------------------------
# Tool Contract — the rich definition object
# ---------------------------------------------------------------------------

@dataclass
class ToolContract:
    """Rich tool definition with full metadata for registry assembly and
    invocation-time enforcement.

    Fields:
        name: Unique tool identifier (e.g. "read", "exec")
        description: Human-readable description for model prompt text
        input_schema: JSON schema dict for input validation
        execute: Callable that performs the tool action
        validate_input: Optional semantic validation beyond schema
        permission_check: Optional per-invocation permission hook
        concurrency_safe: Can this tool run in parallel with others?
        read_only: Does not modify state (safe for sandbox mode)
        destructive: Irreversible operation (delete, overwrite, etc.)
        interruptible: Can be safely aborted mid-execution
        result_serializer: How to format results back to the model
        categories: Tags for filtering (core, extended, web, service, external)
        env_requirements: Environment features required to run this tool
    """

    # Identity
    name: str
    description: str = ""

    # Input contract
    input_schema: dict[str, Any] = field(default_factory=dict)
    validate_input: Callable | None = None

    # Execution
    execute: Callable | None = None

    # Permission (Layer 2: invocation time)
    permission_check: Callable | None = None

    # Concurrency metadata
    concurrency_safe: bool = False

    # Safety flags
    read_only: bool = False
    destructive: bool = False

    # Interrupt behavior
    interruptible: bool = False

    # Result serialization
    result_serializer: Callable | None = None

    # Filtering tags
    categories: list[str] = field(default_factory=list)
    env_requirements: list[str] = field(default_factory=list)

    # Dynamic availability (set by registry)
    available: bool = True

    def to_prompt_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for the tool manifest in the system prompt."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_provider_dict(self) -> dict[str, Any]:
        """Serialize to an OpenAI-compatible tool definition for provider APIs."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


# ---------------------------------------------------------------------------
# Tool manifest — what the model sees
# ---------------------------------------------------------------------------

@dataclass
class ToolManifest:
    """Filtered set of tools visible to the model for a given session/turn."""
    tools: list[ToolContract] = field(default_factory=list)
    agent_type: str = ""
    trust_mode: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def get(self, name: str) -> ToolContract | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None


# ---------------------------------------------------------------------------
# build_tool() — convenience factory with safe defaults
# ---------------------------------------------------------------------------

def build_tool(
    name: str,
    *,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
    execute: Callable | None = None,
    validate_input: Callable | None = None,
    permission_check: Callable | None = None,
    concurrency_safe: bool = False,
    read_only: bool = False,
    destructive: bool = False,
    interruptible: bool = False,
    result_serializer: Callable | None = None,
    categories: list[str] | None = None,
    env_requirements: list[str] | None = None,
) -> ToolContract:
    """Create a ToolContract with safe defaults.

    Safe defaults:
        concurrency_safe = False
        read_only = False
        destructive = False
        interruptible = False

    Pass the tool through the registry to filter by context before
    exposing to the model.
    """
    return ToolContract(
        name=name,
        description=description,
        input_schema=input_schema or {},
        execute=execute,
        validate_input=validate_input,
        permission_check=permission_check,
        concurrency_safe=concurrency_safe,
        read_only=read_only,
        destructive=destructive,
        interruptible=interruptible,
        result_serializer=result_serializer,
        categories=categories or [],
        env_requirements=env_requirements or [],
    )


# ---------------------------------------------------------------------------
# Sandbox-safe tools
# ---------------------------------------------------------------------------

SANDBOX_SAFE_CATEGORIES = {"core"}
SANDBOX_READ_ONLY_TOOLS = {
    "read",
    "memory_get",
    "memory_search",
    "web_fetch",
}


# ---------------------------------------------------------------------------
# Category constants
# ---------------------------------------------------------------------------

CATEGORY_CORE = "core"
CATEGORY_EXTENDED = "extended"
CATEGORY_WEB = "web"
CATEGORY_SERVICE = "service"
CATEGORY_EXTERNAL = "external"


# ---------------------------------------------------------------------------
# Tool Registry — dynamic pool assembly + filtering
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Dynamic tool pool assembled at runtime.

    Filters tools based on:
    - Agent type (main, buddy, phone)
    - Trust mode (allowlist, full, sandbox)
    - Environment availability
    - Provider capabilities
    - Explicit allowlist from config
    - Service-tool availability

    Two-layer permissions:
    Layer 1 (assembly): filter disallowed tools from model-visible pool
    Layer 2 (invocation): validate input + check permissions at call time
    """

    def __init__(
        self,
        *,
        agent_type: str = "main",
        trust_mode: str = "allowlist",
        allowlist: list[str] | None = None,
        destructive_allowlist: list[str] | None = None,
        environment: dict[str, bool] | None = None,
        provider_capabilities: dict[str, Any] | None = None,
        service_tools_available: set[str] | None = None,
    ):
        self.agent_type = agent_type
        self.trust_mode = trust_mode
        self.allowlist = set(allowlist or [])
        self.destructive_allowlist = set(destructive_allowlist or [])
        self.environment = environment or {}
        self.provider_capabilities = provider_capabilities or {}
        self.service_tools_available = service_tools_available or set()

        # Tool store — all registered tools regardless of filtering
        self._tools: dict[str, ToolContract] = {}

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def register(self, tool: ToolContract) -> ToolContract:
        """Register a tool in the pool. Does not filter — filtering happens
        on manifest assembly."""
        self._tools[tool.name] = tool
        return tool

    def register_many(self, tools: list[ToolContract]) -> None:
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the pool."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    @property
    def all_tools(self) -> list[ToolContract]:
        """Return all registered tools (before filtering)."""
        return list(self._tools.values())

    # -----------------------------------------------------------------------
    # Availability check (environment + service tools)
    # -----------------------------------------------------------------------

    def _is_available(self, tool: ToolContract) -> bool:
        """Check if a tool can run in the current environment."""
        if not tool.available:
            return False

        # Check environment requirements
        for req in tool.env_requirements:
            if not self.environment.get(req, False):
                return False

        # Service tools only available if explicitly started
        if CATEGORY_SERVICE in tool.categories:
            if tool.name not in self.service_tools_available:
                return False

        return True

    # -----------------------------------------------------------------------
    # Layer 1: Assembly-time filtering
    # -----------------------------------------------------------------------

    def _passes_assembly_filter(self, tool: ToolContract) -> bool:
        """Layer 1: determine if tool should be in the model-visible pool."""

        # Provider capability check
        if not self.provider_capabilities.get("supports_tool_use", True):
            return False

        # Availability check
        if not self._is_available(tool):
            return False

        # Trust mode filtering
        if self.trust_mode == "sandbox":
            # Sandbox: only read-only tools
            if not tool.read_only and tool.name not in SANDBOX_READ_ONLY_TOOLS:
                return False

        if self.trust_mode == "allowlist":
            # Allowlist: tool must be in the explicit list
            if self.allowlist:
                if tool.name not in self.allowlist:
                    return False

        # Agent type filtering (e.g., Buddy gets fewer tools than Clo)
        if self.agent_type == "buddy":
            # Buddy: core + extended + memory, no external messaging
            restricted_for_buddy = {CATEGORY_EXTERNAL}
            if any(cat in restricted_for_buddy for cat in tool.categories):
                return False

        if self.agent_type == "phone":
            # Phone: core + memory, minimal set
            restricted_for_phone = {CATEGORY_EXTERNAL, CATEGORY_WEB}
            if any(cat in restricted_for_phone for cat in tool.categories):
                return False

        return True

    # -----------------------------------------------------------------------
    # Assemble manifest
    # -----------------------------------------------------------------------

    def assemble(self) -> ToolManifest:
        """Assemble the model-visible tool manifest.

        Applies Layer 1 filtering: agent type, trust mode, environment,
        provider capabilities, allowlist.

        Returns a ToolManifest with only tools the model should see.
        """
        visible = [
            tool for tool in self._tools.values()
            if self._passes_assembly_filter(tool)
        ]

        return ToolManifest(
            tools=visible,
            agent_type=self.agent_type,
            trust_mode=self.trust_mode,
        )

    # -----------------------------------------------------------------------
    # Layer 2: Invocation-time permission check
    # -----------------------------------------------------------------------

    def check_permission(
        self,
        tool_name: str,
        *,
        input_data: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> PermissionDecision:
        """Layer 2: validate specific invocation.

        Steps:
        1. Find tool contract
        2. Validate input against schema
        3. Run semantic validation hook (if defined)
        4. Run permission check hook (if defined)

        Returns PermissionDecision: ALLOW | ASK | DENY
        """
        tool = self._tools.get(tool_name)
        if not tool:
            logger.warning("Permission check for unknown tool: %s", tool_name)
            return PermissionDecision.DENY

        # Check if tool is in the visible manifest (already filtered by Layer 1)
        manifest = self.assemble()
        if tool not in manifest.tools:
            logger.warning("Tool %s not in visible manifest for agent=%s trust=%s",
                          tool_name, self.agent_type, self.trust_mode)
            return PermissionDecision.DENY

        # Run tool's own permission hook (if defined)
        if tool.permission_check:
            decision = tool.permission_check(
                input_data=input_data or {},
                session_id=session_id,
                trust_mode=self.trust_mode,
                agent_type=self.agent_type,
            )
            if isinstance(decision, PermissionDecision):
                return decision

        # Destructive tools in allowlist mode require explicit approval unless
        # the session policy explicitly grants them.
        if tool.destructive and self.trust_mode == "allowlist":
            if tool.name in self.destructive_allowlist:
                return PermissionDecision.ALLOW
            return PermissionDecision.ASK

        return PermissionDecision.ALLOW

    def validate_input(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> ValidationResult:
        """Validate input data against tool schema + semantic checks."""
        tool = self._tools.get(tool_name)
        if not tool:
            return ValidationResult(
                valid=False,
                errors=[f"Unknown tool: {tool_name}"],
            )

        errors = []
        warnings = []

        # Schema validation (basic)
        schema = tool.input_schema
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for req in required:
            if req not in input_data:
                errors.append(f"Missing required parameter: {req}")

        # Type checks
        for prop, spec in properties.items():
            if prop in input_data:
                expected_type = spec.get("type")
                if expected_type and not self._check_type(input_data[prop], expected_type):
                    errors.append(
                        f"Parameter '{prop}' expected {expected_type}, "
                        f"got {type(input_data[prop]).__name__}"
                    )

        # Semantic validation hook
        if tool.validate_input:
            result = tool.validate_input(input_data)
            if isinstance(result, ValidationResult):
                errors.extend(result.errors)
                warnings.extend(result.warnings)
            elif isinstance(result, str):
                errors.append(result)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_type(self, value: Any, expected: str) -> bool:
        """Basic JSON schema type check."""
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected_type = type_map.get(expected)
        if expected_type is None:
            return True  # unknown type, skip
        return isinstance(value, expected_type)

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Registry state for diagnostics."""
        manifest = self.assemble()
        return {
            "total_registered": len(self._tools),
            "visible_count": len(manifest.tools),
            "visible_names": manifest.tool_names,
            "agent_type": self.agent_type,
            "trust_mode": self.trust_mode,
            "allowlist": sorted(self.allowlist) if self.allowlist else [],
            "destructive_allowlist": sorted(self.destructive_allowlist) if self.destructive_allowlist else [],
        }


# ---------------------------------------------------------------------------
# Convenience: create default core tool registry
# ---------------------------------------------------------------------------

def create_default_registry(
    *,
    agent_type: str = "main",
    trust_mode: str = "allowlist",
    tools_allow: list[str] | None = None,
    destructive_tools_allow: list[str] | None = None,
    environment: dict[str, bool] | None = None,
    provider_capabilities: dict[str, Any] | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry with standard configuration.

    Tools must be registered separately (e.g., from tool implementation modules).
    """
    return ToolRegistry(
        agent_type=agent_type,
        trust_mode=trust_mode,
        allowlist=tools_allow,
        destructive_allowlist=destructive_tools_allow,
        environment=environment,
        provider_capabilities=provider_capabilities,
    )
