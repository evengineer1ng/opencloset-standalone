# Claw Runtime Wiring Plan v1

## Decision

The first effective OpenClaw -> OpenCloset integration should not be a big-bang rewrite.

It should be a controlled replacement of OpenCloset's current execution owner with a substrate adapter while preserving:

- OpenCloset session ids
- OpenCloset run lifecycle rows
- OpenCloset canonical event vocabulary
- OpenCloset SSE/UI contracts
- OpenCloset authority over plans, workspaces, memory, queueing, briefings, and transient windows

The immediate goal is simple:

**Make OpenCloset call OpenClaw for foreground execution without forcing the rest of OpenCloset to care how execution happened.**

## What The Code Says Right Now

The current local execution seam is much narrower than the rest of the app:

- [opencloset/api/api/app.py](d:/openclaw/opencloset/api/api/app.py) constructs `SessionAgentRunner` and assigns it to `app.agent_runner`.
- [opencloset/api/api/routes.py](d:/openclaw/opencloset/api/api/routes.py#L930) calls `app.agent_runner.execute_run(session_id, run_id)`.
- [opencloset/api/api/routes.py](d:/openclaw/opencloset/api/api/routes.py#L987) calls `app.agent_runner.request_interrupt(run_id)`.
- [opencloset/api/api/clo_queue.py](d:/openclaw/opencloset/api/api/clo_queue.py#L420) uses the same `execute_run(...)` seam for queued work.
- [opencloset/api/api/agent_channels.py](d:/openclaw/opencloset/api/api/agent_channels.py#L489) uses the same seam for headless channel runs.
- [opencloset/api/api/runtime_diagnostics.py](d:/openclaw/opencloset/api/api/runtime_diagnostics.py#L562) currently reaches into `agent_runner` private routing/provider helpers for recovery suggestions.
- [opencloset/api/api/delegation.py](d:/openclaw/opencloset/api/api/delegation.py#L274) also reaches into local provider plumbing and private runner helpers.

That means the real migration target is not "the whole backend". The real first target is:

- the `app.agent_runner` abstraction boundary,
- plus the two outliers that currently rely on runner internals: diagnostics and delegation.

## Why This Is The Right First Cut

This cut is effective because it preserves almost everything OpenCloset already does well:

- `RunManager` still owns statuses, persistence, and SSE event emission.
- `event_logger.py` still owns durable event history.
- `routes.py`, `clo_queue.py`, and `agent_channels.py` still submit work the same way.
- the UI still listens to the same run/event streams.
- the queue, transient windows, workspace runtime, captures, and briefings do not need to know whether the execution engine was local or Claw-backed.

In other words, this cut changes the execution substrate without forcing a control-plane rewrite.

## Recommended First Architecture

Introduce a single runtime-facing interface inside OpenCloset.

Example shape:

```python
class ExecutionRuntime(Protocol):
    def execute_run(self, session_id: str, run_id: str) -> RunExecutionResult: ...
    def request_interrupt(self, run_id: str) -> bool: ...
```

Then provide two implementations:

1. `LocalSessionExecutionRuntime`
   - wraps the current `SessionAgentRunner`
   - keeps current behavior as fallback

2. `ClawExecutionRuntime`
   - submits the turn to OpenClaw
   - translates OpenClaw output/events back into OpenCloset canonical events
   - returns the same `RunExecutionResult` shape expected by current callers

The app wiring then becomes:

- `app.execution_runtime = ...`
- existing callers stop depending on `app.agent_runner`
- `app.agent_runner` can temporarily alias the local implementation only where legacy code still needs it during migration

## The Best Transport For The First Integration

Use a **process boundary first**, not a deep code embedding.

Why:

- `openclaw` is already installed as a CLI in the environment.
- `openclaw agent --help` confirms a non-interactive turn command exists.
- `openclaw agent --local --json` gives a practical first execution target.
- a process boundary keeps OpenCloset transport-agnostic and avoids entangling Python app state with OpenClaw's Node runtime internals too early.

So the first concrete transport should be:

- OpenCloset spawns `openclaw agent ...`
- captures structured result output
- maps result back into OpenCloset run lifecycle and event storage

Do not start by importing or modifying OpenClaw internals from the Flask app.

## Phase 1: Substrate Adapter With Final-Result Compatibility

This is the smallest useful implementation.

### Goal

Allow OpenCloset to execute a foreground run through OpenClaw while preserving the current API result shape.

### Scope

- no full event streaming yet
- no tool-level replay parity yet
- no queue redesign
- no UI redesign required to prove the substrate bridge

### How it works

1. OpenCloset still accepts `POST /api/sessions/<session_id>/messages` exactly as it does now.
2. OpenCloset still creates the run row and stores attachments/capture refs.
3. When execution begins, `ClawExecutionRuntime.execute_run(...)` builds a normalized execution request from OpenCloset state.
4. The adapter invokes OpenClaw through `openclaw agent --local --json`.
5. The adapter captures stdout/stderr, exit code, timeout, and final JSON.
6. The adapter writes canonical OpenCloset final events and returns `RunExecutionResult`.
7. `RunManager`, `routes.py`, `clo_queue.py`, and `agent_channels.py` continue to behave the same from the outside.

### Required OpenCloset request payload

The adapter should assemble a normalized execution input using existing OpenCloset state:

- session id
- run id
- user message content
- recent transcript slice
- active plan slice
- handoff/continuity slice when present
- attachment refs and capture refs
- tool policy
- workspace root
- workspace id / build project id

The adapter should prefer compact, structured slices over giant prompt concatenation.

### Important limitation

Phase 1 is only acceptable as a bridge if we treat it as a compatibility layer, not the final design.

It proves the substrate swap. It does not yet prove rich streaming.

## Phase 2: Preserve OpenCloset's Event Spine

After the final-result bridge works, the next move is to stop treating OpenClaw as a black box and start relaying execution events into OpenCloset's canonical vocabulary.

OpenCloset should remain authoritative for the public event spine defined in [opencloset/api/api/events.py](d:/openclaw/opencloset/api/api/events.py).

That means `ClawExecutionRuntime` needs an event mapper that emits:

- `run_started`
- `assistant_delta`
- `assistant_final`
- `tool_call`
- `tool_result`
- `usage`
- `provider_notice`
- `interrupt`
- `run_failed`
- `run_succeeded`
- `maintenance_artifact_created`
- `handoff_prepared`

The key rule is:

**OpenCloset UI and persistence should only see canonical OpenCloset event names, even when OpenClaw is the execution engine.**

This preserves the current UI contract and avoids pushing OpenClaw-native event assumptions into the frontend.

## Phase 3: Use A Stable OpenClaw Session Mapping

Do not let OpenCloset session identity collapse into raw OpenClaw session identity.

Instead, add a small mapping table like:

```json
{
  "opencloset_session_id": "oc_sess_123",
  "claw_session_key": "agent:opencloset:oc_sess_123",
  "claw_session_id": "optional-claw-native-id",
  "workspace_id": "ws_001",
  "status": "active"
}
```

This gives you:

- durable OpenCloset continuity ids
- stable routing for repeated execution through the same Claw session
- freedom to switch transport later without changing OpenCloset session ids

For v1, derive the OpenClaw session key deterministically from the OpenCloset session id.

## Phase 4: Stop Reaching Into Runner Internals

Two components currently make the migration harder than it needs to be because they depend on private local-runner details.

### 1. Runtime diagnostics

[opencloset/api/api/runtime_diagnostics.py](d:/openclaw/opencloset/api/api/runtime_diagnostics.py#L562) calls private runner helpers for provider/model suggestion generation.

Fix:

- move route/model resolution into a small public `ExecutionModelResolver` or `InferenceClient` service
- let diagnostics depend on that public service instead of on `SessionAgentRunner`

### 2. Delegation

[opencloset/api/api/delegation.py](d:/openclaw/opencloset/api/api/delegation.py#L274) directly creates providers and uses runner internals.

Fix:

- split `DelegationWorker` into:
  - delegation ledger/policy in OpenCloset
  - inference execution through a small shared inference service
- do not make delegation a hidden second execution runtime

This is important. If delegation keeps direct provider ownership while runs move to OpenClaw, you will still have two runtimes.

## Phase 5: Expose OpenCloset Services To OpenClaw, Not OpenCloset Tools

Do not port OpenCloset's current model-visible tool registry directly.

Instead, expose OpenCloset control-plane capabilities as narrow service endpoints or MCP-style tools that OpenClaw can call.

The first useful service bridge should cover:

- get active plan
- list/update plan items
- retrieve workspace memory context
- list/create captures and evidence
- record maintenance artifacts
- fetch/store handoff packets

This keeps the split clean:

- OpenClaw owns execution-time tool presentation and execution discipline
- OpenCloset owns orchestration data and durable state

## Phase 6: Move Queue And Channels By Reusing The Same Adapter

The good news is that queue and channel execution do not need bespoke migration logic first.

Because both already call `execute_run(...)`, once the adapter exists:

- [opencloset/api/api/clo_queue.py](d:/openclaw/opencloset/api/api/clo_queue.py#L420) automatically becomes Claw-backed
- [opencloset/api/api/agent_channels.py](d:/openclaw/opencloset/api/api/agent_channels.py#L489) automatically becomes Claw-backed

That is one of the strongest reasons to make the adapter seam the first move.

## Phase 7: Only Then Move Maintenance And Continuity Generation

Do not try to migrate idle maintenance, handoff generation, and runtime docking first.

First prove:

- foreground execution works through OpenClaw
- queue execution works through the same seam
- event relay is stable

Then move continuity generation so that:

- OpenClaw produces execution-side maintenance/handoff artifacts
- OpenCloset indexes and surfaces them through maintenance artifacts, briefings, and workspace runtime

That preserves the product split instead of muddying it.

## Recommended Implementation Order

### Step 1

Create a new execution abstraction and route all current call sites through it.

Files most likely touched:

- [opencloset/api/api/app.py](d:/openclaw/opencloset/api/api/app.py)
- [opencloset/api/api/routes.py](d:/openclaw/opencloset/api/api/routes.py)
- [opencloset/api/api/clo_queue.py](d:/openclaw/opencloset/api/api/clo_queue.py)
- [opencloset/api/api/agent_channels.py](d:/openclaw/opencloset/api/api/agent_channels.py)

### Step 2

Add `ClawExecutionRuntime` behind a feature flag while keeping the local runtime as fallback.

Suggested env/config switch:

- `OPENCLOSET_EXECUTION_SUBSTRATE=local|claw-cli|claw-gateway`

### Step 3

Implement the minimal normalized request builder from OpenCloset state.

Files most likely touched:

- new adapter module under `opencloset/api/api/` or `opencloset/api/substrate/`
- [opencloset/api/api/run_inputs.py](d:/openclaw/opencloset/api/api/run_inputs.py)
- [opencloset/api/api/planning.py](d:/openclaw/opencloset/api/api/planning.py)
- [opencloset/api/api/memory.py](d:/openclaw/opencloset/api/api/memory.py)

### Step 4

Return final-result compatibility first, then add event relay.

Files most likely touched:

- [opencloset/api/api/events.py](d:/openclaw/opencloset/api/api/events.py)
- [opencloset/api/api/run_lifecycle.py](d:/openclaw/opencloset/api/api/run_lifecycle.py)
- [opencloset/api/api/streaming.py](d:/openclaw/opencloset/api/api/streaming.py)

### Step 5

Refactor diagnostics and delegation off private runner helpers.

Files most likely touched:

- [opencloset/api/api/runtime_diagnostics.py](d:/openclaw/opencloset/api/api/runtime_diagnostics.py)
- [opencloset/api/api/delegation.py](d:/openclaw/opencloset/api/api/delegation.py)

## What Not To Do First

Do not do these first:

- do not rewrite the UI before the adapter exists
- do not make the workspace runtime consume Claw-native event names directly
- do not preserve two equally privileged execution paths longer than necessary
- do not keep local provider execution in delegation while foreground runs migrate
- do not try to migrate idle maintenance before foreground execution and queue execution are stable through the same seam
- do not directly import and embed OpenClaw internals into Flask as the first version

## The Practical First Milestone

The first milestone should be this:

**A normal OpenCloset chat turn, a queued `Clo Queue` item, and an `agent_channel` event can all execute through one `ClawExecutionRuntime` adapter without any UI contract changes.**

If that works, you have actually wired OpenClaw runtime into OpenCloset in an effective way.

If that does not work, more product-level design discussion is premature.

## Hard Boundary

OpenCloset should own:

- orchestration state
- continuity state
- plans, workspaces, memory, captures, evidence
- queueing, briefings, transient windows, and runtime surfaces
- canonical event vocabulary and durable run/event history

OpenClaw should own:

- turn execution
- tool exposure and tool execution
- prompt assembly
- recovery behavior during execution
- provider/model execution details
- execution-time maintenance and handoff generation

The bridge between them should start narrow, explicit, and replaceable.