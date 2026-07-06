# Claw Idle Maintenance Design v1

## Purpose

Define how Claw should perform opportunistic context maintenance while a session is idle so prompt-time compaction stops stealing latency and attention from the next foreground turn.

This is the execution-side design that complements OpenCloset's orchestration-plane role.

## Core Rule

Compaction should not primarily happen on the first prompt after the user returns.

The default behavior should be:

- user leaves
- session goes idle
- Claw performs bounded housekeeping while the machine is otherwise quiet
- user returns to a warm session with artifacts already prepared

Foreground prompt assembly should consume prepared maintenance artifacts, not do fresh housekeeping unless strictly necessary.

## Goals

- reduce effective prompt size before the next foreground run
- preserve execution-relevant state, not just transcript prose
- prevent repeated file reads and rediscovery loops
- keep maintenance bounded, cancellable, and saturation-aware
- preserve raw transcript truth while allowing prompt-time elision
- precompute return-briefing and handoff material

## Non-Goals

- no destructive archival during the first pass
- no large-model idle burns by default
- no maintenance during active foreground execution
- no silent mutation of workspace/project state as part of compaction

## Required Claw Components

Claw needs five source-level seams.

### 1. Session Idle Scheduler

Tracks whether a substrate session is eligible for idle work.

Responsibilities:

- detect idle windows
- rank maintenance jobs
- suppress work when foreground execution is active
- stop or defer work when provider saturation is high
- expose maintenance state to the control plane

### 2. Maintenance Artifact Store

Stores execution-derived artifacts keyed by session and covered transcript ranges.

### 3. Tool and File Ledger

Records structured facts from tool execution and file inspection so future runs do not need to rediscover the same state.

### 4. Prompt Assembler with Covered-Range Elision

Builds prompts from:

- uncovered transcript tail
- maintenance artifacts
- handoff state
- current user message

instead of replaying the full raw transcript.

### 5. Return Briefing Builder

Produces a concise "what changed while you were away" summary for the next user return.

## Maintenance Jobs

Maintenance jobs should be separate and individually cancelable.

### Job A: Micro Summary Refresh

Purpose:

- summarize the newest uncovered tail of the session

Input:

- newest uncovered messages
- latest tool/file ledger entries

Output:

- `micro_summary`

Default trigger:

- idle for 90 seconds
- no active run
- newest transcript content is not yet covered

Budget:

- smallest available local substrate
- hard timeout 15 seconds

### Job B: Segment Summary Distillation

Purpose:

- compress older transcript spans into structured semantic summaries

Input:

- uncovered historical range
- associated tool/file ledger entries

Output:

- `segment_summary`
- `compaction_marker`

Default trigger:

- idle for 5 minutes
- effective token usage above 45% or uncovered history span above threshold

Budget:

- small or mid local substrate
- hard timeout 30 seconds per segment

### Job C: Decision and Tool Digest

Purpose:

- retain execution truth that prose summaries usually lose

Input:

- tool invocations
- tool results
- assistant final decisions

Output:

- `decision_tool_digest`

Trigger:

- after a successful foreground run if idle begins within 60 seconds
- or any time digest coverage lags the transcript tail

### Job D: File State Digest

Purpose:

- stop redundant file reads and rediscovery

Input:

- read/edit/write tool results
- file path, purpose, key findings, mutation summary

Output:

- `file_state_digest`

Trigger:

- after any run that read more than N files or mutated any file

### Job E: Handoff Candidate Refresh

Purpose:

- produce a bounded resume packet before rollover or return

Input:

- current plan state
- latest maintenance artifacts
- latest run result

Output:

- `handoff_candidate`

Trigger:

- effective token usage above rollover-prep threshold
- session paused or nearing rollover
- session has been idle 10 minutes after substantial work

### Job F: Return Briefing Candidate

Purpose:

- give the user a warm restart summary when they return

Input:

- new maintenance artifacts since the last user turn
- plan delta
- delegation and worker results if present

Output:

- `return_briefing_candidate`

Trigger:

- at least one new maintenance artifact created during idle window

## Trigger Policy

Maintenance should be governed by explicit triggers.

### Hard preconditions

- no active foreground run
- no pending interactive approval step
- no active destructive tool execution
- provider saturation below configured threshold
- session not explicitly pinned `do_not_maintain`

### Soft priority signals

- effective prompt usage percentage
- raw minus effective token savings opportunity
- uncovered transcript span size
- number of recent file reads
- number of recent tool results worth digesting
- time since last maintenance pass

### Abort conditions

