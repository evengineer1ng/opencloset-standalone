# Claw Substrate Integration Spec v1

## Purpose

Define the target split where OpenCloset becomes the orchestration plane and Claw becomes the execution substrate for foreground model turns, tool calling, and source-level context maintenance.

This document is intentionally concrete. It is not a product memo. It is the contract surface needed to stop carrying two competing agent runtimes.

## Decision

OpenCloset should no longer own a first-class foreground agent loop.

OpenCloset should own:

- workspaces
- build projects
- plans and plan items
- long-term memory and evidence
- delegation policy and ledger
- background scheduling and pastimes
- substrate routing policy
- node and array orchestration
- user-facing continuity, briefings, and resumability

Claw should own:

- prompt assembly for execution turns
- tool exposure and execution
- turn loop and recovery behavior
- execution-time token accounting
- idle compaction and maintenance artifacts for active execution sessions
- provider-specific prompt/tooling discipline

## Non-Goals

- OpenCloset does not become a thin chat UI with no state of its own.
- Claw does not become the system of record for workspaces, plans, or memory.
- The first integration does not require a multi-node array.
- The first integration does not require cross-node mutation or remote execution.

## Target Architecture

```text
User
  -> OpenCloset UI / API
     -> OpenCloset control plane
        - workspaces
        - plans
        - memory
        - scheduler / pastimes
        - delegation ledger
        - substrate policy
        -> Claw execution substrate adapter
           -> Claw runtime
              - prompt builder
              - turn loop
              - tool registry / executor
              - provider bindings
              - idle maintenance worker
```

## Authorities

### OpenCloset is authoritative for

- workspace metadata
- build-project metadata
- plan storage and activation
- session-to-workspace binding
- memory and evidence stores
- background work candidate selection
- execution policy selection
- substrate routing policy
- node and array topology
- user-visible event history

### Claw is authoritative for

- active execution state for a foreground run
- tool availability within a run
- tool result continuation payloads
- turn-level retry and recovery nudges
- execution-side compaction artifacts
- effective prompt composition for a run
- provider- and tool-specific repair logic

## Integration Components

### 1. OpenCloset Claw Adapter

OpenCloset needs one integration boundary instead of direct imports of local loop internals.

Responsibilities:

- create or resume substrate-backed execution sessions
- submit execution requests
- interrupt execution requests
- subscribe to execution event streams
- fetch maintenance artifacts and handoff state
- translate Claw-native payloads into OpenCloset canonical events

Transport can be HTTP, local IPC, or embedded process. The contract must stay transport-agnostic.

### 2. Canonical Event Relay

OpenCloset UI and storage should read one canonical event vocabulary. Claw-native event names should be mapped once at the adapter boundary.

### 3. Handoff Store

OpenCloset should store the durable handoff packet attached to the orchestration session. Claw may generate or enrich it, but OpenCloset owns retention and attachment to plans/workspaces.

### 4. Maintenance Artifact Store

Claw should generate execution maintenance artifacts. OpenCloset should index and surface them alongside workspace evidence and briefing systems.

## Session Model

OpenCloset session ids remain the user-facing continuity ids.

Claw gets a substrate session id per execution thread.

Required mapping:

```json
{
  "opencloset_session_id": "oc_sess_123",
  "claw_session_id": "claw_sess_456",
  "workspace_id": "ws_001",
  "build_project_id": "bp_001",
  "active_plan_id": "plan_001",
  "status": "active"
}
```

Rules:

- one OpenCloset session may have one current primary Claw substrate session
- rollover can create a successor OpenCloset session while preserving a handoff from the prior Claw session
- OpenCloset may later support multiple substrates per orchestration session, but v1 should assume one active execution substrate per foreground turn

## Execution Flow

### Foreground user turn

1. User sends message to OpenCloset.
2. OpenCloset resolves workspace, plan, memory, and policy context.
3. OpenCloset selects substrate policy and chooses Claw.
4. OpenCloset submits an execution request to Claw with:
   - user message
   - orchestration context slice
   - tool policy
   - memory/plan references
   - handoff references
5. Claw runs the turn and emits substrate events.
6. OpenCloset relays translated events to UI and durable event log.
7. Final answer, tool ledger, maintenance deltas, and updated handoff state are written back to OpenCloset.

