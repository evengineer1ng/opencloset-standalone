# Concept Brick Contract

Status date: `2026-06-18`

This document defines the first contract for Loom/OpenClaw concept bricks.

Goal:
- make local deterministic coding tractable
- keep small models useful
- keep big models exceptional
- avoid refactoring 100 bricks later because the contract was vague

This is the contract to freeze early.

## 1. The Core Law

One file is one concept brick.

Hard limits:
- Python soft cap: `300` LOC
- Python hard cap: `400` LOC

If a brick approaches the soft cap, split the concept.
If it exceeds the hard cap, the concept is too broad.

We are not building giant engines anymore.
We are building composable bricks.

Important clarification:
- large legacy runtime files can still be useful
- they are migration sources, not "brick exceptions"
- do not label a 9k or 35k line file as a brick
- extract brick families from it instead

Important second clarification:
- not every useful thing in the system is a brick
- some things are higher-level harness or shell capabilities

Examples:
- OpenCloset harness layer
- Audio CLI
- Ribbon OS shell
- Club

Those may be composed from bricks and may expose brick seams, but should not be flattened thoughtlessly into "just another brick."

## 2. The Four Things That Must Stay Stable

If these wobble constantly, every brick will need refactoring later.

Freeze these early:

1. Brick manifest shape
2. Brick lifecycle functions
3. Packet envelope shape
4. Receipt shape

Everything else can evolve more safely.

## 3. Brick Identity

Each brick needs a stable identity.

Recommended format:

`loom.<family>.<name>`

Examples:
- `loom.route.loombit`
- `loom.query.ticketer`
- `loom.answer.renderer`
- `loom.ui.pipeline_status`
- `loom.ui.citation_drawer`

Good families:
- `query`
- `route`
- `evidence`
- `answer`
- `codec`
- `storage`
- `ui`
- `bridge`
- `voice`
- `world`

## 4. Brick Manifest

Every brick exposes a manifest.

Minimum stable fields:

```python
{
    "api_version": "loom.concept.v1",
    "id": "loom.route.loombit",
    "kind": "router",
    "version": "0.1.0",
    "deterministic": True,
    "inputs": ["loom.ticket.v1"],
    "outputs": ["loom.route_trace.v1"],
    "requires": ["loombit.index"],
    "provides": ["route.loombit"],
    "side_effects": [],
    "ui_slots": [],
    "tags": ["routing", "loombit"],
    "description": "Route a query ticket across loombit indexes."
}
```

Fields to keep:
- `api_version`
- `id`
- `kind`
- `version`
- `deterministic`
- `inputs`
- `outputs`
- `requires`
- `provides`
- `side_effects`
- `ui_slots`
- `tags`
- `description`

Do not make the manifest huge.
It should tell the federator what the brick is and how it plugs in.

## 5. Brick Lifecycle

Every brick should expose the same small interface.

```python
def inspect():
    return CONCEPT

def validate(input_packet, context):
    ...

def run(input_packet, context):
    return output_packet

def receipts(output_packet):
    return [...]
```

This is the part to standardize aggressively.

Why:
- deterministic harness can call every brick the same way
- small models can learn the shape
- tests can be templated
- tooling can auto-inspect bricks

### Function roles

`inspect()`
- returns the brick manifest only
- no side effects

`validate(input_packet, context)`
- checks preconditions
- returns issues, not prose
- should not mutate state

`run(input_packet, context)`
- performs the concept function
- should be deterministic unless the manifest explicitly says otherwise

`receipts(output_packet)`
- explains what happened
- returns compact, inspectable proof objects

## 6. Packet Envelope

Do not make every brick invent its own outer wrapper.

Freeze one packet envelope shape.

Minimum shape:

```python
{
    "packet_type": "loom.ticket.v1",
    "packet_version": "loom.ticket.v1",
    "trace_id": "trace-123",
    "parent_trace_id": "",
    "payload": {...},
    "refs": [],
    "meta": {}
}
```

Stable fields:
- `packet_type`
- `packet_version`
- `trace_id`
- `parent_trace_id`
- `payload`
- `refs`
- `meta`

Rules:
- `payload` is the actual content
- `meta` is for non-core annotations
- `refs` can point to evidence, route nodes, files, or upstream packets
- avoid hiding essential meaning in `meta`

Important:
- typed payloads can still vary by packet type
- the envelope should not