- user sends a new prompt
- foreground run starts
- machine power policy says stop
- maintenance substrate becomes saturated

Partial artifacts should remain draft until finalized.

## Artifact Schema

All artifacts should share one envelope.

```json
{
  "artifact_id": "art_123",
  "artifact_type": "segment_summary",
  "session_id": "sess_1",
  "status": "draft",
  "start_position": 1,
  "end_position": 30,
  "content": {},
  "metadata": {
    "source_run_ids": ["run_1"],
    "generator": "claw-maintenance",
    "model": "qwen-small"
  },
  "created_at": "2026-05-07T22:00:00.000Z"
}
```

### `micro_summary`

```json
{
  "summary": "",
  "latest_user_intent": "",
  "latest_assistant_state": "",
  "next_likely_action": ""
}
```

### `segment_summary`

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

### `decision_tool_digest`

```json
{
  "decisions": [],
  "tool_outcomes": [],
  "failures": [],
  "recovery_hints": []
}
```

### `file_state_digest`

```json
{
  "files": [
    {
      "path": "",
      "kind": "read",
      "purpose": "",
      "key_findings": [],
      "last_observed_hash": null,
      "mutated": false
    }
  ]
}
```

### `compaction_marker`

```json
{
  "covered_ranges": [
    {
      "start_position": 1,
      "end_position": 30,
      "source_artifact_ids": ["art_123"]
    }
  ],
  "effective_token_savings": 3200
}
```

### `handoff_candidate`

```json
{
  "summary": "",
  "active_goal": "",
  "next_action": "",
  "open_threads": [],
  "artifact_refs": {}
}
```

## Scheduling Policy

Maintenance should be opportunistic, tiered, and cheap-first.

### Tier 0: Immediate cleanup

When a run ends:

- persist tool/file ledger entries
- enqueue digest jobs
- do not block the final user-visible response

### Tier 1: Short idle window

At 90 seconds idle:

- micro summary refresh
- decision/tool digest refresh

### Tier 2: Medium idle window

At 5 minutes idle:

- segment summary distillation
- compaction marker update
- file state digest refresh

### Tier 3: Long idle window

At 10 to 30 minutes idle:

- handoff candidate refresh
- return briefing candidate refresh
- archive-safe range marking if policy allows

### Fairness rules

- at most one heavy maintenance job per session at a time
- prefer sessions with highest savings opportunity
- pause heavy jobs if user presence resumes

## Prompt Assembly Changes

Foreground prompt assembly must change at the source.

### Before

- take transcript
- maybe trim recent history
- maybe compact reactively during the user turn

### After

- load current uncovered transcript tail
- load valid maintenance artifacts
- exclude transcript ranges covered by valid compaction markers
- inject artifacts in deterministic priority order
- add the new user message
- compute effective token usage using uncovered transcript plus artifacts

### Required injection order

1. handoff candidate if resuming
2. segment summaries from oldest to newest
3. decision/tool digest
4. file state digest
5. micro summary
6. uncovered live transcript tail
7. new user message

### Required short-circuit behavior

If a planned tool call is about to re-read a file that appears in a valid `file_state_digest` and nothing indicates the file changed, prompt assembly or loop recovery should surface the digest first and discourage redundant reads.

This should not forbid rereads when:

- the file was edited afterward
- the purpose changed materially
- exact current content is required

## Token Accounting Changes

Claw should track both raw and effective prompt cost.

Required counters:

- `raw_tokens_used`
- `effective_tokens_used`
- `compacted_transcript_tokens_used`
- `maintenance_tokens_used`
- `compaction_savings_tokens`
- `compacted_range_count`

The rollover guard should key primarily off `effective_tokens_used`, not raw historical transcript total.

## Operational States

Each session should expose one maintenance state.

- `cold`
- `eligible`
- `maintaining`
- `warm`
- `saturated`
- `paused`

`warm` means the next foreground run can start without first doing maintenance work.

## Recommended Implementation Order

### Slice 1

- add artifact envelope and store
- add session idle scheduler
- add micro summary + compaction marker generation
- add effective token accounting

### Slice 2

- add decision/tool digest
- add file state digest
- change prompt assembly to consume artifacts and covered-range elision

### Slice 3

- add handoff candidate and return briefing candidate
- expose maintenance status/events to OpenCloset control plane

## Success Criteria

- after a session sits idle, the next prompt does not spend its first turn compacting by default
- effective prompt size is materially lower than raw transcript size
- previously read files do not get rediscovered repeatedly without cause
- maintenance jobs stop cleanly the moment foreground work resumes
- handoff and return briefing are usually ready before the user asks for them