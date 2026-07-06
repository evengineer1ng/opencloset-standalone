# 08 — Filesystem Tools

## 1. Overview

Filesystem tools provide bounded file and directory operations. Located in `api/tools/filesystem.py`.

All tools enforce path scope validation against a configurable `read_allowlist` (and write scope for mutating operations).

---

## 2. Path Scope System

### 2.1 Allowed Paths

`normalize_allowed_paths(allowed_paths, workspace)` — resolves path list:
- Relative paths resolved against `workspace` root.
- `~` expanded via `Path.expanduser()`.
- Paths resolved to absolute form.

### 2.2 Scope Validation

`is_path_within_scope(target_path, allowed_paths)` — checks if target is within any allowed path:
- Resolves `target_path` to absolute form.
- Checks `target_path.is_relative_to(allowed_path)` for each allowed path.

### 2.3 Read Allowlist

Configurable per-session or globally. Default: workspace root (`D:\openclaw`).

Tools `read`, `list_directory`, and `exec` (command path tokens) validate against this allowlist.

---

## 3. read

Read file contents. Bounded to read allowlist.

### 3.1 Input Schema

```json
{
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {"type": "string", "description": "File path"},
    "offset": {"type": "integer", "description": "Line offset (1-indexed)"},
    "limit": {"type": "integer", "description": "Max lines to read"}
  }
}
```

### 3.2 Behavior

1. Resolve path (relative → absolute via workdir).
2. Check read allowlist → `PermissionDecision.DENY` if outside scope.
3. Check file exists and is a file.
4. Read content (text mode, UTF-8).
5. Handle images (jpg, png, gif, webp) — return metadata, not content.
6. Truncate to `limit` lines if specified, starting at `offset`.
7. Permission: `PermissionDecision.ALLOW` (dynamic path check).

### 3.3 Semantic Validation

`validate_read_input()`:
- Path not empty.
- Resolved path within read allowlist.
- File exists.
- Is a file (not directory).
- Not a known binary file.

---

## 4. write

Create or overwrite files.

### 4.1 Input Schema

```json
{
  "type": "object",
  "required": ["path", "content"],
  "properties": {
    "path": {"type": "string", "description": "File path"},
    "content": {"type": "string", "description": "File content"}
  }
}
```

### 4.2 Behavior

1. Resolve path.
2. Validate write scope (path within allowed paths).
3. Validate parent directory exists.
4. Write content to file (creates parent dirs automatically).
5. Permission: `PermissionDecision.ASK` (requires confirmation).

### 4.3 Semantic Validation

`validate_write_input()`:
- Path not empty.
- Content not empty.
- Resolved path within write scope.
- Parent directory exists.

---

## 5. edit

Make precise text replacements in existing files.

### 5.1 Input Schema

```json
{
  "type": "object",
  "required": ["path", "edits"],
  "properties": {
    "path": {"type": "string"},
    "edits": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "oldText": {"type": "string"},
          "newText": {"type": "string"}
        },
        "required": ["oldText", "newText"]
      }
    }
  }
}
```

### 5.2 Behavior

1. Read file contents.
2. For each edit: find `oldText` in content, replace with `newText`.
3. Validate: `oldText` must match unique, non-overlapping region.
4. Write modified content back.
5. Return list of applied edits with line numbers.
6. Permission: `PermissionDecision.ASK` (requires confirmation).

### 5.3 Constraints

- Each `oldText` must be unique in the file.
- Edits must not overlap.
- If `oldText` not found, edit is skipped with error.

---

## 6. list_directory

List directory contents.

### 6.1 Input Schema

```json
{
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {"type": "string"},
    "recursive": {"type": "boolean"}
  }
}
```

### 6.2 Behavior

1. Resolve path, check read allowlist.
2. List entries (files and directories).
3. Return formatted list with types (file/directory).
4. Recursive mode: traverse subdirectories.
5. Permission: `PermissionDecision.ALLOW` (read-only).

---

## 7. exec (Shell Commands)

Located in `api/tools/process.py`. Executes shell commands with foreground and background modes.

### 7.1 Foreground Mode

- Runs command to completion.
- Captures stdout + stderr.
- Timeout default: 300s (max: 1800s).
- Output truncated to 256KB.
- Returns exit code + output.

### 7.2 Background Mode

- Spawns process, returns `session_id` handle.
- Output captured to temp file.
- Managed via `process` tool (poll, log, write, kill).
- Max 20 background processes per session.

### 7.3 Path Safety

`make_exec_permission_check()`:
- Validates workdir against allowed paths.
- Extracts path tokens from command string.
- Denies if any path token is outside scope.

### 7.4 Process Tool

`process` tool manages background processes:

| Action | Purpose |
|---|---|
| `list` | List all background processes |
| `poll` | Check process status/completion |
| `log` | Read captured output |
| `write` | Send data to stdin |
| `kill` | Terminate process |

---

## 8. ProcessStore

In-memory registry for background processes:

- `register(handle, popen)` → returns session_id.
- `get(session_id)` → ProcessHandle.
- `terminate(session_id)` → kill process.
- `cleanup(session_id)` → remove after completion.
- `list_all()` → all active handles.

`ProcessHandle` tracks: session_id, command, workdir, pid, start_time, return_code, terminated, output_path.

---

## 9. Tool Registration

`register_filesystem_tools(registry, read_allowlist, workdir)`:
- Registers `read`, `write`, `edit`, `list_directory`.
- Configures permission checks with provided allowlist and workdir.
- Returns list of registered ToolContracts.

`register_process_tools(registry, store, workdir, allowed_paths)`:
- Registers `exec`, `process`.
- Configures exec permission check with path scope.
- Returns list of registered ToolContracts.