## 7. Receipt Shape

Receipts are part of the contract, not a nice extra.

Minimum shape:

```python
{
    "receipt_id": "r-001",
    "brick_id": "loom.route.loombit",
    "kind": "route_step",
    "label": "selected shard title-ca",
    "refs": ["shard:title-ca"],
    "data": {
        "score": 0.91
    }
}
```

Stable receipt fields:
- `receipt_id`
- `brick_id`
- `kind`
- `label`
- `refs`
- `data`

Receipts should be:
- compact
- inspectable
- deterministic
- machine-readable first

## 8. Result Shape

The harness should not guess what came back from a brick.

Recommended result shape:

```python
{
    "ok": True,
    "output_packet": {...},
    "receipts": [...],
    "issues": [],
    "meta": {}
}
```

Stable result fields:
- `ok`
- `output_packet`
- `receipts`
- `issues`
- `meta`

## 9. UI/UX Should Also Be Bricks

Yes, UI/UX should be represented as bricks.

But not as one giant UI brick.

Treat the UI as a federation of surface bricks.

Good UI brick examples:
- `loom.ui.pipeline_status`
- `loom.ui.ticket_view`
- `loom.ui.route_trace`
- `loom.ui.citation_drawer`
- `loom.ui.evidence_panel`
- `loom.ui.codec_preview`
- `loom.ui.transport_bar`
- `loom.ui.window_surface_registry`
- `loom.ui.widget_descriptor_adapter`
- `loom.ui.layout_manager`
- `loom.ui.theme_surface`

For Ribbon OS specifically, likely shell-aesthetic bricks include:
- `loom.ui.ribbon_boot_sequence`
- `loom.ui.ribbon_state_machine`
- `loom.ui.carousel_overlay`
- `loom.ui.inactivity_fade`
- `loom.ui.theme_transition_controller`
- `loom.ui.theme_controls`

The page or shell can compose them.

### UI brick rule

UI bricks should usually consume packets and emit view models, not raw business logic.

That means:
- deterministic engine computes
- UI bricks format and expose the state
- UI bricks do not invent new truth

Recommended UI output packet types:
- `loom.ui.view_model.v1`
- `loom.ui.panel.v1`
- `loom.ui.status_steps.v1`

Example view-model payload:

```python
{
    "view_id": "pipeline_status",
    "title": "Pipeline State",
    "slots": {
        "steps": [
            {"label": "Writing ticket", "state": "done"},
            {"label": "Routing evidence", "state": "active"}
        ]
    },
    "actions": [],
    "citations": []
}
```

That keeps UI honest.

### Ribbon OS interpretation

The shell itself should be treated as a federation host.

Meaning:
- browser cards launch worlds/federations
- station runtime windows host concept surfaces
- widgets are adapters over concept packets
- theme controls are shell surfaces

So UI bricks do not replace the shell.
They populate and modernize the shell.

### Ribbon-first shell rule

The ribbon should be treated as the main fullscreen stage.

The carousel should be treated as an overlay surface:
- visible on activity
- faded on inactivity
- allowed to cover the ribbon temporarily

Do not permanently reserve dead space just to avoid covering the ribbon.

That old concern should be retired.

### Visual carrier rule

Visual carriers are allowed and worth exploring.

Examples:
- `loom.ui.codec_preview`
- `loom.codec.png_mosaic`
- `loom.codec.svg_scatter`
- `loom.codec.spiral_map`

But keep the roles separate:
- canonical storage or canonical query bytes remain one concern
- derived visual carriers remain another concern

Good current experimental direction:
- spiral or center-seed geometry
- locality-aware neighborhoods
- custom `loom-pixel` cells with an outer membrane and inner payload
- eyedropper-friendly inspection

Why this matters:
- the UI may need to show inspectable codec surfaces
- a visual carrier may become useful even if it never becomes the canonical substrate
- humans should be able to scrutinize routed bytes without pretending the picture invented new truth

Rule:
- visual-carrier bricks decode, preview, map, and inspect
- they do not get to silently redefine the underlying deterministic payload

## 10. Kinds

Recommended brick kinds:
- `ticketer`
- `router`
- `scorer`
- `renderer`
- `codec`
- `storage`
- `bridge`
- `ui_surface`
- `voice`
- `world_operator`
- `planner`

The exact list can grow.
The important part is that every brick declares one kind clearly.

## 10.1 Higher-Level Layers

