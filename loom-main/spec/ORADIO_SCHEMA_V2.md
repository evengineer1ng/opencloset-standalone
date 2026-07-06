# `.oradio` Schema v2

> Loom-era schema note.
>
> This document defines the first owner-aligned `.oradio` contract after the shift from
> app-to-app thinking toward:
>
> - The Loom as the single app
> - `.oradio` as the single artifact
>
> The key rule is unchanged from the earlier portability work:
>
> **A `.oradio` should stay tiny.**
>
> Heavy capabilities resolve through the **club model**, not by bloating each artifact.

---

## 1. Design Law

An `.oradio` should usually measure in:

- kilobytes
- or low hundreds of kilobytes

It should generally stay:

- under 1 MB by default

That means the file should primarily carry:

- identity
- contract
- wiring
- lens
- small declarative recipes
- optional tiny assets like cover art or lightweight templates

It should not normally bundle:

- LLM weights
- large voice packs
- ribbon clip libraries
- large video packs
- big simulation caches
- API secrets
- machine-specific install paths

Those resolve via the **club**.

---

## 2. Club Model

The club model means:

- the machine is configured once
- capabilities are remembered machine-level
- future `.oradio` artifacts reuse that setup
- the artifact describes what it needs, not where the exporter happened to find it

Examples of club-resolved capabilities:

- LLM provider access
- voice libraries
- Piper binary
- ribbon/theme libraries
- API credentials
- local install/game/folder targets
- sensor endpoints
- device bridges

So `.oradio` stays small by saying:

**"I need this capability"**

instead of:

**"I contain this heavy thing."**

---

## 3. Package Shape

The current package remains a zip-compatible `.oradio` with required files:

- `oradio.json`
- `manifest.yaml`
- `requirements.json`
- `requirements.lock.json`

The Loom-era change is not "more bundle by default."
It is "better contract by default."

The top-level schema defined in this pass is for:

- `oradio.json`

This is the artifact descriptor.

---

## 4. Descriptor Responsibility

`oradio.json` should answer:

- what this artifact is
- what simulation/lens it expresses
- what files inside the package define it
- what surfaces it presents
- what time/interactivity model it supports
- what capabilities it expects the club to resolve

It should not try to hold the entire runtime world or giant asset catalog inline.

---

## 5. Core Blocks

The new descriptor schema introduces these conceptual blocks:

### Identity

- format
- version
- artifact id
- title
- creator / loom metadata

### Entry

Pointers to package-local files that define deeper layers:

- manifest
- requirements
- lock
- optional telemetry contract
- optional lens contract
- optional expression contract
- optional world model seed/state
- optional surface/theme contract
- optional transient template contract

### Simulation

Small summary of what kind of simulation this artifact is:

- telemetry-only
- world-sim
- hybrid
- deterministic vs live-fed
- pause/rewind/forward semantics

### Surfaces

Declares what the player should present:

- subtitles
- ribbon
- bubble surfaces
- optional alternate themes/mods

### Club Requirements

Declares what must be resolved machine-level:

- llm
- voices
- piper
- ribbon library
- credentials
- targets

This is the most important tiny-artifact feature.

---

## 6. Telemetry / Lens / World Separation

The schema assumes these layers are separate even when they live close together:

- **telemetry**: what the artifact observes
- **lens**: how it interprets observations
- **world**: what internal state it maintains

This is important because a future `.oradio` may be:

- mostly live telemetry with a light world model
- mostly deterministic simulation with light telemetry
- a hybrid with both

The descriptor should allow all three cleanly.

---

## 7. Theme-first Playback Contract

The schema should preserve the product law that playback is theme-first.

That means the descriptor must be able to declare:

- a primary surface type
- subtitle behavior
- transport interaction semantics
- transient bubble behavior
- ribbon/theme mutation recipe references

Without assuming the visual assets themselves are embedded.

Large ribbon/video/theme libraries should resolve via the club.

Small recipes and templates can live in the package.

---

## 8. What This Pass Does Not Freeze Yet

This pass does **not** fully freeze:

- the exact internal structure of `manifest.yaml`
- the exact telemetry DSL
- the exact lens DSL
- the exact expression DSL
- the exact world-state schema
- the exact ribbon genome schema
- the exact transient bubble template schema

Instead, it freezes the top-level descriptor boundary so we can evolve those companion contracts under a stable package identity.

---

## 9. Companion Schema Added

This pass adds:

- `schema/oradio_descriptor.schema.json`
- `schema/oradio_telemetry_contract.schema.json`
- `schema/oradio_surface_contract.schema.json`
- `schema/oradio_lens_contract.schema.json`
- `schema/oradio_expression_contract.schema.json`
- `schema/oradio_world_contract.schema.json`
- `schema/oradio_theme_recipe.schema.json`

These schemas define:

- the Loom-era descriptor contract for `oradio.json`
- the telemetry intake contract
- the theme-first playback surface contract
- the interpretation lens contract that turns telemetry into evidence, pressure, threads, predictions, memory, and expressive posture
- the expression contract that turns world truth into narration, subtitle flow, catch-up recap behavior, and bubble/headline emissions
- the persistent world-state contract where those pressures, threads, predictions, memories, entities, and lightweight snapshots can actually live
- the tiny visual mutation recipe that binds world state and interaction to club-resolved ribbon/theme states, transitions, filters, and intensity shifts

---

## 10. Next Likely Schema Files

After this descriptor layer, the next strongest schema slices are:

1. `schema/oradio_time_contract.schema.json`
2. `schema/oradio_transient_surface.schema.json`
3. `schema/oradio_manifest.schema.json`
4. `schema/oradio_package_index.schema.json`
5. `schema/oradio_club_contract.schema.json`

That would let the package grow in capability without growing in size.
