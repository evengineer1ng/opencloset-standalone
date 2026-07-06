# 09 — Memory Tools

## 1. Overview

Memory tools provide semantic memory retrieval and storage. Located in `api/tools/memory_tools.py`.

Two tools: `memory_search` (retrieval) and `memory_add` (storage).

---

## 2. memory_search

Semantic memory retrieval.

### 2.1 Input Schema

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": {"type": "string", "description": "Search query"},
    "limit": {"type": "integer", "description": "Max results (default 5)"}
  }
}
```

### 2.2 Behavior

1. Validate query not empty.
2. Query `memory_entries` table for matching entries.
3. Sort by relevance (embedding similarity or text match).
4. Return top `limit` results.

### 2.3 Output Format

```
Memory search results for "<query>":

1. [tags] text
   source: <source>
   created: <timestamp>

2. ...
```

### 2.4 Permission

`PermissionDecision.ALLOW` — read-only operation.

### 2.5 Validation

`validate_memory_search_input()`:
- Query not empty.

---

## 3. memory_add

Store a semantic memory entry.

### 3.1 Input Schema

```json
{
  "type": "object",
  "required": ["text"],
  "properties": {
    "text": {"type": "string", "description": "Memory text to store"},
    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
    "source": {"type": "string", "description": "Source identifier"}
  }
}
```

### 3.2 Behavior

1. Validate text not empty.
2. Insert into `memory_entries` table.
3. Return confirmation with entry ID.

### 3.3 Output Format

```
Memory entry stored.
id: <id>
text: <text>
tags: [<tags>]
source: <source>
```

### 3.4 Permission

`PermissionDecision.ALLOW` — write to memory table.

### 3.5 Validation

`validate_memory_add_input()`:
- Text not empty.
- Text length ≤ 4000 chars.

---

## 4. Database: memory_entries Table

| Column | Type | Description |
|---|---|---|
| `id` | TEXT (PK) | Entry UUID |
| `text` | TEXT | Memory content |
| `embedding` | TEXT (JSON) | Vector embedding (nullable) |
| `tags` | TEXT (JSON) | Tag list |
| `source` | TEXT | Source identifier |
| `session_id` | TEXT (nullable) | Associating session |
| `created_at` | TIMESTAMP | Creation time |

---

## 5. Retrieval Strategy

Current implementation uses text-based search (LIKE queries or FTS if configured). Semantic embedding-based search is planned for future enhancement.

`memory_search` queries the `memory_entries` table and returns results sorted by relevance.

---

## 6. Integration with Prompt Assembly

Memory entries are retrieved during prompt assembly to inject relevant context:

- `PromptBuilder` calls `memory_search` with query derived from current conversation topic.
- Retrieved entries injected as context section in system prompt.
- Maintenance artifacts (micro-summaries) take priority over memory during prompt truncation.

---

## 7. Tool Registration

`register_memory_tools(registry)`:
- Registers `memory_search` and `memory_add`.
- Both tools use `PermissionDecision.ALLOW`.
- Returns list of registered ToolContracts.
