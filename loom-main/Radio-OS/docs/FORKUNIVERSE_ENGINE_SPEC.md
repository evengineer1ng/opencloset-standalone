# ForkUniverse Engine Spec

## Formal Thesis

ForkUniverse is a persistent causal universe simulator that produces narrative pressure as a first-class output.

It is not fundamentally:

- a story generator
- a novel generator
- a sitcom generator
- a world generator
- a game

Those are downstream products.

At the engine level:

- ATL proved evidence pressure
- Oracle Kingdom proved causal pressure
- From The Backmarker proved competitive pressure
- Neikos proved ecological pressure
- ForkUniverse must produce narrative pressure

Radio OS does not need finished "stories" from ForkUniverse. It needs evidence-bearing narrative pressure surfaces that can be narrated honestly.

## Product Definition

ForkUniverse is also a universe-creation sandbox.

When you first open ForkUniverse, it should not feel like a dev tool or a save editor. It should feel like a flashy reality-forging form.

The first question is:

- what universe do you want?

The product is:

1. a visual sandbox / authoring form
2. a universe compiler
3. a persistent causal simulation backend
4. a Radio OS-compatible narrative surface emitter

In plain terms:

- the front half asks what reality the operator wants to observe
- the middle compiles that into deterministic tables
- the back half advances those tables over ticks and years
- Radio OS listens to the pressure that emerges

## Purpose

ForkUniverse is a new cold-layer simulation engine that runs alongside Radio OS and speaks through Radio OS without requiring Radio OS runtime changes.

Its job is to generate persistent, seeded, evidence-backed fictional worlds that can be narrated as:

- episodic comedy or sitcom-like runs
- long-form feature or novel-like arcs
- endless living worlds with no hard cap or final resolution

Radio OS remains the listening surface. ForkUniverse becomes a new world source.

## What A Fork Universe Is

A Fork Universe is a compiled reality instance.

It is the result of:

- a premise
- a world genre / setting
- a cast size
- a starting situation
- a rules profile
- a seed
- optional operator customizations

being transformed into:

- entity tables
- relationship tables
- organization tables
- location tables
- economy / obligation tables
- memory / myth tables
- thread and prediction tables
- tick rules and pressure coefficients

So when someone makes a Fork Universe, they are not asking an LLM to "keep roleplaying."

They are using a form to specify a reality, then compiling that reality into a deterministic simulation format.

## Universe Creation Flow

ForkUniverse should begin as a creation wizard.

The core flow should feel like:

1. What universe do you want?
2. How big should it start?
3. What kind of story pressure should dominate?
4. How deterministic vs chaotic should it be?
5. Do you want a seed, a preset, or a custom authored fork?
6. Compile universe

The wizard can be visually wild, cinematic, and playful.

That is product language.

Underneath, the engine behavior is strict:

- every answer maps to structured fields
- every field maps to known tables and coefficients
- every generated universe becomes a valid cold-layer state package

## Universe Authoring Form

The form should collect a compact but powerful set of inputs.

### Required Inputs

- `premise`
- `setting`
- `time_period`
- `genre_mix`
- `world_scale`
- `starting_population`
- `story_mode`
- `seed_mode`

### Optional Inputs

- `operator_insert`
- `custom_starting_context`
- `violence_ceiling`
- `romance_ceiling`
- `absurdity_ceiling`
- `institutional_density`
- `economic_harshness`
- `entropy_rate`
- `narration_style`

Example prompts:

- Westworld but inside a Wild West theme park where AI workers and visitors destabilize each other
- Two people alone in a forest
- A huge modern city with 100 starting characters
- Medieval court politics in China
- A Middle East political thriller
- A family sitcom inside a decaying shopping mall

## Universe Compiler

This is the missing product-middle that turns sandbox inputs into backend reality.

ForkUniverse should have a compiler step that transforms the form into structured simulation tables.

The compiler pipeline should be:

```text
form input
→ normalized universe brief
→ LLM-assisted schema fill
→ deterministic table generation
→ simulation-ready world package
```

The LLM should not run the world.

The LLM should help fill one specific structured format once at creation time, then optionally again when generating expansion packs or migrations.