Some system layers sit above ordinary brick scale.

Current important examples:
- `OpenCloset` as harness layer
- `Audio CLI` as high-level runtime/output capability
- `Ribbon OS` as federation shell
- `Club` as machine-level resolver/bouncer

These should be described as:
- layers
- frameworks
- shell capabilities
- harness capabilities

not merely as ordinary concept bricks.

They still need contracts.
They just live at a different scale.

## 11. Determinism And Side Effects

Default assumption:
- bricks are deterministic

If a brick has side effects, declare them.

Examples:
- file write
- network read
- process start
- model call

Put them in:
- `side_effects`

If a brick depends on model inference, say so plainly in the manifest.

That allows the manager layer to escalate honestly.

## 12. The First Federator Contract

The federator should be able to:
- load bricks
- inspect manifests
- validate packet compatibility
- build a dependency graph
- run bricks in order
- collect receipts

That is enough to start.

Do not overbuild orchestration before the first few bricks exist.

### The Club should back the federator

The Club is the natural machine-level manager for the federator.

That means the federator should not have to invent a second memory/resolution system for:
- installed capabilities
- remembered asset paths
- endpoint locations
- consent
- availability

The Club should eventually help answer:
- is this brick family installed?
- is this required asset known on this machine?
- has this remote/plugin source been consented?
- is this optional model endpoint available?
- what shell/theme/ribbon assets are reusable here?

So the division should be:
- federator = runtime wiring and execution
- Club = machine-level memory, readiness, and resolution

### Scope-aware instancing

The federator should also understand how many instances of a brick to create.

Recommended scope modes:
- `shared`
- `station`
- `window`

Meaning:

`shared`
- one instance for the whole shell
- many station subscribers
- examples: `loombit_router`, `media_controller`, `flow_controller`, `rss_reader`

`station`
- one instance per active federation
- examples: station state, world memory, station renderer state

`window`
- one instance per visible surface
- examples: citation drawer, packet inspector, route trace panel

This should likely become a manifest field later, for example:

```python
"instance_mode": "shared"
```

Do not freeze the exact field name yet, but do freeze the idea.

### Packet bus seam

The federator should prefer:

`one brick instance -> many station subscribers`

instead of blindly duplicating the same computation per station.

That means a useful brick should be able to:
- emit packets once
- serve many subscribers
- remain ignorant of who is listening

Illustrative helper verbs:

```python
emit(packet)
subscribe(packet_type, handler)
receipt(claim)
health()
```

Those helper verbs do not need to be the exact final API.
But the emission/subscription seam is important.

### Legacy shell migration note

If a legacy runtime already does:
- world launch
- window layout
- widget placement
- theme editing

then treat that runtime as a federation-shell reference.

Extract from it:
- shell responsibilities
- layout responsibilities
- widget/surface responsibilities
- station/world manifest responsibilities

Do not call the whole file a brick.

## 13. What Not To Freeze Yet

Do not freeze too early:
- exact folder layout for every brick family
- registry storage format
- advanced UI schema details
- manager/escalation policy details
- optional helper functions

Freeze the small core first.

## 14. Recommended First Brick Families

Best early bricks:
- `loom.ui.pipeline_status`
- `loom.ui.citation_drawer`
- `loom.query.ticket_normalizer`
- `loom.route.loombit`
- `loom.evidence.score`

Good first shell-adjacent bricks:
- `loom.ui.window_surface_registry`
- `loom.ui.widget_descriptor_adapter`
- `loom.ui.layout_manager`
- `loom.ui.theme_surface`

Good first shell-aesthetic bricks:
- `loom.ui.ribbon_state_machine`
- `loom.ui.carousel_overlay`
- `loom.ui.inactivity_fade`
- `loom.ui.theme_transition_controller`

Good first Club-adjacent bricks:
- `loom.club.brick_registry`
- `loom.club.capability_status`
- `loom.club.asset_resolver`
- `loom.club.consent_surface`

Good first bus/scope bricks:
- `loom.runtime.packet_bus`
- `loom.runtime.subscription_registry`
- `loom.runtime.shared_service_registry`

These are small, obvious, and exercise the contract well.

## 15. The Bottom Line

The contract to protect is:

- one brick per file
- one manifest shape
- one lifecycle shape
- one packet envelope
- one receipt shape

If we hold those steady, we can change a lot else without rewriting the whole system.
