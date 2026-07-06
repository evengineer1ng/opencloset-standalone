# ForkUniverse Pattern Harvest

## Purpose

This document is not a vision statement. It is a backend extraction map.

The goal is to identify which chunks of the existing codebase should be lifted, generalized, and reassembled into the ForkUniverse backend.

ForkUniverse should not be written as one giant new invention. It should be assembled from proven patterns already present across:

- From The Backmarker
- Oracle Kingdom
- Neikos: Hundred Islands
- ATLFM / Radio OS evidence-oriented narration patterns

## The Big Picture

Each project solved a different layer of the same meta-problem.

### From The Backmarker contributed

- obligation-heavy entity simulation
- economy and contract pressure
- atomic event emission
- event triage into narratable importance
- persistent open-loop narrative memory

### Oracle Kingdom contributed

- causal traceability
- deterministic forked RNG architecture
- absence reconstruction
- deep-sleep epoch compression
- relationship graph as a propagation medium
- mythology / memory drift logic

### Neikos contributed

- macro-ledger world state
- normalized hidden axes
- command-driven deterministic sim loop
- carried-forward cross-run memory echoes
- lightweight trigger-to-voice feed orchestration

### ATLFM contributed

- evidence-first worldview
- hypotheses / predictions / later settlement
- the idea that a system is interesting when it tracks expectations against reality

ForkUniverse should combine these into:

- a persistent cold simulation
- a causal ledger
- a thread and prediction layer
- a memory hierarchy
- a narrative-surface emitter for Radio OS

## Lift Map

## 1. Deterministic Seed Kernel

### Lift from Oracle Kingdom and Neikos

Source patterns:

- `SeededRNG` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:79)
- `SeededRNG` in [plugins/neikos/__init__.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/neikos/__init__.py:120)

What to lift:

- stable seed normalization
- namespace forking
- independence between subsystems

ForkUniverse target module:

- `forkuniverse/core/rng.py`

ForkUniverse rule:

- every major subsystem gets its own forked stream
- examples: `characters`, `relationships`, `economy`, `threads`, `predictions`, `entropy`, `audio_signatures`

Why it matters:

- this is the root of reproducibility
- it prevents call-order bugs from changing the world

## 2. Rich Entity and Obligation Layer

### Lift from From The Backmarker

Source patterns:

- `Contract` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2174)
- `Budget` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2819)
- `SimState` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:3499)

What to lift:

- contracts as time-aware obligations with expiry and buyout logic
- budgets as rolling future constraints, not just current cash
- central simulation state that owns all long-lived systems

ForkUniverse target modules:

- `forkuniverse/domain/contracts.py`
- `forkuniverse/domain/economy.py`
- `forkuniverse/domain/state.py`

What to keep conceptually:

- obligations should be explicit objects
- commitments should project into future risk
- state should own both current values and long-lived subsystems

What to change:

- generalize beyond motorsport roles
- support housing, bills, romance obligations, debts, custody, promises, leases, employment, pets, vehicle upkeep

## 3. Atomic Event Spine

### Lift from FTB and Oracle Kingdom

Source patterns:

- `SimEvent` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2538)
- `SimEventBus` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2592)
- `SimEvent` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1175)
- `EventQueue` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1222)

What to lift:

- small typed event records
- priority / severity / urgency
- parent-cause linkage
- active unresolved event queues

ForkUniverse target modules:

- `forkuniverse/events/types.py`
- `forkuniverse/events/queue.py`

Recommended synthesis:

- use FTB's practical event payload style
- use Oracle Kingdom's explicit urgency and resolution fields

ForkUniverse addition:

- thread references and prediction references on events

## 4. Relationship Graph as Causal Medium

### Lift from Oracle Kingdom

Source pattern:

- `RelationshipGraph` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1095)

What to lift:

- graph-based relationship storage
- directional weighted edges
- multiple relation types between the same actors

ForkUniverse target module:

- `forkuniverse/social/graph.py`

What to change:

- expand from a single relation type enum into multi-axis relational state
- include affection, trust, dependency, resentment, attraction, loyalty, fear, and history depth

Why it matters:

- this becomes the transmission medium for ripple effects
- it is where secrets, betrayals, dependencies, and promises become mechanically real

## 5. Causal Ledger

### Lift from Oracle Kingdom

Source pattern:

- `CausalLedger` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1322)

What to lift:

- append-only directed causal edge store
- variable/source/target/tick indexing
- explainability and trace queries

ForkUniverse target module:

- `forkuniverse/causality/ledger.py`

This should be one of the least-modified lifts in the whole project.

Why:

- ForkUniverse needs to answer why a relationship failed, why a dream died, why a contract collapsed, why a rumor became myth
- the thread system and prediction system both become dramatically stronger if every important mutation is traceable