Its job is:

- infer coherent names
- infer coherent institutions
- infer coherent social roles
- infer coherent table labels
- infer genre-fitting defaults
- populate schema fields

Its job is not:

- invent live facts every tick
- bypass the deterministic backend
- become the simulation itself

## LLM Role In Creation

ForkUniverse should use the LLM as a schema-filler and coherence pass.

Given a premise, the LLM should fill a single structured format such as:

- setting metadata
- naming banks
- role archetype banks
- institution types
- location types
- social tension defaults
- likely thread families
- likely prediction families
- audio signature tendencies

The important rule is:

- the LLM fills the recipe
- the engine cooks from the recipe

That keeps creation instant and magical without making the running universe mushy or untraceable.

## Preset Seeds and Custom Forks

ForkUniverse should support two equally important creation modes.

### 1. Preset Seed Mode

This is the Minecraft-style promise.

- same preset
- same seed
- same ruleset version
- same initial universe

Example:

- `westworld_frontier + 19383ybfgobblegork`

should generate the same starting form for every operator using that preset and version.

### 2. Custom Fork Mode

This is the instant sandbox promise.

- operator supplies premise and constraints
- ForkUniverse compiles a new schema-filled world package
- the resulting universe still gets a canonical seed and ruleset hash

This means custom universes are still reproducible once compiled.

## Compiled World Package

The output of universe creation should be a deterministic package, not a loose prompt.

Suggested contents:

- `universe_brief.json`
- `world_tables.json`
- `naming_banks.json`
- `coefficient_profile.json`
- `thread_templates.json`
- `prediction_templates.json`
- `time_policy.json`
- `seed_manifest.json`

This package is what the backend actually runs.

## Core Insight

The magic is not that the LLM tells stories forever.

The magic is:

- the operator can invent a reality in seconds
- the compiler can turn that into real tables in seconds
- the tick engine can then simulate that reality for a long time
- Radio OS can listen to what that reality becomes

## What We Are Reusing

ForkUniverse should not start from zero. The existing projects already prove the most important primitives.

### From The Backmarker

Source surfaces:

- `plugins/ftb_game.py`
- `plugins/meta/from_the_backmarker.py`
- `docs/FTB_NARRATIVE_ENGINE_IMPLEMENTATION.md`

Reusable lessons:

- deep deterministic sim with many entity classes and economic pressure
- contracts, budgets, careers, aging, promotion/relegation, staff ecosystems
- event emission from sim state instead of freeform roleplay
- editorial beat collapsing so many raw events become a few spoken beats
- story memory via themes, momentum, and open loops

FTB proves ForkUniverse should treat simulation facts as primary and spoken narration as a compression layer.

### Oracle Kingdom

Source surfaces:

- `plugins/oracle_kingdom.py`
- `plugins/oracle_court_feed.py`
- `stations/OracleKingdom/manifest.yaml`

Reusable lessons:

- seeded deterministic universe with forked RNG namespaces
- causal ledger so outcomes are inspectable after the fact
- absence reconstruction and epoch compression for "while you were gone"
- player influence through interpreted decrees, not brittle command-response trees
- cold layer and hot layer separation

Oracle Kingdom is the clearest proof that ForkUniverse should simulate ripple effects, not hard-coded dialogue trees.

### Neikos: Hundred Islands

Source surfaces:

- `plugins/neikos/__init__.py`
- `plugins/meta/neikos_meta.py`
- `stations/NeikosExpedition/manifest.yaml`

Reusable lessons:

- preset seed identities that are reproducible across machines
- structured world generation from finite seed space
- presentation-only LLM usage over deterministic world math
- explicit audio identity per archetype and per artifact type
- content delivery through small typed triggers, not giant prose dumps

Neikos proves that a world can be highly authored in tone while still remaining deterministic in structure.

### ATLFM / Algotrading League

Source surfaces in this repo:

- `stations/ATLFM/manifest.yaml`
- `stations/ATLFM/signature.json`
- `docs/NARRATIVE_WORLD_RUNTIME_VISION.md`

Inference from those sources:

- ATLFM already treats evidence, hypotheses, experiments, and observed outcomes as narratable material
- the Radio OS vision doc explicitly names hypotheses and contract offers as valid transient evidence surfaces

This is the best local precedent for ForkUniverse's prediction-resolution layer: worlds should not only do things, they should form expectations about what will happen next and score those expectations against reality.

## Core Design Rules

1. ForkUniverse is a simulation engine first, a story engine second.
2. The cold layer is the source of truth. The hot layer may interpret, never invent.
3. Every output fact should be traceable to seed + world state + event history.
4. Interesting must emerge from interacting systems, not from one giant prompt.
5. Absence is first-class. The world continues when nobody is listening.
6. Radio OS integration happens through feeds, events, memory, and meta-plugin narration. Radio OS itself does not need redesign for ForkUniverse to exist.
7. Open unresolved things are first-class entities, not incidental byproducts.
8. Prediction and prediction failure are core simulation loops.
9. Entropy is mandatory. Decay, disappointment, contradiction, and loss create narrative pressure.

## Product Shape

ForkUniverse should support three world modes from the same engine:

### 1. Episodic Mode

Target:

- 20-30 minute sitcom / dramedy / procedural beats
- recurring cast
- situation resets are partial, not total
- strong recurring premises, rivalries, and callback density

Core behavior:

- story pressure resolves per episode
- character pressure does not fully resolve
- each episode leaves residue: contracts, jealousy, debt, romance, grudges, injuries, reputation

### 2. Longform Mode

Target:

- 90+ minute action/drama/adventure arcs
- beginning, escalation, midpoint, climax, aftermath
- protagonist/antagonist/lightning-rod/static-character patterns

Core behavior:

- stronger macro-arc rails
- explicit act structure windows
- fewer resets, more irreversible consequences

### 3. Continuous World Mode

Target:

- no hard cap
- no forced ending
- ongoing social/economic/ecological life
- Radio OS can tune in at any point and hear the current state

Core behavior:

- world keeps simulating until storage or operator policy says otherwise
- while-you-were-gone summaries are a core feature, not a patch

## High-Level Architecture

ForkUniverse should be split into nine layers.

### 1. Seed Kernel

Responsibilities:

- normalize string seeds into canonical world seeds
- create deterministic RNG namespaces
- guarantee that identical presets produce identical starts on different machines

Required rule:

- `seed + preset + ruleset_version` must define the same initial universe everywhere

Recommended pattern:

- adopt Oracle Kingdom / Neikos style forked RNG namespaces for independent systems
- examples: `cast`, `economy`, `romance`, `transport`, `weather`, `crime`, `threads`, `predictions`, `artifact_audio`

### 2. World State Kernel

The cold state should include at minimum:

- time and continuity
- locations and sublocations
- characters
- relationships
- households
- organizations
- assets and money
- obligations and contracts
- goals, fears, fantasies, grudges, secrets
- active situations
- open loops
- recent events
- prediction book

ForkUniverse differs from FTB in one important way: it is not just managing competition systems. It must manage ordinary life pressures and emotional inertia as first-class state.

### 3. Causality Engine

This is the heart of the project.

It should convert:

- trait state
- resources
- relationships
- current pressures
- world rules
- player/operator influence

into:

- events
- changed probabilities
- changed obligations
- changed beliefs
- changed future affordances

This is where Oracle Kingdom's ripple logic matters most. The user should not need a handcrafted response system for every line. They should tweak magnitude, intention, context, or decree style and let the world reinterpret that pressure.

### 4. Narrative Pressure Engine

This layer decides what kind of unresolved pressure the world is currently producing.

It should track:

- comedic pressure
- romantic pressure
- social pressure
- status pressure
- danger pressure
- mystery pressure
- scarcity pressure
- institutional pressure
- entropy pressure

Its primary job is not "write plot."

Its primary job is:

- detect unresolved tensions
- score their heat
- estimate likely developments
- expose narratable pressure surfaces

### 5. Story Thread Engine

This is a core system, not a feature add-on.

ForkUniverse should maintain open questions as first-class entities.

Example shape:

```python
StoryThread(
    thread_id="contract_jason_teamowner_127",
    title="Will Jason sign the contract?",
    participants=["Jason", "TeamOwner"],
    domain="contract",
    confidence=0.72,
    urgency=0.31,
    heat=0.64,
    status="active",
    predicted_resolution_day=127,
    supporting_event_ids=["evt_991", "evt_1004"],
)
```

Listeners do not return for raw state. They return for unresolved things.

Thread lifecycle should include:

- created
- heated
- cooled
- delayed
- split
- merged
- misread
- resolved
- mythologized

### 6. Prediction and Belief Market

ForkUniverse should formalize the loop as:

```text
state
→ prediction
→ event
→ prediction resolution
→ updated world model
```

That creates a major difference from a normal story sim.

Now:

- characters can be wrong
- factions can be wrong
- institutions can be wrong
- narrators can be wrong
- the world can surprise itself

Each prediction should track:

- predictor
- target
- time horizon
- confidence
- rationale anchors
- thread linkage
- real outcome
- calibration score

Prediction resolution should feed back into:

- trust
- reputation
- institutional legitimacy
- self-belief
- myth formation
- future prediction weighting

### 7. Format and Arc Shaping Layer

This layer tracks format-sensitive structures:

- episode setup
- escalation window
- payoff readiness
- aftermath window
- callback opportunities

This is how the same world can be heard as sitcom, action flick, or ambient life-sim without rewriting the simulation core.

### 8. Chronicle Compiler

This layer turns raw event history into legible packets:

- live beat
- recap beat
- episode summary
- season summary
- while-you-were-gone digest
- character dossier delta
- relationship delta
- world-state bulletin
- prediction scorecard
- thread watchlist

FTB's event collapsing and Oracle Kingdom's resume chronicle should both be treated as direct precedent here.

### 9. Radio Bridge

ForkUniverse should expose itself to Radio OS through the existing pattern:

- one or more feed plugins emit typed candidates or events
- one meta plugin converts high-priority evidence into narrated segments
- audio signatures are passed as metadata, not hardwired in Radio OS

No Radio OS runtime rewrite should be required.

## World Model

ForkUniverse needs a richer general-purpose entity model than any one predecessor project.

### Characters

Suggested fields:

- `character_id`
- `seed_anchor`
- `name`
- `age`
- `origin`
- `archetype`
- `traits`
- `skills`
- `values`
- `dreams`
- `fears`
- `fantasies`
- `inner_conflicts`
- `social_mask`
- `stress_state`
- `money_state`
- `health_state`
- `romance_state`
- `employment_state`
- `home_id`
- `vehicle_ids`
- `pet_ids`
- `organization_ids`

### Character Ledger

Current traits are not enough.

Every major character should accumulate a ledger that explains why the world knows them.

Suggested fields:

- `major_events`
- `relationships_history`
- `wins`
- `losses`
- `promises_made`
- `promises_broken`
- `betrayals`
- `predictions_made`
- `predictions_failed`
- `threads_touched`
- `institutional_roles`
- `myth_tags`

Identity persistence should come from accumulated history, not from the LLM deciding someone feels important.

### Relationships

Relationships should not be one scalar. They should be multi-axis:

- affection
- trust
- dependency
- resentment
- attraction
- loyalty
- fear
- familiarity
- history depth

### Organizations

Examples:

- workplaces
- families
- criminal groups
- sports teams
- clubs
- apartment boards
- agencies
- schools

These should carry policy, money, prestige, obligations, and internal faction lines.

### Assets and Material Life

Track things that create pressure:

- cash
- debt
- housing
- vehicles
- possessions with upkeep
- pets
- tools
- contracts
- recurring bills
- social obligations

### Situation Objects

ForkUniverse needs explicit living-problem objects such as:

- expiring contract
- hidden affair
- broken vehicle
- looming rent
- custody conflict
- election challenge
- injury rehab
- mystery clue chain
- promotion race

Situation objects should decay, intensify, split, merge, or resolve.

## World Memory Hierarchy

ForkUniverse should not treat all memory as equally available.

It likely needs at least four levels:

- `ImmediateMemory`
- `RecentHistory`
- `HistoricalRecord`
- `Mythology`

