# OpenCloset Runtime Module Disposition for Claw Execution

## Purpose

Audit the current OpenCloset runtime modules and mark which ones should be kept, wrapped, or retired if Claw becomes the execution engine.

Disposition meanings:

- `keep`: remain first-class OpenCloset control-plane code
- `wrap`: keep the module concept, but repoint it at Claw or shrink it to an adapter/translation role
- `retire`: remove local ownership from OpenCloset once Claw is the substrate of record

## Decision Summary

OpenCloset should keep its orchestration-plane modules.

OpenCloset should wrap its execution-facing boundaries so the UI and durable state do not care whether the turn ran locally or through Claw.

OpenCloset should retire duplicate loop, prompt, and tool execution ownership.

## Module Audit

| Module | Current Role | Disposition | Why | Migration Note |
|---|---|---|---|---|
| `api/api/app.py` | app factory and service wiring | keep | remains the composition root for orchestration services | replace local execution wiring with Claw adapter wiring |
| `api/api/routes.py` | message/session/run routes | wrap | submission routes should call substrate adapter, not local loop stack | preserve REST shape where possible |
| `api/api/events.py` | canonical event taxonomy | keep | should remain the public event vocabulary for UI and persistence | add adapter mappings from Claw-native events |
| `api/api/run_lifecycle.py` | run lifecycle authority + stream persistence | wrap | still needed, but fed by adapter events/results | keep canonical statuses and persistence ownership |
| `api/api/streaming.py` | SSE replay and stream plumbing | wrap | still needed for UI, but no direct local loop dependency | consume canonical events only |
| `api/api/event_logger.py` | durable event sink | keep | control plane still owns durable event history | extend for substrate metadata |
| `api/api/run_inputs.py` | normalized run attachments/input envelope | keep | still valuable before execution handoff | pass normalized payload through adapter |
| `api/api/session_attachments.py` | attachment persistence | keep | orchestration concern | expose attachment refs to Claw |
| `api/api/session_validation.py` | route/session validation | keep | unaffected by substrate choice | none |
| `api/api/workspaces.py` | workspace authority | keep | core OpenCloset domain | none |
| `api/api/planning.py` | plan rolodex, items, revisions, activation | keep | core OpenCloset domain | expose active plan slice to Claw requests |
| `api/api/memory.py` | memory store and retrieval | keep | core continuity domain | Claw should consume references or selected payloads, not own the store |
| `api/api/delegation.py` | read-only worker delegation | wrap | concept stays, execution path should use Claw or later worker substrates | split policy/ledger from provider execution |
| `api/api/clo_queue.py` | queue visibility and orchestration | keep | still useful as control-plane work queue | adapt to substrate-backed run states |
| `api/api/maintenance.py` | idle maintenance worker and artifact generation | wrap | keep artifact/index concepts, move execution-side maintenance generation into Claw | OpenCloset becomes scheduler/index/surface layer |
| `api/api/maintenance_artifacts.py` | maintenance artifact persistence | keep | useful shared store and querying layer | extend to accept Claw-generated artifacts |
| `api/api/maintenance_ranges.py` | covered-range and archive-safe helpers | wrap | good logic, but should operate on substrate-generated artifacts | keep helpers, remove local loop assumptions |
| `api/api/watchdog.py` | rollover poller | keep | orchestration concern | feed off effective token data from Claw artifacts/results |
| `api/api/scheduler.py` | unified background work arbitration | keep | central to OpenCloset identity | none |
| `api/api/scheduler_producers.py` | maintenance/watchdog candidates | wrap | still useful, but candidate production should use Claw-backed maintenance state | none |
| `api/api/workspace_runtime.py` | workspace operational workers/signals | keep | orchestration/pastime layer | keep substrate-agnostic |
| `api/api/reflective_pastimes.py` | idle reflective work | keep | directly aligned with OpenCloset product role | can dispatch work through Claw later |
| `api/api/briefing.py` | return briefings | keep | core continuity surface | consume Claw maintenance artifacts |
| `api/api/windows.py` | transient window registry/rendering | keep | user-facing product differentiator | independent of execution substrate |
| `api/api/agent_channels.py` | long-lived headless channels | keep | orchestration/runtime surface | may call Claw adapter per tick |
| `api/api/agent_harnesses.py` | domain harness registry | keep | product-layer abstraction | none |
| `api/api/runtime_diagnostics.py` | runtime inspection | wrap | still useful, but should report adapter/substrate health too | none |
| `api/api/rollover.py` | rollover creation and handoff routing | wrap | still core behavior, but handoff content should come from Claw execution state | keep OpenCloset authority for successor sessions |