## 6. Narrative Memory and Open Loops

### Lift from FTB

Source pattern:

- `ArcMemory` in [plugins/meta/from_the_backmarker.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/meta/from_the_backmarker.py:159)

What to lift:

- compact durable narrative memory
- open loops
- momentum
- theme persistence

ForkUniverse target modules:

- `forkuniverse/narrative/memory.py`
- `forkuniverse/narrative/threads.py`

What to change:

- FTB's open loops are still lightweight strings
- ForkUniverse should upgrade them into typed `StoryThread` entities

Important lesson:

- this is the clearest sign that the codebase already knows unresolved things matter
- ForkUniverse should formalize that instinct instead of leaving it soft

## 7. Story Thread Engine

### New system built from FTB open loops plus Oracle event resolution patterns

Source inspirations:

- `ArcMemory.open_loops` in [plugins/meta/from_the_backmarker.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/meta/from_the_backmarker.py:168)
- unresolved `EventQueue` logic in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1222)

What to build:

- typed unresolved questions
- explicit participants
- confidence, urgency, heat, likely resolution horizon
- linkages to events, relationships, and predictions

ForkUniverse target module:

- `forkuniverse/narrative/threads.py`

This should be treated as the single biggest new native ForkUniverse module.

Not because the old projects lacked it conceptually, but because they stopped one layer short of formalizing it.

## 8. Prediction-Resolution Loop

### Lift conceptually from ATLFM and practically from FTB forecasting hooks

Source precedents:

- ATLFM signature references to hypotheses in [stations/ATLFM/signature.json](/c:/Users/evana/OneDrive/Documents/Radio-OS/stations/ATLFM/signature.json:171)
- Radio OS vision references to hypotheses/evidence in [docs/NARRATIVE_WORLD_RUNTIME_VISION.md](/c:/Users/evana/OneDrive/Documents/Radio-OS/docs/NARRATIVE_WORLD_RUNTIME_VISION.md:279)
- FTB budget forecasting in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2877)

What to lift:

- expectation as a first-class simulation object
- later comparison against actual outcomes
- credibility changes based on calibration

ForkUniverse target module:

- `forkuniverse/prediction/book.py`

Suggested structure:

- `Prediction`
- `PredictionOutcome`
- `PredictorProfile`

This is not present as a direct complete lift anywhere. It is a synthesis module.

## 9. Macro-State Ledger

### Lift from Neikos

Source pattern:

- `IslandLedger` in [plugins/neikos/__init__.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/neikos/__init__.py:2764)

What to lift:

- hidden macro axes
- seed-relative baselines
- normalization
- derived indices
- world-state classification from underlying axes

ForkUniverse target module:

- `forkuniverse/world/macro_ledger.py`

Why it matters:

- not every meaningful world shift belongs on individual characters
- the city, district, workplace, town, or institution needs its own drifting hidden state

ForkUniverse adaptation:

- axes might include labor pressure, housing pressure, social trust, scarcity, corruption, myth density, civic fear, and romantic volatility

## 10. World Memory Hierarchy

### Lift from Oracle myth memory and Neikos memory echoes

Source patterns:

- `MythMemory` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:8462)
- cross-run `generate_echo_events` in [plugins/neikos/__init__.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/neikos/__init__.py:4827)

What to lift:

- recency decay
- myth amplification
- contradiction-sensitive persistence
- historical echoes re-entering present play

ForkUniverse target modules:

- `forkuniverse/memory/hierarchy.py`
- `forkuniverse/memory/echoes.py`

What to build:

- immediate memory
- recent history
- historical record
- mythology

This is another synthesis module where Oracle and Neikos together show the pattern more clearly than either alone.

## 11. Absence Reconstruction and Time Compression

### Lift from Oracle Kingdom

Source patterns:

- `AbsenceReconstructor` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:11097)
- `EpochCompressionEngine` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:11282)
- `ResumeProtocol` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:11541)
- `ReconstructionStateMachine` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:1815)

What to lift:

- active / idle / deep sleep time states
- reduced-fidelity resume
- epoch compression
- phased reconstruction results

ForkUniverse target modules:

- `forkuniverse/time/policy.py`
- `forkuniverse/time/resume.py`
- `forkuniverse/time/epoch.py`

This should also be one of the least-modified major lifts.

ForkUniverse addition:

- thread-aware resume output
- prediction scorecard on wake
- explicit "what resolved while you were gone"

## 12. Command-Driven Deterministic Controller

### Lift from Neikos and Oracle Kingdom

Source patterns:

- `NKController` in [plugins/neikos/__init__.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/neikos/__init__.py:6020)
- `OKController` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:11667)
- `FTBController` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:30890)

