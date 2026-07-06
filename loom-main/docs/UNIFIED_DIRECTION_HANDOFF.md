# Unified Direction Handoff

Status date: `2026-06-18`

This document is for the next coder picking up Loom's current direction.

It is intentionally practical:
- what we are trying to build
- what already exists
- where the real files are
- what is still rough or unresolved
- what to do next

Important companion docs:
- `loom-main/docs/CONCEPT_BRICK_CONTRACT.md`
- `loom-main/docs/CLUB_CATALOG_PYTHON_ROUNDUP.md`
- `loom-main/docs/RIBBONOS_SHELL_CONTRACT.md`

## 1. The Current Direction

The project is converging on this seam:

`human -> optional small LLM ingress -> deterministic judge/planner -> deterministic retrieval/synthesis -> booth/decoder/human`

Important constraint:
- the LLM is not the answer engine
- the LLM is only an interpreter on ingress
- final answers should remain deterministic and evidence-backed

Working mental model:
- the LLM is a lawyer for the human
- the deterministic engine is the judge
- the loombit library is the routeable knowledge substrate
- the booth/decoder surfaces are codecs and inspection tools

## 2. What We Are Building

There are now three closely-related tracks:

1. Deterministic query and answer stack
- retrieve evidence
- choose a rigid synthesis shape
- return route traces and citations

2. Loombit knowledge substrate
- compact binary artifacts
- shared dictionaries
- packed banks instead of millions of tiny files
- deterministic indexes for routing

3. Renderer / carrier surfaces
- original booth audio path remains separate
- booth v2 explores alternate deterministic carriers
- decoder verifies or recovers structure from those carriers

The long-term picture is:
- one unified HTML can still work without any LLM
- an LLM toggle can optionally call a local backend
- that backend writes a ticket
- the deterministic engine routes through loombits and answers with citations

## 2.0 Ribbon OS Reinterpretation

One major simplification has become clear:

Radio OS should be reinterpreted as a federation shell.

Working rename in our heads:
- `Radio OS` -> legacy name
- `Ribbon OS` -> clearer future role

The important change is not branding.
It is architectural meaning.

Old mental model:

`Radio OS -> plays stations`

New mental model:

`Ribbon OS -> hosts federations -> runs concepts -> exchanges packets -> renders outputs`

This is a better fit for what the runtime UI already is.

### Shell correction now locked in

One practical correction is now explicit:

- RibbonOS should not open by stealing the whole viewport by default
- the shell should launch safely windowed first
- fullscreen remains an explicit mode, not the initial ambush
- the card browser should behave like a carousel/dock overlay, not a stock scrollbar admin strip
- the ribbon stage must stay visually important while the overlay is present

This matters because the first rough Python shell proved how easy it is to drift from:

`ribbon-first launcher`

into:

`generic Tk control panel`

The contract now rejects that drift.

### What a station now means

A station is no longer primarily an audio stream.

A station is:
- a world
- a federation
- a `.oradio` artifact
- a container of concepts and surfaces

So these cards:
- `KerbalFM`
- `Neikos Expedition`
- `Night City Chronicles`
- `Oracle Kingdom`

should be understood less as radio stations and more as worlds/federations.

### What widgets now mean

The current widget menu already hints at the real architecture.

Entries like:
- `bluesky`
- `callin`
- `character_mix`
- `context_memory`
- `event_explorer`
- `flows_control`
- `ftb_calendar`

are not really radio widgets.

They are concept surfaces.

That is the correct reinterpretation.

### New stack

The stack should now be understood as:

- `Ribbon OS` = federation shell
- `OpenCloset` = agentic harness layer around the shell
- `Club` = machine-level membership, resolver, and bouncer
- `.oradio` = station/world/federation manifest
- concept bricks = small Python units
- widgets/windows = rendered concept surfaces
- packets = language between concepts
- receipts = proof and traceability

### OpenCloset is not a normal brick

OpenCloset should be treated as its own high-level layer.

Not:
- one small concept brick
- one ordinary plugin

Instead:
- agentic harness layer
- shell-management layer
- orchestration and authoring layer around Ribbon OS

The OpenCloset harness should be decomposed into harness abilities, not reduced to "just another brick."

Those harness abilities are specifically oriented around managing the Ribbon OS experience, for example:
- opening stations
- orchestrating stations
- creating new stations
- creating new bricks
- reading files
- inspecting packets and receipts
- helping port and manage existing OpenCloset functions

Important invariant:
- nothing changes about execution authority
- the deterministic engine still executes
- the LLM remains ingress-only

So the safe mental model is:

- human asks
- LLM interprets ingress only
- OpenCloset harness manages shell/workflow
- deterministic engine executes
- Ribbon OS hosts and renders

### Audio CLI is a separate high-level capability

`audio_cli` should also be treated as a high-level shell capability, not merely a low-level brick.

Why:
- it exposes shell-level needs
- it exposes station-level needs
- it already represents a serious runtime/output surface

So there are at least two important scales of composition:

1. Shell-level composition
- OpenCloset harness
- Audio CLI
- Club
- Ribbon shell surfaces
- packet bus / service registry

2. Station-level composition
- routers
- evidence scorers
- narrators
- media/flow controllers
- world/state bricks
- citation and render surfaces

This distinction should remain explicit.

### Shared brick model

The next important abstraction is:

`one brick instance -> many station subscribers`

Meaning:
- do not run the same brick ten times unless it is genuinely station-specific
- run it once when possible
- publish packets once
- let multiple active stations subscribe

That gives a shape like:

`brick -> packet bus -> station A window / station B renderer / station C tape / station D radio segment`

This is a big part of how the system stays light even when many federations are active at once.

It also means high-level layers like OpenCloset and Audio CLI can sit above ordinary brick execution without forcing every concern into the same scale.

### Why this matters

This solves an old project-organization problem.

Instead of:
- ATL as one app
- Oracle Kingdom as one app
- Neikos as one app
- Night City as one app

we can think in:
- one shell
- many worlds
- each world as a federation
- each federation assembled from concept bricks

That is much closer to an operating-system shell than a radio player, which is exactly why the old UI suddenly makes more sense.

### Important warning about `bookmark.py`

`bookmark.py` or similar large runtime files are not brick 1.

They are:
- legacy substrate
- migration source
- proof that the shell/UI problem is already partly solved

They are not:
- the new unit of authorship
- the new size target
- the new brick model

So the rule is:

