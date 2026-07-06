# 07 — Tool Registry & Permissions

## 1. Overview

The tool system provides structured tool invocation for the agent loop. It consists of three layers:

1. **ToolRegistry** (`api/tools/registry.py`) — tool catalog, permission checks, input validation.
2. **ToolExecutor** (`api/tools/executor.py`) — sequential tool execution, batch results, interrupt handling.
3. **ToolCallNormalizer** (`api/tools/normalizer.py`) — provider output → internal format conversion.

---

## 2. ToolRegistry (`api/tools/registry.py`)

### 2.1 ToolContract

The unit of tool definition:

```python
@dataclass
class ToolContract:
    name: str
    description: str
    input_schema: dict        # JSON Schema for arguments
    execute: Callable | None  # Handler function
    result_serializer: Callable | None  # Optional output formatter
    permission_decision: PermissionDecision
    validate_input: Callable | None  # Semantic validation
    permission_check: Callable | None  # Dynamic permission check
    read_only: bool           # No filesystem writes
    categories: list[str]
```

### 2.2 ToolManifest

Runtime metadata for a registered tool:

```python
@dataclass
class ToolManifest:
    name: str
    description: str
    input_schema: dict
    permission_decision: PermissionDecision
    read_only: bool
    categories: list[str]
```

### 2.3 Registry API

| Method | Purpose |
|---|---|
| `register_tool(name, fn, schema, desc, permissions)` | Add tool to catalog |
| `register_many(contracts)` | Batch register ToolContracts |
| `execute(tool_name, arguments, permission_context)` | Resolve permission, call handler |
| `resolve_permission(decision, context)` | Evaluate PermissionDecision |
| `validate_input(tool_name, input_data)` | Schema + semantic validation |
| `check_permission(tool_name, input_data, session_id)` | Dynamic permission check |
| `list_tools()` | Return all registered tool names |
| `get_tool_schema(tool_name)` | Return tool manifest/schema |
| `get_tool_manifest(tool_name)` | Return ToolManifest |

### 2.4 build_tool() Helper

Convenience factory for creating ToolContracts:

```python
build_tool(
    name="read_file",
    description="Read file contents...",
    input_schema={...},
    execute=read_file_handler,
    validate_input=validate_read_input,
    permission_decision=PermissionDecision.ALLOW,
    read_only=True,
    categories=[CATEGORY_WORKSPACE],
)
```

---

## 3. Permission System

### 3.1 PermissionDecision Enum

| Decision | Behavior |
|---|---|
| `ALLOW` | Always execute without asking |
| `ASK` | Require user approval before execution |
| `DENY` | Never execute |

### 3.2 Permission Resolution Flow

1. **Static decision:** `ToolContract.permission_decision` — base policy per tool.
2. **Dynamic check:** `ToolContract.permission_check` — contextual check (e.g., path scope, command safety).
3. **Override:** `ToolExecutor.ask_approval` callback — user-facing approval hook for ASK tools.

### 3.3 Dynamic Permission Checks

Tools can define `permission_check` functions for context-sensitive decisions:

- **`read` tool:** Checks path against allowed read allowlist.
- **`exec` tool:** `make_exec_permission_check()` — validates workdir and command path tokens against allowed paths.
- **`write`/`edit` tools:** Require write confirmation + path scope validation.

---

## 4. Input Validation

Two-layer validation:

### 4.1 Schema Validation (Layer 1)

`validate_schema(data, schema)` — validates input against JSON Schema:
- Required fields present.
- Types match schema definitions.
- Enum values within allowed set.

Returns `ValidationResult(valid, errors)`.

### 4.2 Semantic Validation (Layer 2)

Tool-specific `validate_input` functions:

- **`read`:** Path exists, within read allowlist, not a binary file.
- **`write`:** Path within write scope, parent directory exists.
- **`exec`:** Command not empty, timeout within limits.
- **`process`:** Action is valid enum, sessionId present when required.
- **`memory_search`:** Query not empty.

---

## 5. ToolExecutor (`api/tools/executor.py`)

### 5.1 Execution Flow

Per tool call:
1. Look up `ToolContract` in registry.
2. Validate input (schema + semantic).
3. Check permission (Layer 2 dynamic check).
4. Handle ASK (approval hook → ALLOW/DENY).
5. Execute tool function.
6. Serialize result via `ToolContract.result_serializer` (or default stringify).
7. Return `ToolResult`.

### 5.2 ExecutionStatus Enum

| Status | Meaning |
|---|---|
| `SUCCESS` | Tool executed normally |
| `VALIDATION_FAILED` | Input validation error |
| `PERMISSION_DENIED` | Permission check blocked |
| `ASK_PENDING` | Awaiting user approval |
| `EXECUTION_ERROR` | Tool function raised exception |
| `INTERRUPTED` | Run interrupted during execution |
| `TOOL_NOT_FOUND` | Unknown tool name |

### 5.3 Batch Execution

`execute_all(calls, session_id)` — executes tool calls sequentially:
- Stops on interrupt, ASK pending, or max tool calls per turn.
- Returns `ToolBatchResult` with aggregate stats: `success_count`, `all_succeeded`, `any_asks`, `any_denied`, `any_errors`.

### 5.4 Interrupt Handling

- `interrupt_check` callback or `runtime.is_interrupted` — checked before each tool call.
- Remaining calls marked `INTERRUPTED` on abort.
- `runtime.complete_tool_call(call_id)` — updates runtime tracking after each result.

---

## 6. ToolCallNormalizer (`api/tools/normalizer.py`)

Bridges provider output to internal format:

1. **Call ID normalization:** Ensures consistent `call_<hex>` format.
2. **Argument parsing:** JSON-string → dict. Handles malformed JSON with repair attempts (trailing commas, bare strings).
3. **Unknown tool filtering:** Validates tool name exists in registry.
4. **Batch normalization:** `normalize_batch()` — filters failures, logs warnings.

Returns `NormalizationResult(success, executor_call, error)`.

---

## 7. Tool Categories

| Category | Tools |
|---|---|
| `CATEGORY_CORE` | `exec`, `process` |
| `CATEGORY_WORKSPACE` | `read`, `write`, `edit`, `list_directory` |
| `CATEGORY_MEMORY` | `memory_search`, `memory_add` |
| `CATEGORY_PLANNING` | `create_plan`, `create_slice`, `update_slice`, `checklist`, `build_project`, `workspace_status`, `list_workspace` |

---

## 8. Tool Result Serialization

`ToolResult.to_dict()` produces:
```json
{
  "call_id": "call_abc123",
  "tool_name": "read",
  "status": "success",
  "content": "...",
  "error": null,
  "token_estimate": 42
}
```

Token estimation: `len(content) // 4` (English assumption).