Example:

- yesterday: Jason lost his job
- last month: the quarry collapse happened
- three years ago: the Great Fire

The important payoff is not just recap compression.

It is perspectival memory:

- people forget differently
- institutions archive differently
- rumors distort differently
- mythology keeps shape while losing detail

This should affect narration, prediction, trust, and thread revival.

## Event Taxonomy

ForkUniverse should emit small, typed, evidence-rich events, not prose paragraphs.

Suggested top-level event families:

- `life_change`
- `relationship_shift`
- `obligation_created`
- `obligation_risk`
- `contract_warning`
- `contract_resolved`
- `resource_gain`
- `resource_loss`
- `institutional_move`
- `public_incident`
- `private_incident`
- `dream_progress`
- `dream_abandoned`
- `prediction_opened`
- `prediction_settled`
- `thread_opened`
- `thread_heated`
- `thread_cooled`
- `thread_resolved`
- `episode_boundary`
- `arc_threshold`
- `audio_signature_change`

Each event should support:

- who was involved
- where it happened
- what changed
- why it changed
- confidence / determinism grade if needed
- story pressure delta
- suggested audio signature

## Narrative Surface API

ForkUniverse should emit narrative surfaces, not just "story."

Suggested surface families:

- `breaking_development`
- `rumor`
- `character_thought`
- `recap`
- `prediction`
- `prediction_scorecard`
- `open_thread`
- `thread_resolution`
- `major_event`
- `historical_summary`
- `institutional_bulletin`
- `myth_echo`

Radio OS can then decide how to broadcast those surfaces.

## Time and Continuity

ForkUniverse should adopt Oracle Kingdom's absence model, generalized.

### Time Compression Model

The engine needs a formal real-time to sim-time contract.

At minimum, support:

- fixed rate
- adaptive rate
- operator-defined presets

Examples:

- `1 minute real = 1 day simulated`
- `1 minute real = adaptive world rate`

Adaptive rate is useful when:

- the listener is present and wants richer moment-to-moment continuity
- the listener is away and the world needs efficient compression
- different domains need different fidelity

The coder should be able to point to a single time policy object rather than infer timing from scattered loops.

### Active

- full-fidelity simulation
- detailed event emission
- short-latency narration opportunities

### Idle

- reduced-fidelity simulation
- summarize repeated low-impact loops
- maintain important micro-state

### Deep Sleep

- epoch compression
- preserve major changes, not every tiny beat
- generate a while-you-were-gone chronicle first, then resume live state

Deep Sleep output should include:

- elapsed in-world time
- major relationship changes
- deaths, breakups, promotions, bankruptcies, births, departures
- prediction scorecard
- unresolved new tensions
- why today's world is different from the last heard world

Deep Sleep should explicitly re-evaluate:

- active threads
- dead threads
- mythologized threads
- prediction scorecards
- identity ledgers
- entropy outcomes

## Story Formatting Layer

ForkUniverse should not hardcode one narrative grammar.

Instead it should expose formatting parameters such as:

- mode: `episodic | longform | continuous`
- tone mix
- genre weighting
- cast size
- starting context
- reset elasticity
- conflict intensity
- coincidence tolerance
- dialogue density
- violence ceiling
- romance ceiling
- absurdity ceiling
- narration cadence

This lets one seed kernel power many listening experiences.

## Entropy and Failure Systems

ForkUniverse should constantly generate degradative pressure, not only generative pressure.

Required entropy families:

- death
- decay
- debt failure
- contract failure
- relationship decay
- dream abandonment
- injury and limitation
- corruption
- institutional rot
- scarcity
- contradiction
- disproven belief

Worlds become interesting because things fail, rot, and disappoint.

Entropy should not be treated as random punishment. It is a pressure source that:

- opens threads
- resolves predictions negatively
- changes character identity
- creates mythology
- creates new power vacuums
- forces adaptation

## Audio Production Signatures

ForkUniverse should emit audio intent metadata so Radio OS Studio can manage the layer stack.

Suggested signatures:

- `cold_open`
- `walk_and_talk`
- `sitcom_bed`
- `laugh_button_soft`
- `laugh_button_big`
- `dramatic_hush`
- `stinger_reveal`
- `domestic_ambience`
- `vehicle_motion`
- `institutional_tension`
- `victory_release`
- `lonely_afterglow`
- `meanwhile_transition`
- `while_you_were_gone`

This is exactly the kind of typed production cue Neikos and Oracle already hint at with archetype voices and special audio handling.

## Radio OS Integration Contract

ForkUniverse should fit Radio OS using the established pattern already visible in FTB, Oracle Kingdom, Neikos, and generated stations.

### Required Runtime Pieces

1. A ForkUniverse controller/world engine
2. A feed plugin that advances the sim and emits event candidates
3. A ForkUniverse meta plugin that:
   - ranks evidence
   - collapses raw events into beats
   - remembers open loops
   - tracks threads and prediction state
   - generates narration without fabricating facts
4. A station manifest defining voices, pacing, quotas, and mix weights

### Non-Goals

- no Radio OS scheduler rewrite
- no TTS pipeline rewrite
- no special-case runtime branch just for ForkUniverse

If ForkUniverse needs a totally custom pipeline, the design is probably drifting away from the strongest part of Radio OS.

## Data Persistence

ForkUniverse should persist at least six layers:

1. `world_state`
2. `event_ledger`
3. `character_ledger`
4. `story_memory`
5. `thread_book`
6. `prediction_book`

Recommended rule:

- the event ledger stays factual
- character ledger stores accumulated identity history
- story memory stores compressed interpretation
- thread book stores unresolved and resolved questions
- prediction book stores forward claims and later settlement

This keeps replay, recap, and calibration honest.

## Build Plan

### Phase 1: Engine Skeleton

Build:

- seed normalization
- RNG namespaces
- core entity types
- relationship graph
- assets/contracts/money primitives
- time modes
- event ledger
- thread entities
- character ledgers

Deliverable:

- deterministic headless sim with repeatable starts

### Phase 2: Causal Life Pressure

Build:

- obligations
- household economics
- job/status systems
- romance/friendship strain
- dream and fear pressures
- simple decree/influence input
- entropy systems
- memory hierarchy

Deliverable:

- world changes meaningfully from systemic pressure, not just random dice

### Phase 3: Story Compiler

Build:

- beat collapsing
- open-loop memory
- formal thread tracking
- prediction-resolution loop
- episode and chronicle compiler
- while-you-were-gone digests
- narrative surface API

Deliverable:

- narratable output without needing handcrafted prose everywhere

### Phase 4: Radio Bridge

Build:

- feed plugin
- meta plugin
- station manifest
- audio signature metadata

Deliverable:

- a playable ForkUniverse station inside Radio OS with no Radio OS core changes

### Phase 5: Domain Adapters

Build import/adaptation paths for:

- From The Backmarker data
- Oracle Kingdom-style decree semantics
- Neikos-style authored world packs
- ATL-style prediction/evidence overlays

Deliverable:

- ForkUniverse becomes a host engine for old worlds, not just a brand-new silo

## First Implementation Slice

The safest first slice is not "make the full endless universe."

It is:

1. one seed
2. one city/district
3. 12-20 characters
4. households, jobs, money, contracts, relationships
5. one format mode: `continuous`
6. one absence-resume pipeline
7. one Radio OS station voice
8. one formal thread system
9. one prediction-resolution loop

That slice is enough to prove:

- deterministic starts
- ongoing world continuity
- evidence-backed narration
- synthetic prediction tracking
- audio signature tagging
- unresolved thread persistence
- entropy-driven change

## Decision Summary

ForkUniverse should be built as:

- a deterministic cold-layer simulation
- a persistent causal universe simulator that produces narrative pressure as a first-class output
- with causal ripple mechanics
- with first-class story threads
- with prediction-resolution loops
- with identity ledgers and memory hierarchy
- with explicit absence reconstruction
- with time compression
- with narrative surface emission
- with prediction scoring
- with entropy and failure systems
- with mode-sensitive story formatting
- and with Radio OS integration through normal feed/meta-plugin contracts

The engine is new, but most of the hard ideas are already proven in your codebase.