`bookmark.py` is a shell seam to decompose, not a template for future bricks.`

That distinction must stay explicit or the brick discipline collapses immediately.

## 2.0.2 The Simulator Has Snapped Into Place

One important ambiguity is now resolved:

`bookmark.py` is not a brick.

It is not merely a large runtime we happen to still have around.

It is better understood as:
- a simulator
- an inhabitable authoring environment
- the place where `.oradio` stations are built, tested, learned, and emitted

Analogy:
- Xcode simulator for an iPhone app
- a game-engine viewport
- Photoshop or Blender as an authoring surface

This matters because it resolves an old point of contention:
- a brick should stay small and concept-bounded
- a simulator is allowed to be richer because it is not the authored unit

### What the simulator does

The simulator is where the user:
- opens a station-in-progress
- drags in bricks
- configures windows/surfaces
- tests TTS and voice behavior
- tests plugin and meta-plugin behavior
- shapes station theme and layout
- lives inside the federation long enough to discover what the `.oradio` actually wants to be
- exports or writes the resulting `.oradio` artifact

This is why the old Radio OS runtime suddenly fits so well.

What once looked overbuilt now becomes exactly the right tool:
- it already has TTS
- it already has a plugin contract
- it already has a meta-plugin contract
- it already has expandable/closable windows
- it already has a theme system
- it already behaves like an inhabitable runtime rather than a thin form wizard

### Consequence: no more station wizard

We should stop thinking in terms of:
- new station wizard
- studio form that emits a `.oradio` in one pass

Instead:
- launch the simulator from Ribbon OS
- inhabit the station
- assemble and test the federation
- let the resulting `.oradio` emerge from that lived configuration
- have the simulator write the `.oradio` file directly

So the simulator replaces the old "wizard" mental model with a stronger one:

`simulate -> configure -> inhabit -> export`

Important clarification:
- `bookmark.py` lineage is not only a viewer or runtime
- it must be treated as an authoring simulator that can output `.oradio` files
- that output behavior is part of why it matters more than an ordinary brick

### Theme implication

The existing theme/runtime system should be leveraged directly into station authorship.

Meaning:
- theme is not just shell chrome
- theme state is part of the authored station identity
- the simulator can become the place where a `.oradio`'s theme is discovered and saved

This fits the older Ribbon OS / Godot shell thinking very naturally:
- splash/boot feel
- carousel feel
- theme state machine
- style/intensity/patterns controls
- station-world identity

### Revised hierarchy

It helps to keep the layers separate:

- `RibbonOS` = launcher shell / carousel / theme host
- simulator runtime (`bookmark.py` lineage) = authoring and testing environment for stations
- `.oradio` = exported unit of simulation / federation / station
- bricks = the smaller concepts assembled inside the simulator and station

That hierarchy is cleaner than trying to flatten everything into "plugins" or "bricks."

## 2.0.3 `.oradio` Means Unit Of Simulation

`.oradio` should now be understood as the deployable assembled tower.

It can contain:
- bricks
- connections
- definitions
- plugin references
- theme identity
- surfaces
- bindings
- runtime intent

So a `.oradio` is not merely:
- a station preset
- an audio playlist
- a tiny descriptor with decorative metadata

It is:
- a unit of simulation
- a federation artifact
- a world/station ready to inhabit

This language matters because the shell, simulator, and artifact each occupy different roles.

## 2.0.4 Radio OS Can Become The First `.oradio`

Another convergence point is now clear:

`RadioOS` does not need to stay thought of as a standalone app first.

It can become:

`RadioOS.oradio`

That is a very strong proving artifact.

Meaning:
- someone receives `RadioOS.oradio`
- they do not need Ribbon OS already installed in some heavy traditional sense
- they double-click the `.oradio`
- Club provisions what is needed
- the runtime opens
- they inhabit the radio world that was authored for them

Inside that single `.oradio`, they could:
- turn the dial
- move among multiple stations/worlds
- hear computed synthesis
- experience the authored shell/theme
- receive the cheap deterministic version of the system without requiring a hosted LLM answer engine

This is one of the clearest demonstrations of the whole direction because it proves:
- packaged station/world artifact
- Club-managed provisioning
- deterministic synthesis as cheap runtime intelligence
- shell, theme, and station all converging into one shareable object

### Why this is especially important

The system is now less burdened by LLM cost than earlier versions.

That means the magic can come from:
- computed synthesis
- turn-the-dial interaction
- shell aesthetics
- crackle/noise/radio texture
- deterministic variation

not from an always-on hosted model.

That makes the first shareable `RadioOS.oradio` feel much more realistic and much more aligned with the actual Loom direction.

## 2.0.1 The Club Fits This Direction

The existing Club architecture meshes with the Ribbon OS direction extremely well.

Current Club idea in the repo:
- `.oradio` stays tiny
- heavy capabilities are resolved machine-level
- the system asks once when something is new, changed, or vanished
- answers are remembered and reused

That is already very close to what we need for a federation shell.

Relevant files:
- `loom-main/oradio_engine/club.py`
- `loom-main/provisioning.py`
- `loom-main/club_gate.py`
- `loom-main/descriptor_club_gate.py`
- `loom-main/docs/SIMULATION_ENGINE.md`
- `loom-main/README.md`

### What the Club should mean in Ribbon OS

In the new interpretation, the Club is not just for:
- voices
- piper
- llm
- theme clips
- antenna targets

It should also become the machine-level manager for brick-era concerns such as:
- installed brick families
- remembered brick asset paths
- remote/plugin consent
- window/widget surface availability
- shared shell capabilities
- optional model endpoints used by ingress/fixer/manager layers

It should also help with scope-aware brick registration:
- shared singleton services
- per-station instances
- per-window surfaces

So the new mental model is:

- `Ribbon OS` = shell
- `Club` = membership + resolver + bouncer
- `.oradio` = federation declaration
- bricks = runnable concepts

### Why the Club is a good fit

The Club already has the right laws:
- configure once
- reuse forever
- ask only when genuinely needed
- separate tiny artifact from heavy endpoint state
- keep machine memory outside the tiny declaration

Those laws are exactly what we want for brick management too.

### What the Club should probably manage next

In addition to current capability resolution, the Club should eventually know about:
- brick registries
- brick installation status
- brick manifests cached locally
- shell surface packs
- ribbon/theme libraries
- optional local model endpoints by role
- plugin/code consent for fetched bricks

This suggests a future split like:
- artifact declaration stays in `.oradio`
- machine-level fulfillment lives in the Club

That is a very strong fit.

## 2.1 Agentic Coding Direction

This same determinism-first seam should also shape how we build software locally.

The coding harness direction is:

`determinism first -> small model when stuck -> bigger model only when truly stuck -> human for goal/risk decisions`

This is the manager escalation ladder.

The important design rule is:
- bigger models are not always on
- bigger models wake up because of failure conditions
- deterministic machinery should do the cheap repeatable work first

Working slogan:

`Cheap machinery does the work.`
`Small models write the tickets.`
`Big models wake up only for trouble.`

### Escalation ladder

Level 0: deterministic recipe
- apply known scaffold
- rename symbols
- add endpoint
- add test
- run formatter
- run tests

Level 1: tiny model ticket writer
- parse human request
- fill a simple task ticket
- choose a likely recipe

Level 2: small local fixer
- inspect failing file or test
- revise the ticket
- suggest a constrained patch

Level 3: manager model
- diagnose architecture issue
- split the task
- rewrite the plan
- unblock repeated failure

Level 4: human
- decide if the goal changed
- approve risky refactor
- break ties when constraints conflict

### Example escalation triggers

- tests fail twice
- same file patched three times
- syntax error survives deterministic repair
- ticket confidence below threshold
- diff exceeds allowed size
- dependency request appears
- architecture boundary detected

The harness should be able to say things like:

- `Deterministic pass failed. Escalating ticket to small fixer.`
- `Small fixer could not stabilize patch. Escalating to manager model.`

### Example escalation record

```json
{
  "task": "add evidence drawer",
  "level": 2,
  "reason_for_escalation": "test_failed_twice",
  "last_error": "citation_id missing from drawer payload",
  "files_touched": ["ui.js", "local_ingress_server.py"],
  "diff_size": 84,
  "manager_request": "revise ticket only; do not write final answer"
}
```

This matters because it keeps model usage inspectable rather than mystical.

## 2.2 Brick Law For Python

The repo should move toward concept bricks, not giant engine files.

Hard rule:
- Python file soft cap: `300` LOC
- Python file hard cap: `400` LOC
- once a file pushes past roughly `300`, it is in split territory
- if it crosses `400`, split the concept

The intended unit is:

`one file = one concept brick`

Not:
- one engine per file
- one huge service file
- one magic integration blob

Why this matters:
- local small models can understand one brick at a time
- deterministic patch recipes work better on small surfaces
- tests become narrower and more truthful
- contracts become explicit instead of implied

The design law is:

`Engines are federations.`
`Concepts are files.`
`Packets are the language between them.`
`Receipts are the proof.`

## 2.3 Concept Plugin Contract

Each brick should declare:
- identity
- inputs
- outputs
- dependencies
- deterministic function
- tests
- receipts

Illustrative shape:

```python
CONCEPT = {
    "id": "loom.route.loombit",
    "kind": "router",
    "version": "0.1.0",
    "inputs": ["QueryTicket", "LoombitIndex"],
    "outputs": ["RouteTrace"],
    "requires": [],
    "provides": ["route.loombit"],
    "deterministic": True,
}