What to lift:

- a single controller boundary between sim and runtime
- thread-safe state lock
- explicit command queue
- explicit UI/event queue
- deterministic tick advancement

ForkUniverse target module:

- `forkuniverse/runtime/controller.py`

Recommended synthesis:

- Neikos gives the cleanest command-driven loop
- Oracle gives the strongest resume/absence controller semantics
- FTB gives the strongest runtime integration seam

## 13. Event-to-Beat Compiler

### Lift from FTB

Source patterns:

- `EventRouter` in [plugins/meta/from_the_backmarker.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/meta/from_the_backmarker.py:62)
- `BeatBuilder` in [plugins/meta/from_the_backmarker.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/meta/from_the_backmarker.py:202)
- `ftb_emit_segments` in [plugins/meta/from_the_backmarker.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/meta/from_the_backmarker.py:924)
- async narration bridge in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:35191)

What to lift:

- event relevance classification
- collapse many atomic events into a few meaning-bearing beats
- async narration generation so simulation does not block

ForkUniverse target modules:

- `forkuniverse/narrative/router.py`
- `forkuniverse/narrative/compiler.py`
- `forkuniverse/runtime/narration_bridge.py`

What to change:

- route by thread heat and prediction significance, not only by player-team relevance

## 14. Narrative Surface Feed Layer

### Lift from Oracle Court feed and Neikos NPC feed

Source patterns:

- `feed_worker` in [plugins/oracle_court_feed.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_court_feed.py:39)
- `NeikosNPCFeed` in [plugins/neikos_npc_feed.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/neikos_npc_feed.py:37)

What to lift:

- a feed polling or driving the controller
- converting internal triggers into Radio OS candidates
- attaching voice, priority, and metadata at the edge

ForkUniverse target modules:

- `plugins/forkuniverse_feed.py`
- `plugins/meta/forkuniverse_meta.py`

Recommended split:

- the feed should emit typed narrative surfaces
- the meta plugin should interpret and voice those surfaces

## 15. Character Identity Persistence

### Lift from FTB career history plus Oracle decree/event history

Source precedents:

- `ManagerCareerStats` in [plugins/ftb_game.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/ftb_game.py:2501)
- `event_history` and `decree_history` in [plugins/oracle_kingdom.py](/c:/Users/evana/OneDrive/Documents/Radio-OS/plugins/oracle_kingdom.py:5060)

What to build:

- persistent character ledger
- major wins and losses
- promises and betrayals
- prediction history
- myth tags

ForkUniverse target module:

- `forkuniverse/domain/character_ledger.py`

This is another key synthesis module.

## Recommended ForkUniverse Backend Layout

```text
forkuniverse/
  core/
    rng.py
    ids.py
  domain/
    state.py
    characters.py
    character_ledger.py
    relationships.py
    contracts.py
    economy.py
    organizations.py
  world/
    macro_ledger.py
    generation.py
    entropy.py
  causality/
    ledger.py
    ripple.py
  events/
    types.py
    queue.py
  narrative/
    threads.py
    router.py
    compiler.py
    surfaces.py
    memory.py
  prediction/
    book.py
  memory/
    hierarchy.py
    echoes.py
  time/
    policy.py
    resume.py
    epoch.py
  runtime/
    controller.py
    narration_bridge.py
```

## What To Lift Almost Intact

- Oracle Kingdom `SeededRNG`
- Oracle Kingdom `CausalLedger`
- Oracle Kingdom `ResumeProtocol` / absence model
- Neikos-style macro ledger normalization
- FTB-style event-to-beat compile boundary

## What To Lift But Generalize Heavily

- FTB `Contract`
- FTB `Budget`
- FTB `SimState`
- Oracle `RelationshipGraph`
- Neikos `NKController`

## What ForkUniverse Must Invent Natively

- first-class `StoryThread`
- prediction-resolution book
- character ledger
- memory hierarchy across present/history/myth
- entropy system designed around narrative pressure rather than only domain simulation

## First Backend Spike

The first serious backend spike should not try to build the full universe.

It should build this chain only:

1. `SeededRNG`
2. `WorldState`
3. `Character`
4. `RelationshipGraph`
5. `Contract` and `Budget`
6. `SimEvent`
7. `CausalLedger`
8. `StoryThread`
9. `PredictionBook`
10. `ResumeProtocol`

If those ten pieces work together, ForkUniverse stops being a concept and becomes a backend.

## Final Read

The real lesson from these projects is that ForkUniverse does not need more imagination.

It needs:

- better formalization of unresolved things
- better formalization of expectation and surprise
- better formalization of memory and identity

The codebase already contains most of the hard structural answers.
