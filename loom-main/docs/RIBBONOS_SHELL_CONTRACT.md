# RibbonOS Shell Contract

Status date: `2026-06-19`

This document freezes the intended shape of the new RibbonOS shell.

It exists to stop a recurring confusion:
- the launcher shell is not the same thing as the simulator
- the simulator is not the same thing as an exported `.oradio`
- `RadioOS.oradio` is not a port of the old shell

This is the shell build target.

## 1. Core Statement

RibbonOS is the merged launcher shell built from:
- `shell_bookmark.py` lineage from Radio OS
- the Godot `ribbon-os` shell lineage

The shell merge is specifically about:
- launcher behavior
- station browsing
- shell orchestration
- theme state machine
- ribbon-first presentation
- OS-adjacent shell feel

It is not a wholesale port of all Radio OS code.

## 2. The Three Layers

Keep these layers separate.

### A. RibbonOS shell

This is the outer host.

Responsibilities:
- boot sequence
- fullscreen ribbon stage
- carousel overlay
- station/world browser
- launch surfaces for stations and tools
- shell settings
- Club entry and provisioning surfaces
- Audio CLI entry surface
- OpenCloset harness entry surface
- server/process/status surfaces

Short version:

`RibbonOS shell = launcher + ribbon presence layer + shell orchestration`

### B. Simulator

This is the `bookmark.py` lineage.

It is launched from the shell when the user wants to build or edit a station.

Responsibilities:
- inhabit a station-in-progress
- drag in bricks
- configure windows and surfaces
- test TTS and other runtime behavior
- shape theme/layout identity
- inspect station composition
- export or write `.oradio`

Short version:

`simulator = authoring environment`

It is not the shell.
It is not a brick.

### C. `.oradio`

This is the exported runtime artifact.

Responsibilities:
- package a world/federation/station experience
- run the authored simulation
- expose the authored theme, voice, surfaces, and interactions
- remain small and shareable

Short version:

`.oradio = deployable unit of simulation`

It is not the launcher shell.

## 3. What Comes From `shell_bookmark.py`

The old Radio OS shell contributes the practical launcher substrate.

Important inherited capabilities:
- station browser / card launcher
- station discovery
- station launch and process management
- desktop / web / headless launch patterns
- persistent settings/config
- theme selection and configuration storage
- server toggle / server launch behavior
- Audio CLI integration
- station management/admin surfaces

This is why `shell_bookmark.py` matters:
- it already solves many operational shell problems
- it already behaves like a launcher/admin shell

What should not be carried forward unchanged:
- the old station wizard as the primary authoring model
- the old visual assumptions when they conflict with ribbon-first shell behavior

## 4. What Comes From Godot Ribbon

The Godot `ribbon-os` shell contributes the aesthetic and stateful shell brain.

Important inherited capabilities:
- ribbon-first fullscreen stage
- shell boot/splash flow
- explicit state machine
- category entry / loop / exit transitions
- overlay carousel model
- transition timing and crossfade grammar
- search and category overlay feel
- clock / tray / system-feel touches
- OS-adjacent visual behavior worth preserving where practical

This is why the Godot shell matters:
- it has the best current shell aesthetic
- it proves the state-machine side of the shell
- it gives RibbonOS a strong visual identity

## 5. The Real Merge

The new shell is not:
- Radio OS with prettier graphics
- Godot shell with station cards glued on

The real merge is:

### Godot side provides
- ribbon stage
- boot flow
- shell state machine
- overlay logic
- transition language
- shell atmosphere

### `shell_bookmark.py` side provides
- station launcher logic
- station metadata/card model
- settings/config plumbing
- server/process orchestration
- Audio CLI orchestration
- practical entry points into tools and runtimes

### Result

`RibbonOS shell = ribbon-first launcher host with real station and tool orchestration`

## 6. Ribbon-first Law

The ribbon is the main fullscreen stage.

The carousel is an overlay.

Rules:
- the ribbon may be covered temporarily by the carousel
- the shell should not reserve permanent dead space just to avoid overlap
- overlay controls should fade on inactivity
- activity should bring the shell controls back

This comes from the newer corrected direction, not the older fear of covering the ribbon.

## 7. Shell Responsibilities

These belong to the RibbonOS shell itself.

### Launch and browse
- discover available stations / worlds / `.oradio` artifacts
- browse them as cards or categories
- launch them
- resume recent ones

### Theme and shell state
- own the ribbon stage
- own shell-wide theme state
- own boot and transition state
- own shell overlay visibility / inactivity fade

### Tool entry points
- launch simulator
- launch OpenCloset harness surfaces
- launch Audio CLI surfaces
- launch Club/config/provisioning surfaces

### Orchestration
- manage child processes or runtimes
- surface status
- preserve shell-level settings

### Packaging entry
- allow opening a standalone `.oradio`
- let Club provision what is needed
- then enter the authored runtime cleanly

## 8. Simulator Responsibilities

These do not belong in the outer shell.

- detailed station assembly
- brick composition
- plugin/meta-plugin shaping
- world/runtime testing
- station theme authoring
- `.oradio` export/write

This keeps the shell clean.

## 9. `.oradio` Responsibilities

These do not belong in the outer shell either.

- embody the authored world
- speak with its own voice
- express its own theme
- run its own runtime surfaces
- expose its own dial/station logic if authored that way

An `.oradio` can be rich.
It should not be forced to carry general shell administration concerns.

## 10. Important Clarification About `RadioOS.oradio`

`RadioOS.oradio` is not the old shell repackaged.

It is:
- an authored station/world/meta-world experience
- something launched by RibbonOS shell or directly by double-click with Club provisioning
- a way to deliver the Radio OS feeling as an artifact

What it may contain:
- a dial-like station experience
- multiple contained station behaviors
- deterministic synthesis
- radio texture / crackle / transitions
- the upgraded authored experience of "Radio OS"

What it does not need to contain:
- the launcher shell itself
- the old shell administration layer

This distinction must remain explicit.

## 11. First Build Target

The first real RibbonOS shell should do these things well:

1. Show a ribbon-first shell stage, opening safely windowed by default unless the user explicitly asks for fullscreen.
2. Show a carousel/browser overlay.
3. Fade the overlay after inactivity.
4. Browse launchable stations/tools.
5. Launch the simulator.
6. Launch a station or `.oradio`.
7. Expose shell-level theme/settings.
8. Leave room for Audio CLI, OpenCloset, and Club surfaces.

If it does those eight things, it is already the right shell.

## 11.1 UX Guardrails

These are now explicit because the first rough Python shell drifted away from them.

- do not seize the whole viewport by default
- do not depend on stock scrollbars as the primary carousel interaction
- do not let generic toolkit controls dominate the shell aesthetic
- the ribbon should remain visually important even while the overlay is visible
- fullscreen can exist as an explicit mode, but not as an ambush

Short version:

`safe launch first, ribbon first, carousel second, toolkit chrome last`

## 12. Non-goals

Do not confuse the first shell milestone with:
- finishing deterministic retrieval
- finishing the full simulator decomposition
- solving all Club provisioning
- building the final `RadioOS.oradio`

Those are adjacent.
They are not required to define the shell correctly.

## 13. Build Rule

When implementing the shell:

- merge launcher responsibilities from `shell_bookmark.py`
- merge theme/state behavior from Godot Ribbon
- do not flatten simulator logic into the shell
- do not flatten shell logic into `.oradio`
- do not treat large legacy runtime files as the new unit of authorship

Short version:

`build the launcher shell first, keep the simulator separate, keep the artifact separate`
