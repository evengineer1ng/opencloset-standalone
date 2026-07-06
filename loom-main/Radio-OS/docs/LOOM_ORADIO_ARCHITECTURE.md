0.......# The Loom / `.oradio` Architecture

> Owner-corrected architecture note, 2026-06-12.
>
> This document supersedes older product framing where Radio OS, ForkUniverse, ATL, Oracle Kingdom,
> motion tools, or other simulation-heavy systems were treated as separate apps with integration seams.
> The new center is The Loom. The new artifact is `.oradio`.

---

## 1. Product Boundary

There is one app:

- **The Loom**

There is one artifact:

- **`.oradio`**

Everything else is now a capability layer, building block, or engine contribution inside that system.

Examples:

- Radio OS -> how the artifact speaks
- ForkUniverse -> causal world simulation patterns
- Oracle Kingdom -> decree/ripple/absence/causal-ledger patterns
- FTB -> competitive/economic/contract simulation patterns
- ATL -> evidence/prediction/resolution patterns
- Neikos -> seed-determinism/ledger/outcome-band/ecology patterns
- audio CLI / OpenCloset / theme systems / scouts / telemetry tools -> expressive and observational building blocks

The Loom is the authoring environment.
`.oradio` is the standalone result.

---

## 2. The Core Question

The Loom asks one foundational question:

**What are you simulating?**

That question unifies:

- content selection
- telemetry intake
- simulation rules
- voice
- visuals
- interaction
- packaging

The artifact is not a playlist, not a dashboard, and not a mere reactive skin.
It is a packaged simulation expression.

---

## 3. What `.oradio` Is

An `.oradio` is a standalone simulation artifact that can:

- observe telemetry
- interpret telemetry through a simulation lens
- mutate its visual theme over time
- speak from that lens
- surface transient evidence moments
- remember and evolve state

At minimum, an `.oradio` is the contract between:

- a **world model**
- an **interpretation lens**
- an **expressive surface**

Short form:

**`.oradio` = telemetry interpreter + world model + expressive surface**

Not:

**`.oradio` = media player with a reactive theme**

---

## 4. Telemetry Rule

Everything is telemetry.

Examples:

- RSS feed items
- social density spikes
- local file changes
- market movement
- simulation state transitions
- sports events
- sensor activity
- user transport controls

Telemetry is never "just displayed."
Telemetry is interpreted by the active simulation.

Example:

- an RSS feed does not merely produce headlines
- it becomes evidence, pressure, rhythm, or intrigue inside the active `.oradio`

Another example:

- an ESP32 fridge-door sensor fires unusually often
- the artifact does not just log the count
- the active world interprets it as meaningful behavior
- the voice comments on it from its own lens
- the theme mutates from the same interpreted state

All inputs should learn from each other through the world model.

---

## 5. The Interpretation Lens

Every `.oradio` has a lens.

The lens defines what incoming telemetry means inside the artifact's universe.

Lens examples:

- detective / intrigue
- economic / prediction-market
- social / reputation
- ecological / world-balance
- oracle / decree-and-ripple
- motorsport / performance-and-contract
- domestic / ambient household myth

The Loom artist is not just selecting visuals or prompts.
They are authoring the interpretive lens that converts telemetry into meaning.

That same meaning must drive:

- voice
- subtitles
- theme mutation
- environment growth
- transient surfaces

---

## 6. Playback Identity

The playback shell should be theme-first.

Primary visible structure:

- **main theme surface**
- **bottom subtitle lane**
- **minimal transport controls**

The UX reference is closer to a song player than a library-heavy application.

The user should feel like they are inhabiting a living expressive surface, not operating a control room.

### Theme-first law

The theme is not wallpaper.
It is part of the simulation output.

If the simulation shifts, the theme should embody that shift.

If the user interacts, the theme should react.

If telemetry spikes, the theme should show pressure.

The visual body of the artifact is part of the simulation itself.

---

## 7. Ribbon as the Reference Surface

Current concrete example:

- a Godot-based ribbon system with approximately 20 mutable states and looped source video

Desired direction:

- the ribbon mutates over time
- the ribbon reacts to simulation state
- the ribbon uses filters, blends, and state transitions across video sources
- the ribbon loads its theme pack from the club
- the ribbon follows a state machine for seamless transitions