def inspect():
    return CONCEPT

def validate(input_packet):
    ...

def run(input_packet, context):
    return output_packet

def receipts(output_packet):
    return [...]
```

This is not a final syntax law yet.
It is the right shape.

The important part is that each brick has:
- a stable identity
- a tiny interface
- declared contracts
- receipts that explain what it did

Concrete contract reference:
- `loom-main/docs/CONCEPT_BRICK_CONTRACT.md`
- `loom-main/plugins/concepts/base.py`
- `loom-main/plugins/concepts/template_brick.py`

## 2.4 Federation Model

OpenCloset / OpenClaw should eventually act as the federator.

Its job is:
- load concepts
- inspect contracts
- build a dependency graph
- route packets
- run deterministic bricks
- collect receipts

The key refinement now is that the federator should understand brick scope.

### Brick scopes

Not every brick should be instantiated the same way.

Useful scope classes:
- `shared`
- `station`
- `window`

Meaning:

`shared`
- one process/service instance
- many station subscribers
- good for routers, readers, media control, caches, registries

`station`
- one instance per active station/federation
- good for station state, station renderer state, world-local memory

`window`
- one instance per visible surface/view
- good for panels, drawers, inspectors, render views

This matters because it prevents wasteful duplication.

So instead of building monolithic engines, we build reusable bricks like:

- `rss_reader`
- `tape_appender`
- `query_ticketer`
- `loombit_router`
- `evidence_scorer`
- `answer_renderer`
- `town_crier_voice`
- `radio_segment_scheduler`
- `citation_drawer`

Then an engine or station becomes a recipe:

```yaml
station: atl_radio
uses:
  - tape_reader
  - atl_standings
  - anomaly_detector
  - answer_renderer
  - town_crier_voice
  - citation_receipts
```

More explicit future shape:

```yaml
station: flow_fm
uses:
  media_controller:
    mode: shared
  loombit_router:
    mode: shared
  flow_controller:
    mode: shared
  town_crier_renderer:
    mode: station
  packet_inspector:
    mode: window
