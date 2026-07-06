# Readonly Loop Guard

## Purpose

This note records where the `3 reads in a row` / `loop:guard` behavior lives in OpenCloset, how it is triggered, and which knobs are safest to change.

## Main Files

- `opencloset/api/agent/loop.py`
- `opencloset/api/agent/runner.py`
- `opencloset/api/api/app.py`

## Core Symbols

### `opencloset/api/agent/loop.py`

- `_ACTION_TOOLS`
  - defines which tool names count as a concrete action instead of readonly/inspection work
  - current set:
    - `write`
    - `edit`
    - `exec`
    - `process`
    - `plan_add_item`
    - `plan_set_status`
    - `plan_create`
    - `plan_activate`
    - `plan_reorder`
    - `plan_archive`
    - `plan_accept_proposal`
    - `plan_reject_proposal`
- `LoopConfig.readonly_warn_after_turns`
  - default value: `3`
  - comment: `inject action directive after N read-only turns`
- `AgentLoop.run(...)`
  - enforcement lives in the post-tool-execution branch
  - logic:
    1. collect tool names used in the turn
    2. if any used tool is in `_ACTION_TOOLS`, reset the readonly streak
    3. otherwise increment `consecutive_readonly`
    4. if streak reaches `readonly_warn_after_turns`, inject a warning message and reset the streak
- `AgentLoop._inject_readonly_warning(...)`
  - injects a `user` role transcript message containing:
    - `[loop:guard] {n} consecutive turns used only read/inspect tools ...`
    - `You MUST act now: call write, edit, exec, or process ...`

### `opencloset/api/agent/runner.py`

- `SessionAgentRunner.execute_run(...)`
  - constructs `LoopConfig(...)`
  - passes:
    - `readonly_warn_after_turns=int(self.app.config.get("LOOP_READONLY_WARN_AFTER_TURNS", 3))`

### `opencloset/api/api/app.py`

- `create_app(...)`
  - defines the config default:
    - `LOOP_READONLY_WARN_AFTER_TURNS`
  - environment variable:
    - `OPENCLOSET_LOOP_READONLY_WARN_AFTER_TURNS`

## Trigger Behavior

- This is not a global session-level guard across arbitrary messages.
- It runs inside the agent loop only after a turn that produced tool calls and completed tool execution.
- It does not trigger on plain assistant-text turns with no tool calls, because the loop exits before this branch.
- It treats a turn as readonly if the model used tools but none of them are in `_ACTION_TOOLS`.
- When triggered, it does not fail the run.
- Instead, it injects a synthetic `user` message into transcript, which pressures the next continuation to take an action.

## Observed Session Behavior

- The warning text seen in transcripts comes from `AgentLoop._inject_readonly_warning(...)`, not from the frontend.
- In Session 24, the separate shell failure `<< was unexpected at this time.` came from trying bash-style heredoc syntax through the Windows `exec` tool.
  - That is a command-shape incompatibility.
  - It is separate from the readonly guard itself.

## Safest Change Options

### Option 1: Disable via config only

Use:

- `OPENCLOSET_LOOP_READONLY_WARN_AFTER_TURNS=0`

Why this is safest:

- no code path removal
- no prompt-format change beyond suppressing the injected warning
- existing logic already treats `warn_after <= 0` as disabled because enforcement checks `warn_after > 0`

### Option 2: Relax the threshold

Use a larger value such as:

- `OPENCLOSET_LOOP_READONLY_WARN_AFTER_TURNS=6`
- or `=10`

Why:

- preserves the feature
- lowers the chance of interrupting legitimate discovery work

### Option 3: Narrow what counts as readonly

Change `_ACTION_TOOLS` or the enforcement rule so more tool patterns count as legitimate progress.

Risk:

- behavior becomes policy-heavy in code
- easier to drift from expected semantics

### Option 4: Remove transcript injection but keep telemetry

Keep streak tracking, but log or surface it operationally instead of inserting a coercive `user` message.

Risk:

- requires code change and likely UI/event handling decisions

## Recommended First Move

If the goal is to stop the guard from aggressively steering the agent during investigation, the smallest safe change is:

- set `OPENCLOSET_LOOP_READONLY_WARN_AFTER_TURNS=0`

If we want the default product behavior changed for everyone rather than just this environment, the next-smallest code change is:

- change the default in `opencloset/api/api/app.py` from `3` to `0`