The Loom should eventually be able to generate theme packs.

Ribbon is the reference visual surface, not necessarily the only future surface.
But it is the current standard bearer for how a simulation should feel visually.

---

## 8. Interaction as Evidence

User interaction is also telemetry.

Transport controls are not just player chrome.
They are simulation-facing events.

Examples:

- `play`
- `pause`
- `rewind`
- `fast_forward`
- bubble pin
- bubble pop
- bubble dismiss
- bubble timeout fade

Each of these may create ripples in:

- theme state
- environment feeling
- interpretation posture
- future narration

This does not mean the simulation lies about reality.
It means the artifact acknowledges interaction as part of the world it is expressing.

---

## 9. Transient Surfaces

Transient surfaces should not present as classic windows.

They should appear as **bubbles**.

Expected behavior:

- soft fade-in notification
- user may click to surface a bubble
- bubble may be pinned
- bubble may be popped closed
- bubble may be allowed to fade away

Each of these interactions should be reflected by the active theme.

Transient surfaces are template-driven visual pages filled with live interpreted data.
They should feel like a tasteful crystallization of the simulation, not an interruption of it.

---

## 10. Internal Layer Model

The clean internal architecture for `.oradio` should be:

### A. Telemetry Input Layer

Observes signals from:

- feeds
- sensors
- simulations
- local files
- APIs
- social streams
- operator/player interaction

Output:

- normalized observations

### B. Interpretation Layer

Transforms observations into simulated meaning through the artifact's lens.

Output examples:

- events
- evidence
- pressure
- suspicion
- calm
- urgency
- prediction candidates
- unresolved threads

### C. World Model Layer

Maintains durable internal state such as:

- entities
- relationships
- contracts
- memories
- predictions
- threads
- environmental pressure
- narrative posture

### D. Expression Layer

Turns world state into:

- spoken narration
- subtitle text
- ribbon/theme mutation directives
- transient bubble content
- ambient visual changes

### E. Interaction Feedback Layer

Feeds user actions back into the world as evidence or modulation.

---

## 11. Relationship to Older Systems

Older systems are no longer peers that need runtime integration.
They are pattern libraries and engine blocks.

### Radio OS

Contributes:

- how it speaks
- live narration discipline
- subtitle and host logic
- telemetry observation concepts

### ForkUniverse

Contributes:

- persistent causal world simulation
- open-thread thinking
- ontology-driven world construction
- on-demand truth computation

### Oracle Kingdom

Contributes:

- decree/ripple causality
- causal ledger
- absence reconstruction
- deterministic layered ticking

### FTB

Contributes:

- deep stats/economy/contracts
- event generation from pure simulation
- competition pressure

### ATL

Contributes:

- prediction loop
- evidence scoring
- hypothesis resolution
- the idea that the world can be surprised

### Neikos

Contributes:

- deterministic seed identity
- ledger normalization
- ecological and outcome-band thinking
- echo-memory patterns

---

## 12. Authoring Implication for The Loom

The Loom should not just ask for:

- a feed
- a theme
- a voice

It should author:

- what telemetry is observed
- what simulation lens interprets it
- what world model rules are active
- what expressive surfaces exist
- what interactions matter
- what gets packed into the final `.oradio`

In practical terms, the Loom is closer to:

- Photoshop for simulations
- an editor for living expressive worlds

Not:

- a station settings form
- a feed packager
- a media-skin generator

---

## 13. Immediate Engineering Direction

Near-term implementation should align around these priorities:

1. Treat `.oradio` as the central runtime contract.
2. Define `.oradio` in terms of telemetry, lens, world state, and expression outputs.
3. Keep playback theme-first.
4. Treat ribbon as the reference surface.
5. Treat transient bubbles as tasteful, simulation-aware evidence surfaces.
6. Convert existing standalone subsystems into Loom-capability blocks instead of preserving them as separate product identities.

---

## 14. Summary

The Loom is the app.
`.oradio` is the artifact.
Everything is telemetry.
Telemetry becomes interpreted meaning.
Meaning drives voice, subtitles, theme mutation, bubbles, and environmental change.

The artifact is not "radio plus visuals."
It is a living simulation expression.