```

This matches Loom's older frame surprisingly well:
- `.loom` already thinks in `universe` and `connections`
- we can reuse that instinct
- the connections become explicit concept wiring

So yes, this should feel plugin-like:
- Python concept bricks
- explicit contracts
- explicit connections
- composable local systems

### Packet bus idea

The packet bus is now an important unification seam.

The intended pattern is:
- bricks compute
- bricks emit packets
- stations subscribe
- windows render

This lets one useful brick feed many stations without knowing who is listening.

### Ribbon OS and the federator

The easiest path is not "build a brand new shell."

The easier path is:
- reuse the Radio OS shell shape
- reinterpret it as Ribbon OS
- let it host `.oradio` federations
- let windows expose concept surfaces

This means the old shell is now a product advantage.

In practice:
- the browser is the world/federation launcher
- the runtime canvas is the station shell
- the windows are concept surfaces
- the theme editor is a surface/theme control system
- audio is just one output mode among several

OpenCloset should sit around this shell as the harness layer:
- operator workflow
- authoring help
- station creation
- brick creation
- file-reading / repo-reading help
- orchestration of shell and station actions

Audio CLI should remain a strong separate capability:
- voice/output path
- shell-facing media/output tool
- station-facing performance/render tool

The missing host layer is not a new idea.
It is largely the Club:
- what is installed
- what is remembered
- what is allowed
- what is available to this machine

That is why Club should be treated as first-class in the Ribbon OS rewrite.

### Ribbon aesthetic direction

The old Godot `ribbon-os` container appears to be the strongest aesthetic reference we have right now, and should be treated as foundational rather than optional flair.

Reference path:
- `C:\Users\evana\OneDrive\Documents\ribbon-os`

Important observed structure from the real project:
- explicit shell state machine in `main.gd`
- boot/splash to press-to-start transition
- category entry / loop / exit transitions
- carousel fade animations
- top and bottom carousel construction
- theme controls that already think in `style`, `intensity`, and `patterns`

Relevant Godot seams:
- `main.gd`
- `main.tscn`

Key state names already present there:
- `HOME_IDLE`
- `CATEGORY_ENTERING`
- `CATEGORY_LOOPING`
- `CATEGORY_EXITING`
- `REVERSING_ENTRY`
- `CATEGORY_WAITING_FOR_LOOP_END`

This is important because it suggests we already have the right mental model for the shell:
- the ribbon is not wallpaper
- the shell has explicit visual states
- transitions are first-class
- carousel behavior is part of the shell grammar

### Carousel rule

Old concern:
- carousel was placed above the ribbon to avoid covering the ribbon

New rule:
- cover the ribbon when needed
- let the carousel fade away after inactivity

That is the stronger direction.

The ribbon is the stage.
The carousel is an overlay surface, not a permanent reservation of space.

So the intended UX is:
- ribbon visible as the main fullscreen body
- carousel overlays on activity
- carousel fades after inactivity
- shell returns visual focus to the ribbon

### Theme system rule

We should copy the old `ribbon-os` theme structure as a framework.

Meaning:
- theme state machine is foundational
- transition grammar is foundational
- user customization should plug into that framework

This is not glue logic.
It is aesthetic confidence and shell identity.

The shell should think in things like:
- boot sequence
- idle state
- focused/active overlay state
- category entry
- category loop
- category exit
- inactivity fade
- theme style
- theme intensity
- theme patterns

### Product rule

Best future shell is probably not "Radio OS shell" or "Ribbon OS shell" alone.

It is:
- the best carousel and station browser ideas from Radio OS
- the best ribbon/theme/state-machine ideas from Godot Ribbon OS

That combined shell is the target reference.

### Telemetry on bricks

We should also keep light telemetry on bricks themselves.

Not for hype.
For accountability and tuning.

Good brick telemetry fields:
- calls
- latency
- errors
- packet count
- subscribers
- cache hits
- last output
- memory cost
- usefulness score

This effectively turns the codebase into a league of concepts.

That meshes well with existing instincts from ATL-style measurement without forcing every brick into a financial metaphor.

That is much simpler than inventing a second shell from scratch.

## 2.5 Project Memory For The Coding Harness

For coding, the harness needs its own substrate, analogous to loombits/tapes.

Useful project memory layers:
- file map
- function map
- schema map
- test map
- recipe library
- previous patches
- failure traces

This is how the harness becomes a deterministic coding system instead of "LLM writes code freehand."

## 2.6 What This Means In Practice

This direction is optimistic, but realistic if the repo is shaped for it.

It works best when the repo has:
- small files
- clear contracts
- tests per module
- stable schemas
- few hidden dependencies
- repeatable commands
- no giant magic files

The critical implication is simple:
- a `1B` or `4B` model will not understand a huge monolith well
- it may do very well on a `220` line brick, one failing test, and one constrained ticket

That is the local-development bet here.

### Migration law

For large legacy files, including Radio OS runtime files:
- do not pretend they are already bricks
- treat them as reference monoliths
- identify responsibilities
- extract stable contracts
- split them into brick families over time

Good early extraction families from the old shell:
- station manifest loader
- window surface registry
- widget descriptor adapter
- layout persistence
- theme application
- packet inspector surface
- pipeline status surface

Good Club-aligned extraction families:
- club-backed brick registry
- club-backed capability resolver
- club-backed theme/ribbon asset resolver
- club-backed model endpoint registry
- club-backed consent surface

Good bus/scope-aware extraction families:
- shared service registry
- packet bus adapter
- station subscription registry
- window surface subscription adapter

Good OpenCloset-harness extraction families:
- station creation harness
- brick creation harness
- file and repo inspection harness
- shell orchestration harness
- station orchestration harness

Good Audio CLI extraction families:
- shell audio control surface
- station voice/render surface
- audio output orchestration

Good shell-aesthetic extraction families:
- ribbon boot sequence adapter
- ribbon state machine surface
- carousel overlay manager
- inactivity fade controller
- theme transition controller
- theme style/intensity/pattern surface

That is the right "big old file -> new brick system" path.

## 3. Real Components That Exist Now

### 3.1 Query packet and deterministic answer contract

Files:
- `loom-main/oradio_engine/packet.py`
- `loom-main/oradio_engine/query_codec.py`
- `loom-main/oradio_engine/query_codec_impl.py`
- `loom-main/tests/test_query_codec.py`

What exists:
- packet version `loom.answer.packet.v2`
- structured answer packet with synthesis plan, route trace, concept citations, and evidence citations
- deterministic query codec path already wired into tests

Useful types in `packet.py`:
- `RouteTraceStep`
- `ConceptCitation`
- `EvidenceCitation`
- `SynthesisPlan`
- `AnswerPacket`

This is now the best contract to preserve during future integration.

### 3.2 LLM ingress seam

Files:
- `loom-main/oradio_engine/ingress.py`
- `loom-main/oradio_engine/ollama_ingress.py`
- `loom-main/tools/query_ingress.py`
- `loom-main/tests/test_query_ingress.py`

What exists:
- candidate briefs from messy human queries
- deterministic arbitration over those candidates
- optional local Ollama translator path
- safe fallback when the model is weak or unavailable

Important behavior:
- translator output is advisory, not final authority
- deterministic judge still chooses what to run
- translator-added unstable scopes like `latest` are sanitized

### 3.3 Deterministic answer synthesis

Files:
- `loom-main/oradio_engine/answer_synthesis.py`
- `loom-main/tests/test_answer_synthesis.py`

What exists:
- a first synthesis layer that chooses a rigid answer family and fills it deterministically

Current template families:
- `synthesis_summary`
- `ranked_answer`
- `definition`
- `direct_answer`
- `chronology`
- `cause_because`
- `count`
- `comparison`
- `route_not_answerable`
- `uncertainty_boundary`

This is the start of the "50 templates later, but small safe set first" plan.

### 3.4 Local ingress server

Files:
- `loom-main/oradio_engine/local_ingress_server.py`
- `loom-main/tools/loom_ingress_server.py`
- `loom-main/tests/test_local_ingress_server.py`

What exists:
- a minimal local server seam for UI integration

Current endpoints:
- `POST /ticket`
- `POST /answer`
- `GET /evidence/<citation_id>`

This is the most direct backend seam for a future unified HTML with an LLM toggle.

### 3.5 Loombit binary floor

Files:
- `loom-main/loom/loombit.py`
- `loom-main/tools/loombit.py`
- `loom-main/spec/LOOMBIT_V1.md`
- `loom-main/tests/test_loombit.py`

What exists:
- canonical loombit binary format
- shared-string dictionary support
- external `.ldict` support
- strict mode where authored strings are not left plainly visible in the `.loombit`

This is the current binary floor for Loom-era declarations.

### 3.6 Text to loombit compiler

Files:
- `loom-main/loom/text_loombit.py`
- `loom-main/tools/text_to_loombit.py`
- `loom-main/tests/test_text_to_loombit.py`

What exists:
- deterministic UTF-8 text chunker
- shared dictionary emission
- chunk loombits plus a root text index loombit

This was the first general-purpose large-text path before the Wikipedia-specific pilot.

### 3.7 Loombit routing

Files:
- `loom-main/loom/loombit_route.py`
- `loom-main/tests/test_loombit_route.py`

Related support:
- `loom-main/loom/loombit.py`

What exists:
- deterministic routing over `loombit_index`
- support for `bucket`
- support for `gradient`
- support for `tags`
- path classification recognizes `.lbpack` and `.idx`

This is the first actual "bloodhound nose" over the loombit tree.

### 3.8 Wikipedia multistream index pilot

Files:
- `loom-main/loom/wikipedia_index_loombit.py`
- `loom-main/tools/wikipedia_index_loombit.py`
- `loom-main/tests/test_wikipedia_index_loombit.py`

Source file:
- `D:\openclaw\opencloset\enwiki-20260601-pages-articles-multistream-index.txt`

Existing output example:
- `D:\openclaw\opencloset\wikipedia_index_pilot2\snapshots\2026-06-01\`

What exists:
- parses source rows as `offset:page_id:title`
- stable routing leaves for Wikipedia index entries
- shard rule based on normalized title prefix
- per-shard `.lbpack`
- per-shard `.idx`
- per-shard `.ldict`
- shard `.loombit` indexes
- root `.loombit` index
- human `manifest.json`
- RGB / quadrant previews derived from canonical bytes

This pilot does not encode article meaning yet.
It encodes the Wikipedia address universe first.

### 3.9 Frontend / carrier surfaces

Files:
- `d:\openclaw\opencloset\dbooth.html`
- `d:\openclaw\opencloset\decoder.html`
- `d:\openclaw\opencloset\booth-v2.html`

Current roles:
- `dbooth.html`: original timestamp booth path, still important
- `decoder.html`: paired decoder / verifier
- `booth-v2.html`: alternate deterministic carrier lab

`booth-v2.html` currently explores:
- varint byte stream views
- glyph strings
- ASCII path tape
- SVG scatterplot
- PNG mosaic canvas
- loom-pixel PNG canvas
- timestamp-style audio

The original audio booth should remain separate while v2 evolves.

What changed recently:
- `booth-v2.html` now emits a real loom-pixel carrier, not just a plain mosaic
- the loom-pixel frame currently uses `LPX1` magic + varint lengths + checksum + payload bytes
- each framed byte is rendered as one custom cell with a locality-friendly membrane and four inner 2-bit quadrants
- `decoder.html` now has a matching loom-pixel PNG decode path that samples the fixed quadrant geometry back into bytes

Important proof:
- a large loom-pixel PNG made from a long unified coder prompt decoded end-to-end in the current decoder
- that does not prove final storage superiority
- but it does prove loom-pixel is already behaving like a real deterministic carrier rather than a decorative idea

Current experimental note:
- a saved PNG mosaic of the entire unified handoff doc came out around `560 KB`
- that does not prove a canonical storage win
- but it is clearly small enough to justify a serious carrier experiment

Working visual-carrier direction:
- prefer a spiral or center-seed geometry over one long strip
- treat distance from center as a deterministic neighborhood signal
- treat each custom `loom-pixel` as a larger cell, not a literal single PNG pixel
- give each loom-pixel a locality-friendly membrane or border
- use the inner payload area for higher-entropy color payloads

Why this is interesting:
- PNG likes local similarity
- semantic neighborhoods may compress better than random spread
- the artifact stays inspectable with zoom and eyedropper tooling
- location can help mean "where this belongs" while color can help mean "what this is"

Important constraint:
- visual carriers are still experimental or derived surfaces for now
- they should not silently replace the canonical binary/query substrate
- promote them only if measurements say they are honest, decodable, and worth the bytes

### 3.10 Coordinate-map / atlas baseline stack

Files:
- `loom-main/loom/x_region_schema_v1.py`
- `loom-main/loom/y_overlay_schema_v1.py`
- `loom-main/loom/coordinate_record_v1.py`
- `loom-main/loom/atlas_seed_v1.py`
- `loom-main/loom/thesaurus_bridge_v1.py`
- `loom-main/loom/llm_baseline_tape_v1.py`
- `loom-main/loom/baseline_prior_generator_v1.py`
- `loom-main/loom/placement_solver_v1.py`
- `loom-main/loom/loom_pixel_render_v1.py`
- `loom-main/loom/placement_receipt_v1.py`
- `loom-main/tests/test_coordinate_map_v1.py`
- `loom-main/tests/test_baseline_atlas_v1.py`

What exists:
- stable X-region schema
- stable Y-overlay schema
- canonical coordinate record shape
- seed atlas export
- thesaurus bridge scoring
- LLM baseline grading tape
- blended baseline priors from `dictionary + thesaurus + optional llm`
- deterministic placement solver
- deterministic overlay cell plan for loom-pixel rendering

Important rule:
- the LLM does not place pixels
- the LLM only names neighborhoods
- Loom still assigns the address

Current honest state:
- the baseline pipeline is live
- the first atlas is still rough and too identity-heavy for many Wikipedia index titles
- this is expected and useful because the weakness is now visible and measurable

### 3.11 Bundle and bundle-context stack

Files:
- `loom-main/loom/bundle_slot_schema_v1.py`
- `loom-main/loom/loom_bundle_schema_v1.py`
- `loom-main/loom/bundle_receipt_v1.py`
- `loom-main/loom/bundle_context_overlay_v1.py`
- `loom-main/loom/bundle_builder_v1.py`
- `loom-main/tests/test_loom_bundle_v1.py`
- `loom-main/tests/test_bundle_builder_v1.py`

What exists:
- fixed 3x3 bundle slot contract
- 8 overlay slots plus 1 computed `bundle_context` slot
- real bundle artifact builder
- real bundle receipt
- explicit `bundle-of-bundles-ready` nesting hint

Why this matters:
- we are no longer only mapping concepts
- we are mapping the overlays themselves as higher-order objects
- this should matter later for synthesis because the system can reason over local facts, overlay agreement, and bundle-level shape

### 3.12 Baseline population runner

Files:
- `loom-main/loom/baseline_population_runner_v1.py`
- `loom-main/tools/populate_baseline_map.py`
- `loom-main/tests/test_baseline_population_runner_v1.py`

What it does:
- takes a slice of Wikipedia index rows
- emits the LLM grading tape
- accepts returned labels if provided
- blends LLM labels with dictionary/thesaurus baseline
- produces real coordinate records
- produces real placement receipts
- produces first overlay cell plans ready for loom-pixel rendering

Live sample output:
- `D:\openclaw\opencloset\loom-main\outputs\baseline-population-sample\llm_baseline_tape.json`
- `D:\openclaw\opencloset\loom-main\outputs\baseline-population-sample\baseline_population_batch.json`

Current reality check from the first 64-row sample:
- the population runner works
- the artifacts are real
- the first atlas is still too coarse for many early titles like `AfghanistanGeography` and `AfghanistanPeople`
- some rows already separate correctly, like:
  - `AfghanistanHistory -> event`
  - `AssistiveTechnology -> artifact`
  - `AlbaniaGovernment -> governance`

## 4. What The Wikipedia Pilot Proves

The Wikipedia pilot is important because it shifts the repository idea from theory into a real routing substrate.

What it already proves:
- we can avoid per-entry loose file explosion
- we can pack many leaves into banks
- we can traverse `root -> shard -> pack -> idx -> recovered entry`
- we can make the output snapshot-versioned and deterministic
- we can derive RGB inspection surfaces from canonical bytes without making those surfaces the canonical storage

What it does not yet prove:
- semantic compression of article meaning
- efficient full-scale build economics
- that final artifacts beat the raw source bytes for this dataset

## 5. Known Limits And Honest Reality Check

These are the important non-marketing truths.

### 5.1 The current full Wikipedia pipeline is temp-heavy

The first-pass compiler staging is wasteful.

Observed earlier during the full run:
- average source row was about `47.55` bytes
- average temporary spooled row was about `548.85` bytes
- projected spool for the full source was about `14.08 GB`

Meaning:
- the current intermediate form is much fatter than the source
- the final architecture idea may still be sound
- but the current builder implementation needs a streaming or less-bloated staging pass

### 5.2 Final artifacts are better than spool, but not yet smaller than source

From a 5,000-row pilot slice:
- source slice: `153,433` bytes
- temp spool: `2,508,213` bytes
- whole artifact family: `741,845` bytes
- `.lbpack` only: `246,082` bytes

Meaning:
- the final artifact family was much smaller than spool
- `.lbpack` itself was much smaller than spool
- but the total snapshot output was still larger than the raw source slice

So the honest state is:
- packing and routing work
- storage economics are not yet "Wikipedia becomes magically tiny"

### 5.3 Small local LLMs are currently the weak link

Observed on this machine:
- `tinyllama:1.1b` produced empty candidates
- `smollm2:135m` produced empty candidates
- `openbmb/minicpm-v4.6:1b` hit a local 500 load failure
- `rnj-1:8b` worked, but took roughly `18-20s` per ticket in sampled runs

Meaning:
- the ingress seam is correct
- the current local model choice is still a practical bottleneck for fast UX

## 6. Current Repo Layout Worth Knowing

Core docs:
- `loom-main/docs/THE_LOOM.md`
- `loom-main/docs/ARCHITECTURE.md`
- `loom-main/docs/CANON.md`
- `loom-main/docs/SIMULATION_ENGINE.md`
- `loom-main/docs/LOOM_ORADIO_ARCHITECTURE.md`

Core query / packet path:
- `loom-main/oradio_engine/packet.py`
- `loom-main/oradio_engine/query_codec.py`
- `loom-main/oradio_engine/query_codec_impl.py`
- `loom-main/oradio_engine/answer_synthesis.py`
- `loom-main/oradio_engine/ingress.py`
- `loom-main/oradio_engine/ollama_ingress.py`
- `loom-main/oradio_engine/local_ingress_server.py`

RibbonOS shell path:
- `loom-main/Radio-OS/ribbon_shell.py`
- `loom-main/Radio-OS/ribbon_shell_theme.py`
- `loom-main/Radio-OS/ribbon_shell_models.py`
- `loom-main/Radio-OS/ribbon_shell_launcher.py`
- `loom-main/docs/RIBBONOS_SHELL_CONTRACT.md`

Loombit / knowledge substrate:
- `loom-main/loom/loombit.py`
- `loom-main/loom/loombit_route.py`
- `loom-main/loom/text_loombit.py`
- `loom-main/loom/wikipedia_index_loombit.py`
- `loom-main/loom/x_region_schema_v1.py`
- `loom-main/loom/y_overlay_schema_v1.py`
- `loom-main/loom/coordinate_record_v1.py`
- `loom-main/loom/atlas_seed_v1.py`
- `loom-main/loom/thesaurus_bridge_v1.py`
- `loom-main/loom/llm_baseline_tape_v1.py`
- `loom-main/loom/baseline_prior_generator_v1.py`
- `loom-main/loom/placement_solver_v1.py`
- `loom-main/loom/loom_pixel_render_v1.py`
- `loom-main/loom/placement_receipt_v1.py`
- `loom-main/loom/bundle_slot_schema_v1.py`
- `loom-main/loom/loom_bundle_schema_v1.py`
- `loom-main/loom/bundle_receipt_v1.py`
- `loom-main/loom/bundle_context_overlay_v1.py`
- `loom-main/loom/bundle_builder_v1.py`
- `loom-main/loom/baseline_population_runner_v1.py`
- `loom-main/spec/LOOMBIT_V1.md`

Tools:
- `loom-main/tools/loombit.py`
- `loom-main/tools/text_to_loombit.py`
- `loom-main/tools/query_ingress.py`
- `loom-main/tools/loom_ingress_server.py`
- `loom-main/tools/wikipedia_index_loombit.py`
- `loom-main/tools/populate_baseline_map.py`

Tests:
- `loom-main/tests/test_loombit.py`
- `loom-main/tests/test_text_to_loombit.py`
- `loom-main/tests/test_loombit_route.py`
- `loom-main/tests/test_query_ingress.py`
- `loom-main/tests/test_query_codec.py`
- `loom-main/tests/test_answer_synthesis.py`
- `loom-main/tests/test_local_ingress_server.py`
- `loom-main/tests/test_wikipedia_index_loombit.py`
- `loom-main/tests/test_coordinate_map_v1.py`
- `loom-main/tests/test_baseline_atlas_v1.py`
- `loom-main/tests/test_loom_bundle_v1.py`
- `loom-main/tests/test_bundle_builder_v1.py`
- `loom-main/tests/test_baseline_population_runner_v1.py`

Example snapshot output:
- `D:\openclaw\opencloset\wikipedia_index_pilot2\snapshots\2026-06-01\`
- `D:\openclaw\opencloset\loom-main\outputs\baseline-population-sample\`

## 7. What The Ticket Should Mean

The ingress ticket should be treated as a constrained brief, not as the answer.

The useful fields conceptually are:
- query intent
- entities
- time scope
- constraints
- ambiguities
- proposed retrieval plan
- confidence
- alternate parses
- gradient bucket
- aim tokens
- preferred paths

The deterministic side should then:
- accept
- reject
- clarify
- or run multiple candidate routes and score them

This is the right place to keep the system strict while still allowing long, messy human input.

## 8. What Deterministic Synthesis Should Do

The deterministic engine should not "write like an LLM."
It should:
- choose a template family
- fill slots from retrieved evidence
- expose boundaries when support is weak
- keep concept citations separate from evidence citations

Recommended citation layers:

1. Concept citations
- why this route or concept was selected

2. Evidence citations
- the exact leaves, rows, or records supporting the answer

Ideal inspectable trail:
- root index hit
- shard chosen
- bank chosen
- offset record
- recovered leaf
- evidence text or source metadata

This inspectability is one of Loom's strongest differentiators.

## 9. How To Run The Existing Pieces

Focused tests:

```powershell
python -m pytest loom-main\tests\test_answer_synthesis.py loom-main\tests\test_local_ingress_server.py loom-main\tests\test_query_codec.py loom-main\tests\test_query_ingress.py
```

Loombit tests:

```powershell
python -m pytest loom-main\tests\test_loombit.py loom-main\tests\test_text_to_loombit.py loom-main\tests\test_loombit_route.py loom-main\tests\test_wikipedia_index_loombit.py
```

Query ingress CLI:

```powershell
python loom-main\tools\query_ingress.py --help
```

Local ingress server:

```powershell
python loom-main\tools\loom_ingress_server.py
```

Wikipedia index pilot CLI:

```powershell
python loom-main\tools\wikipedia_index_loombit.py --help
```

Baseline population CLI:

```powershell
python loom-main\tools\populate_baseline_map.py D:\openclaw\opencloset\enwiki-20260601-pages-articles-multistream-index.txt -o D:\openclaw\opencloset\loom-main\outputs\baseline-population-sample --max-entries 64
```

## 10. What To Build Next

The next work should be disciplined, not sprawling.

### UI status rule

Do not show LLM "thinking."
Show pipeline state.

Why:
- it is more honest
- it matches the real architecture
- it reinforces that the LLM fills the form and the deterministic engine computes the answer

Preferred framing:
- the LLM fills the ticket
- the engine computes the answer
- the receipts explain the path

Good operational phrases:
- `Writing ticket...`
- `Validating ticket...`
- `Routing evidence...`
- `Computing answer...`
- `Attaching receipts...`

Expanded variants that are also acceptable:
- `Filling query ticket...`
- `Parsing request into ticket...`
- `Stamping ticket fields...`
- `Normalizing query...`
- `Separating human text from route hints...`
- `Checking for unsafe inferred fields...`
- `Aiming the retrieval puck...`
- `Selecting loombit paths...`
- `Routing through the tape library...`
- `Scoring evidence trails...`
- `Binding answer slots...`
- `Preparing citation receipts...`
- `Rendering deterministic response...`

Best short sequence for the UI:

1. `Writing ticket...`
2. `Validating ticket...`
3. `Routing evidence...`
4. `Computing answer...`
5. `Attaching receipts...`

Good multi-step copy example:
- `Writing ticket for the engine...`
- `Ticket written. Routing through loombits...`
- `Evidence found. Rendering cited answer...`

Avoid:
- `thinking`
- `reasoning`
- `cogitating`

### Highest-priority next steps

1. Unify the UI seam
- add a single UI path that can call `/ticket`, `/answer`, and `/evidence/<id>`
- keep deterministic-only mode available
- keep the original booth path intact

2. Clarify Ribbon OS migration
- document the shell as federation host, not radio player
- define `.oradio` station manifests for world/federation loading
- define widget surfaces as concept-surface adapters

3. Define Club-backed brick management
- clarify how Club resolves installed brick families
- clarify how Club remembers shell assets and brick assets
- clarify how Club asks for code/plugin/model consent

4. Define OpenCloset harness role
- document OpenCloset as a high-level harness layer, not a normal brick
- define what harness abilities belong at shell scope
- define how OpenCloset invokes but does not replace deterministic execution

5. Define Audio CLI role
- document Audio CLI as a separate high-level capability
- define shell-level versus station-level audio concerns

6. Define packet-bus and scope rules
- shared vs station vs window instancing
- one brick instance to many station subscribers
- subscription and emission contracts

7. Define shell aesthetic framework
- adopt the Godot ribbon state machine as reference behavior
- define carousel-over-ribbon overlay rules
- define inactivity fade behavior
- define theme style / intensity / pattern controls as first-class shell concepts

8. Build first shell-side bricks
- pipeline status surface
- ticket view
- route trace
- citation drawer
- packet inspector

9. Run the 27B model against the emitted baseline grading tape  ✅ DONE (and far exceeded) — see the "DONE" section at the bottom
- use `loom-main\outputs\baseline-population-sample\llm_baseline_tape.json`
- return labels as a concept-id keyed JSON map
- rerun `populate_baseline_map.py --labels-json ...`
- compare the improved atlas spread against the current identity-heavy baseline

10. Build citation drawers
- answer pane
- concept citations
- evidence citations
- route trace drawer

11. Make the Wikipedia compiler stream better
- reduce or remove bloated intermediate staging
- measure bytes per source row and bytes per final leaf again after the rewrite

12. Tighten the ingress ticket schema
- freeze fields
- make the UI show the raw ticket and the chosen route

13. Improve routing over shard indexes
- use `gradient`, `bucket`, and routing tokens more deliberately
- let the ticket aim at the right region before deterministic sniffing begins

### Medium-priority next steps

14. Add more synthesis families only after retrieval math is trustworthy

15. Expand the loombit library beyond Wikipedia index routing
- entity summaries
- relation leaves
- evidence chunks
- dated events

16. Improve the seed atlas and thesaurus bridges aggressively
- the machinery is working
- the main weakness is baseline vocabulary and bridge quality
- the first 64-row sample proved this clearly

17. Keep RGB lensing derived, not canonical
- it is useful for inspection
- it is not the actual compression win

18. Run a measured visual-carrier experiment
- compare straight mosaic vs spiral mosaic vs loom-pixel membrane layout
- measure raw capacity, PNG size, decode reliability, and inspectability
- keep the experiment derived from canonical bytes first
- only elevate it if it proves itself against simpler binary storage

## 11. Short Version

If you only remember five things, remember these:

1. The correct seam is `LLM on ingress, deterministic on egress`.
2. `AnswerPacket v2` is the current contract worth preserving.
3. Loombits are now real, with dictionaries, routing, and Wikipedia packed-bank pilot output.
4. The current full Wikipedia build path is structurally right but operationally too temp-heavy.
5. Radio OS should be treated as the Ribbon OS federation shell, with old runtime files mined into bricks rather than copied forward as new monoliths.
6. The Club already supplies the right machine-level resolver model for Ribbon OS and should expand to manage brick/capability availability.
7. The system should prefer one brick instance feeding many station subscribers, with explicit `shared`, `station`, and `window` scopes.
8. The Godot `ribbon-os` project is the best current aesthetic reference and should supply the shell's theme state machine, transitions, and carousel-overlay behavior.
9. OpenCloset should be treated as a high-level harness layer around Ribbon OS, not as an ordinary brick.
10. Audio CLI should remain a separate high-level capability that exposes needs at both shell scope and station scope.
11. The deterministic engine still executes; the LLM remains ingress-only.
12. PNG mosaic / spiral / loom-pixel carriers are promising inspectable experiments, and loom-pixel already decodes end-to-end on large prompts, but they still remain secondary until they beat simpler canonical byte storage honestly.
13. The coordinate-map stack now exists for real: atlas, thesaurus bridge, LLM grading tape, placement solver, bundle builder, and baseline population runner.
14. The next quality jump is not more scaffolding. It is feeding the emitted baseline grading tape through the 27B model and measuring how much the atlas spread improves.  ✅ DONE — and the finding flipped: atlas "spread" is a vanity metric, recall@k is the real measure, and a build-time embedding (not more atlas scaffolding) was the real jump (semantic recall 0% → 89%). See the "DONE" section at the bottom.
15. The next real product step is still UI unification around tickets, citations, route traces, concept surfaces, packet-bus rules, Club-backed shell management, OpenCloset harness abilities, and a real ribbon-first shell.

---

## DONE — Atlas retrieval benchmark + embedding pivot (2026-06-19 session)

Everything below is COMPLETE. The rest of this doc above is left intact for pick-up later.
All artifacts live in `loom-main/bench/atlas_recall/` (self-contained; run scripts from `loom-main/`).

### What got built
- A **falsifiable retrieval recall@k benchmark** on the REAL `enwiki-20260601` index: a 20,015-title curated corpus (all gold pages + 20k distractors carved from the real 1.2GB / 25.6M-title dump) compiled through the real `compile_wikipedia_index` + routed through the real `route_index`. Eval = 37 `query→gold-title` pairs split lexical (shares a word) vs semantic (shares none).
- Graded all 20k titles with the local 27B at build time → `corpus_labels_27b.json` (concurrent, resumable, ~0.5s/title, ~3h... settled ~1.1/s → ~5h; healthy region spread, no clump).
- A **build-time embedding bank** (all-MiniLM-L6-v2): 20k titles embedded in 18s → 29MB float32 (`embedding_bank.py`). Compile-time LLM, runtime = dot products.
- A **lens engine** ("one bundle, mapped many ways"): bundle stored once; a "lens" is a projection; re-map all 20k against a new concept axis in ~1ms (one matvec), no second copy (`lens_engine_v1/v2/v3.py`).

### Roadmap items this closes
- **#9** (run 27B on the grading tape) — done and far exceeded.
- **#14 short-version** (next quality jump = feed 27B, measure atlas spread) — done; finding flipped (see below).
- **#16** (improve seed atlas / thesaurus bridge — "main weakness is bridge quality") — root cause found & fixed: the thesaurus **pre-filter was excluding the correct region from the 27B's candidate menu** (Albedo's menu had no `science`); fix = offer all 16 regions → labels correct, zero clump.

### Key findings (receipts)
- **"Atlas spread/entropy" is a vanity metric** (trivially gameable — un-stacking identity just moved the clump to context). The real measure is recall@k vs gold. Built that; it became the instrument for everything after.
- **Baseline floor:** semantic recall is a hard **0%** in flat token-match AND the existing loombit alphabetic-prefix router (which is **net-negative** vs flat: 40% vs 70% lexical @20 — it prunes the gold shard out 60% of the time).
- **Atlas region routing = right NEIGHBORHOOD, no ADDRESS:** 27B region labels put query↔gold in the same region **100%** of semantic queries, but token-only within-region recall stays ~0 (the gold is in the right region, buried).
- **Deterministic ladder** (IDF-rare-token tier → region cell → fractal subdivision → semantic bank) lifted semantic @20 **0% → 44%**, lexical preserved/raised to **80%**. The **fractal/recursion is a carrier/navigation affordance, not a recall lever** (recursive tiles ≤ continuous proximity; region-pair cells stay ~2585 deep).
- **PIVOT — a real build-time embedding ~doubles recall and shows the atlas gating is now a crutch:** pure cosine NN (no region gate, no token tier) = **lexical @20 100%, semantic @20 88.9% (24/27), semantic @1 44.4%**. The SAME embedding collapses to 44% when the atlas region/cell tiers are bolted on (e.g. Democracy → rank 2288 because `shared-region` outranks the embedding match). The whole X-region→cell→fractal stack existed to squeeze recall from WEAK deterministic signals; with a real signal it is unnecessary and degrades quality.
- **Lens/synthesis substrate validated on the embedding bundle:** coherent neighborhoods everywhere (Jupiter→Planet/Europa/Exoplanet; Saxophone→Adolphe Sax/Saxhorn/brass; Volcano→Caldera/geology), lens theming works (Submarine: danger→Kursk, exploration→NR-1), ~2ms per query over one stored bundle.

### Strategy shift (recorded for pick-up)
- **Retrieval quality winner = build-time embedding + cosine NN**, not the deterministic atlas stack.
- **Atlas routing's justification is now SCALE/SPEED** (sublinear search toward 25M; brute cosine is fine at 20k) **and SYNTHESIS structure** (lenses → templates → voice), NOT retrieval quality.
- Synthesis plan unchanged: ~50 deterministic message templates chosen by retrieval + lens relationships, populated with retrieved data + a booth voice (town crier / philosopher).

### Still OPEN (not done — left in the doc above)
- **Runtime-pure query path:** `emb_only` embeds the query at runtime (one tiny ~5ms MiniLM call; docs are build-time). A build-time term-vocab compose would keep runtime fully pure — not yet measured (expected < 89%).
- **Scale test** embedding NN + loombit routing toward 25M (the routing tree's real justification).
- **The synthesis layer itself** (the ~50 templates + voice).
- **Visual-carrier experiment #18** (mosaic vs spiral vs loom-pixel) — only lens-render PNGs done; mp3-degradation dropped as not worth it.

### File map (`loom-main/bench/atlas_recall/`)
- corpus: `eval_set.json`, `corpus_src/`, `make_grading_tape.py`, `grade_corpus.py`, `grade_queries.py`, `corpus_labels_27b.json`, `query_labels_27b.json`
- harness: `run_recall_bench.py` (v1 flat/routed) → `_v4` (fractal) / `_v5` (semantic bank) / `_v6` (recursion) / `_v7` (embedding pivot); reports in `compiled/recall_report_*.json`
- signal: `atlas_address_v1.py`, `semantic_bank_v1.py`, `embedding_bank.py` (+ `emb_bank/`)
- synthesis: `lens_engine_v1.py` (region-score), `_v2` (deterministic bank), `_v3` (embedding bundle); `lenses/*.png`