### Idle background maintenance

1. OpenCloset scheduler decides a session is idle and maintenance-eligible.
2. OpenCloset issues a maintenance request to Claw, or Claw performs scheduled idle maintenance locally and reports results.
3. Claw writes updated maintenance artifacts.
4. OpenCloset stores artifact metadata, updates briefings, and marks the session warm.

## Execution Request Contract

OpenCloset sends a normalized execution request.

```json
{
  "contract_version": 1,
  "request_id": "exec_001",
  "opencloset_session_id": "oc_sess_123",
  "claw_session_id": "claw_sess_456",
  "workspace_id": "ws_001",
  "build_project_id": "bp_001",
  "active_plan": {
    "id": "plan_001",
    "title": "OpenCloset substrate migration",
    "active_goal": "Route execution through Claw while keeping OpenCloset as control plane",
    "next_item": "Write bridge contract and retire duplicate loop ownership"
  },
  "memory_context": {
    "entries": ["mem_1", "mem_2"],
    "mode": "reference"
  },
  "handoff": {
    "id": "handoff_007",
    "mode": "resume"
  },
  "tool_policy": {
    "enabled_tools": ["read", "write", "edit", "exec", "process"],
    "allow_destructive_tools": ["write", "edit"],
    "allowed_paths": ["D:/openclaw"]
  },
  "user_message": {
    "id": "msg_900",
    "content": "Continue the migration audit and update the docs."
  },
  "attachments": [],
  "run_mode": "foreground"
}
```

Rules:

- OpenCloset sends references when possible, not huge inline blobs.
- Claw owns prompt assembly from the normalized payload.
- OpenCloset does not preassemble provider prompts in v1.

## Canonical Event Contract

Every event relayed into OpenCloset should conform to one envelope.

```json
{
  "contract_version": 1,
  "event_id": "evt_001",
  "sequence": 42,
  "occurred_at": "2026-05-07T22:30:00.000Z",
  "source": "claw",
  "opencloset_session_id": "oc_sess_123",
  "claw_session_id": "claw_sess_456",
  "run_id": "run_999",
  "type": "tool_result",
  "data": {}
}
```

### Required canonical event types

- `run_queued`
- `run_started`
- `assistant_delta`
- `assistant_final`
- `tool_call`
- `tool_result`
- `usage`
- `provider_notice`
- `provider_stream_timeout`
- `tool_failure_pivot`
- `action_progress_blocked`
- `prompt_unanswered`
- `repeated_intent_blocked`
- `interrupt`
- `run_failed`
- `run_succeeded`
- `run_blocked`
- `maintenance_artifact_created`
- `handoff_prepared`

### Mapping rule

Claw-native events must be adapted into OpenCloset canonical names before they enter:

- `api/api/events.py`
- `api/api/run_lifecycle.py`
- `api/api/streaming.py`
- UI runtime step renderers

The UI must never need to know whether a turn ran on local OpenCloset loop code or on Claw.

## Final Run Result Contract

At run completion, Claw returns a normalized result block.

```json
{
  "contract_version": 1,
  "run_id": "run_999",
  "status": "succeeded",
  "finish_reason": "completed",
  "final_text": "I updated the integration docs and marked the loop duplication for retirement.",
  "transient_text": "...full visible run output...",
  "input_tokens": 4200,
  "output_tokens": 870,
  "provider_route": {
    "requested_provider": "auto",
    "resolved_provider": "llamacpp",
    "resolved_model": "qwen3.6-27b"
  },
  "tool_results": [],
  "maintenance_delta": {
    "new_artifact_ids": ["art_12"],
    "compaction_savings_tokens": 3100
  },
  "handoff_id": "handoff_008"
}
```

## Handoff Contract

The handoff packet is the durable resume packet between execution windows, rollover, and later authority transfer.

