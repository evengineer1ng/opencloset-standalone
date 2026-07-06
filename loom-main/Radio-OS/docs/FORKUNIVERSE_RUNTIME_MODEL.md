# ForkUniverse Runtime Model

## Decision

ForkUniverse should default to an on-demand truth engine, not a mandatory always-running daemon.

That means:

- a compiled universe does not need to stay open
- ForkUniverse does not need its own permanent task-manager process
- Radio OS or any other client can query a universe whenever it wants
- elapsed real time is one of the variables used during that query

ForkUniverse is therefore:

- a compiler
- a rules engine
- a time-aware truth calculator

It is not required to be:

- a permanently running live service

## What A Tick Is

A tick is not primarily a timer pulse.

A tick is a unit of simulation computation.

More specifically:

- a tick is the engine's atomic step when computing what became true
- ticks may be executed in batches during a query
- ticks may represent elapsed real time since last inquiry
- ticks may also be manually advanced if an operator wants denser observation

So the important distinction is:

- wall-clock scheduling is optional
- truth computation is mandatory

## Default Execution Model

ForkUniverse should support three modes, but default to the first.

### 1. On-Demand

Default mode.

- no long-running process required
- universe is queried when a client wants truth
- elapsed real time since last computation is converted into owed simulation time
- engine computes only when asked

This is the best fit for Radio OS.

### 2. Real-Time Daemon

Optional mode.

- a local service keeps computing at a cadence
- useful for debugging, local monitoring, or terminal watching
- not required for universe existence

### 3. Hybrid

Optional mode.

- mostly on-demand
- with opportunistic background advancement if a client requests it

## Radio OS Integration

Radio OS should treat ForkUniverse as a telemetry source, not as a fused subsystem.

The clean integration is:

- Radio OS owns the antenna
- the antenna asks ForkUniverse for current truth
- ForkUniverse returns surfaces, summaries, and state deltas
- Radio OS heat/ranking decides what gets airtime

This keeps the projects distinct:

- Radio OS is an ecosystem around narrated data
- ForkUniverse is a generator of narrative data

## ForkUniverse Antenna Contract

The simplest useful antenna call shape is:

```text
compute truth for universe X now
```

Optionally with flags like:

- include recap
- include thread changes
- include prediction scorecard
- max compute budget
- observation mode

Example conceptual API:

```json
{
  "universe_id": "fu_frontier_mirror",
  "query_mode": "radio_observe",
  "include_recap": true,
  "include_thread_changes": true,
  "include_prediction_scorecard": true,
  "max_compute_ticks": 2000
}
```

And the response can include:

- current state snapshot
- important event ledger rows
- thread deltas
- prediction settlements
- recap summary surfaces
- heat hints for Radio OS

## Time Is Still Real

Even though ForkUniverse is on-demand, time still matters.

A universe is living by existing because:

- last computation timestamp is stored
- current real timestamp is known
- elapsed real time becomes owed simulated time
- owed simulated time is resolved when the universe is queried

This gives the exact behavior wanted:

- leave for hours, days, or months
- come back later
- engine computes what changed
- summarize the most important developments

## Cadence Setting

The creation form should therefore include a cadence or time-policy setting.

Examples:

- 1 minute real = 1 minute simulated
- 1 minute real = 5 minutes simulated
- 1 minute real = 1 day simulated
- adaptive scaling

This is not a UI playback setting.

It is a universe law.

## Recommended Default

The default ForkUniverse runtime contract should be:

- `execution_model = on_demand`
- `time_model = elapsed_real_time_to_owed_ticks`
- `query_role = truth_calculator`

That makes ForkUniverse especially compatible with Radio OS heat systems, because Radio OS can simply ask for truth at its own cadence and let signal importance emerge from what changed.