## Agent Runtime Modules

| Module | Current Role | Disposition | Why | Migration Note |
|---|---|---|---|---|
| `api/agent/runner.py` | queued-run executor and prompt compaction owner | retire | this is the clearest duplicate execution seam | replace with adapter-backed execution coordinator |
| `api/agent/loop.py` | foreground turn loop | retire | Claw should own the turn loop | preserve blocker/event lessons, not ownership |
| `api/agent/engine.py` | conversation runtime and active tool tracking | retire | Claw should own active execution runtime state | keep only if needed as adapter-local stub during migration |
| `api/agent/prompt.py` | provider prompt assembly | retire | prompt assembly belongs to Claw in the target split | keep OpenCloset plan/memory/workspace extraction helpers only if reused externally |
| `api/agent/input_pipeline.py` | local input normalization for loop ingestion | wrap | some normalization remains useful before adapter handoff | shrink to request normalization only |
| `api/agent/guard.py` | token estimation and pressure monitor | wrap | concept remains, but execution-side numbers should come from Claw | OpenCloset should consume effective token telemetry |
| `api/agent/governor.py` | execution failure/governor heuristics | retire | belongs with the execution loop | migrate heuristics into Claw |
| `api/agent/substrate_router.py` | provider chooser | wrap | routing policy belongs to OpenCloset, but concrete provider execution belongs to Claw | evolve into substrate policy selector, not provider caller |

## Tooling Modules

| Module | Current Role | Disposition | Why | Migration Note |
|---|---|---|---|---|
| `api/tools/registry.py` | model-visible tool assembly | retire | Claw should own model-visible tool pool and permission checks | OpenCloset exposes services, not direct runtime tool registry |
| `api/tools/executor.py` | tool execution engine | retire | direct execution ownership moves to Claw | none |
| `api/tools/normalizer.py` | tool-call repair/normalization | retire | belongs in Claw execution stack | preserve ideas/tests if useful |
| `api/tools/filesystem.py` | file tools | retire | execution substrate concern | Claw should own implementation |
| `api/tools/process.py` | exec/process tools | retire | execution substrate concern | Claw should own implementation |
| `api/tools/planning_tools.py` | model-facing planning tool shims | wrap | planning service stays in OpenCloset, but tools should be surfaced through Claw bridge | expose as adapter-backed service calls |
| `api/tools/memory_tools.py` | model-facing memory tool shims | wrap | memory service stays in OpenCloset, but tool exposure should happen through Claw | expose as adapter-backed service calls |
| `api/tools/__init__.py` | registry assembly helpers | retire | local assembly ownership goes away | replace with service registration metadata if needed |

## Keep / Wrap / Retire by Theme

### Keep

- workspace and planning authority
- memory and evidence
- scheduler, briefings, pastimes, delegation ledger
- canonical event vocabulary
- transient windows and chat-native product surfaces

### Wrap

- routes that currently submit or observe execution
- run lifecycle and stream plumbing
- maintenance and rollover logic that should consume Claw artifacts
- substrate routing as policy rather than provider execution
- planning and memory tools as service bridges into Claw

### Retire

- local OpenCloset foreground loop
- local OpenCloset prompt assembly
- local OpenCloset tool registry and executor
- local OpenCloset direct provider-calling execution path

## Recommended Refactor Sequence

### Phase 1: Add adapter without deleting anything

- introduce `ClawExecutionAdapter`
- make `routes.py` and run submission paths call the adapter behind a flag
- keep existing canonical event storage and SSE flow intact

### Phase 2: Move prompt and tool ownership

- stop building execution prompts in `api/agent/prompt.py`
- stop executing tools in `api/tools/*`
- convert planning/memory tools into service contracts consumable by Claw

### Phase 3: Remove duplicate runtime ownership

- retire `api/agent/runner.py`, `api/agent/loop.py`, `api/agent/engine.py`, and local tool execution modules
- simplify `app.py` wiring around the adapter and control-plane services

## Hard Constraints During Migration

- do not move workspace, plan, or memory truth into Claw
- do not change OpenCloset UI event vocabulary just because Claw uses different internal names
- do not preserve two equal execution paths longer than necessary
- do not let OpenCloset become responsible for provider-specific recovery logic once Claw is in place

## End State

OpenCloset should look like a continuity and orchestration product with a stable canonical event spine.

Claw should look like the substrate that actually executes turns, tools, compaction, and recovery.

That is the clean boundary. Anything that leaves both sides owning the foreground loop is just another temporary duplication phase.