```json
{
  "contract_version": 1,
  "handoff_id": "handoff_008",
  "source": "claw",
  "opencloset_session_id": "oc_sess_123",
  "claw_session_id": "claw_sess_456",
  "workspace_id": "ws_001",
  "build_project_id": "bp_001",
  "active_plan_id": "plan_001",
  "status": "ready",
  "summary": "Migration work is in progress; execution should continue by replacing local loop ownership with Claw adapter calls.",
  "active_goal": "Route execution through Claw while keeping OpenCloset orchestration intact",
  "next_action": "Replace SessionAgentRunner execution path with substrate adapter",
  "open_threads": [
    "keep canonical OpenCloset events stable",
    "do not move workspace/planning authority into Claw"
  ],
  "last_user_intent": "write concrete integration and maintenance specs",
  "execution_state": {
    "last_run_status": "succeeded",
    "provider": "llamacpp",
    "model": "qwen3.6-27b"
  },
  "artifact_refs": {
    "micro_summary": "art_micro_001",
    "decision_tool_digest": "art_digest_001",
    "segment_summaries": ["art_seg_001", "art_seg_002"]
  },
  "context_guard": {
    "effective_tokens_used": 7200,
    "raw_tokens_used": 14800,
    "compaction_savings_tokens": 7600
  },
  "created_at": "2026-05-07T22:35:00.000Z"
}
```

Rules:

- OpenCloset persists the handoff and attaches it to the active session and plan.
- Claw may generate it because it has the execution truth.
- OpenCloset may enrich it with workspace and plan metadata.
- Handoffs must be reference-heavy and bounded.

## Maintenance Artifact Contract

Maintenance artifacts are execution-derived compression and continuity objects.

Shared envelope:

```json
{
  "contract_version": 1,
  "artifact_id": "art_123",
  "artifact_type": "segment_summary",
  "source": "claw",
  "opencloset_session_id": "oc_sess_123",
  "claw_session_id": "claw_sess_456",
  "workspace_id": "ws_001",
  "status": "valid",
  "start_position": 1,
  "end_position": 30,
  "content": {},
  "metadata": {},
  "created_at": "2026-05-07T22:40:00.000Z"
}
```

### Required artifact types in v1

- `micro_summary`
- `segment_summary`
- `decision_tool_digest`
- `file_state_digest`
- `compaction_marker`
- `handoff_candidate`
- `return_briefing_candidate`

### Segment summary content schema

```json
{
  "findings": [],
  "decisions": [],
  "files_touched": [],
  "unresolved_questions": [],
  "tool_results_worth_reusing": [],
  "next_likely_action": "",
  "raw_snippets": []
}
```

### File state digest content schema

```json
{
  "files": [
    {
      "path": "opencloset/api/agent/runner.py",
      "kind": "read",
      "purpose": "inspect prompt compaction seam",
      "key_findings": [
        "maintenance artifacts are injected before memory",
        "covered transcript ranges are removed from prompt assembly"
      ],
      "last_seen_run_id": "run_999"
    }
  ]
}
```

Rules:

- Claw owns generation.
- OpenCloset owns indexing, surfacing, and orchestration use.
- Prompt assembly in Claw should prefer artifact references and covered-range elision over replaying raw transcript.

## OpenCloset Module Changes Required by the Contract

OpenCloset should stop calling its local loop stack directly from:

- `api/agent/runner.py`
- `api/agent/loop.py`
- `api/agent/engine.py`
- `api/agent/prompt.py`

Those become either retired or Claw-side responsibilities.

OpenCloset routes that submit or observe execution should instead go through the adapter and canonical event relay.

## Migration Sequence

### Phase 1

- add a Claw adapter interface in OpenCloset
- preserve existing OpenCloset event shapes
- mirror one foreground run through Claw
- keep OpenCloset planning/workspace/memory ownership unchanged

### Phase 2

- move execution prompt assembly to Claw
- move tool execution ownership to Claw
- move execution compaction ownership to Claw
- keep OpenCloset maintenance artifacts table as the presentation/index surface

### Phase 3

- retire duplicate OpenCloset loop/prompt/tool execution paths
- keep only adapter, canonical event relay, and control-plane services in OpenCloset

## Success Criteria

- one OpenCloset session can execute foreground turns entirely through Claw
- OpenCloset UI still consumes the same canonical event stream
- handoff packets survive rollover and resume cleanly
- idle maintenance happens before the next user prompt, not during it
- OpenCloset retains authority over workspaces, plans, memory, and delegation