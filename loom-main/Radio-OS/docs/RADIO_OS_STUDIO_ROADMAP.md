# Radio OS Studio Roadmap

## Status

- Loop v0 is proven: antenna + generated meaning can turn a live source into narrated station material.
- Phase 1 is current: make stations authorable in the native Tkinter Studio.
- ATL/ATLFM remains a frozen benchmark/example, not the Studio product shape.
- Studio builds station projects and `.oradio` artifacts.
- Studio includes a builder simulator for author feedback, like play-in-editor inside a game engine.
- `bookmark.py` is the `.oradio` playback kernel.
- `shell_bookmark.py` is the library/player manager, like iTunes for `.oradio` files.
- `.oradio` double-click playback is deferred until the portability phase.

## Phase 1: Make Stations Authorable

**Objective**

Create and edit a generic live station through Studio without touching code.

**Concrete deliverables**

- Native Studio app in `radio_os_studio.py`
- Generic station identity, source, meaning, and production panels
- HTTP/JSON antenna profiling through `plugins/antenna_http.py`
- Editable `signature.json` and `meta_plugin_spec.json` artifacts
- Builder simulator tab that saves the draft and previews it through `bookmark.py`
- Draft `.oradio` export package containing `oradio.json`, `manifest.yaml`, station artifacts, and referenced plugin code
- Format notes in `docs/ORADIO_FORMAT.md`
- No ATL defaults and no dependency on `shell_bookmark.py`

**Dependencies on prior phases**

- Existing station manifest contract
- Existing antenna plugin
- Existing generated meta-plugin
- Existing `bookmark.py` runtime contract

**Explicit non-goals**

- Making Studio the normal listener/player experience
- Requiring Studio to open a `.oradio`
- Solving double-click `.oradio` playback
- Treating ATL as the default source
- Replacing `bookmark.py` as the playback kernel
- Replacing `shell_bookmark.py` as the library/player manager

**Exit criteria**

- A user can create/edit an ATLFM-style station through Studio without touching code.
- A builder can preview the current draft in the Studio simulator and hear/inspect real runtime output.
- Phase 1 is not judged by whether `.oradio` double-click playback exists.

## Phase 2: Make Authoring Pleasant

**Objective**

Turn the working builder into a comfortable production environment.

**Concrete deliverables**

- Better source inspection and field mapping UI
- Safer spec regenerate/edit/compare flow
- Production controls for voices, flow, pacing, mix, and event audio
- Artifact validation panel with fix-forward guidance
- Project templates that are generic first, with ATLFM only as one example
- Simulator polish: clearer status, recent segments, audio preview affordances, and fast restart

**Dependencies on prior phases**

- Working Studio authoring loop
- Draft simulator that exercises the real `bookmark.py` kernel
- Stable source/signature/spec artifact loop

**Explicit non-goals**

- Introducing source-specific dashboards into Studio
- Folding source application logic into Radio OS
- Making pleasant authoring depend on `.oradio` portability work

**Exit criteria**

- Authors can iterate on station meaning and production without touching JSON directly unless they choose to.

## Phase 3: Make Stations Portable

**Objective**

Make exported `.oradio` artifacts open and play without requiring Studio.

**Concrete deliverables**

- Define the `.oradio` runtime contract around `bookmark.py`
- Add a standalone `.oradio` player/bootstrapper
- Decide how double-click works on Windows/macOS/Linux: file association, bundled player, or self-contained artifact strategy
- Resolve embedded assets: plugins, voices, local model references, keys, and package-relative paths
- Add validation for portability warnings before export
- Keep `shell_bookmark.py` as the optional library/player manager, not a requirement

**Dependencies on prior phases**

- Stable `.oradio` package layout
- Clear boundary between Studio, kernel, and library/player manager
- Authoring flow mature enough that portable artifacts are worth shipping

**Explicit non-goals**

- Requiring `shell_bookmark.py` for artifact playback
- Making Studio the player
- Hiding portability warnings from authors

**Exit criteria**

- A `.oradio` exported from Studio can be opened by the playback layer and starts producing station audio without Studio running.

## Phase 4: Expand Antennas

**Objective**

Let `.oradio` stations listen to more kinds of complex systems.

**Concrete deliverables**

- File/folder/log antennas
- Local structured-data antennas
- Script/process adapters
- Shared signature-profile expectations across source types
- Importable samples so builders can test station ideas without a live backend

**Dependencies on prior phases**

- Stable source/signature/spec artifact loop
- Pleasant enough authoring flow to make new antennas usable

**Explicit non-goals**

- One-off source hacks that bypass the artifact loop
- Turning Studio into a dashboard for any one source system

**Exit criteria**

- At least one non-HTTP system can become a `.oradio` station through the same builder flow.

## Phase 5: Separate Stations, Tools, And Widgets

**Objective**

Formalize the ecosystem so station-sized plugins, antennas, widgets, and kernel tools have clear roles.

**Concrete deliverables**

- Plugin classification: kernel tool, antenna, widget, source adapter, or full station experience
- Station assembly manifest rules for what gets embedded in `.oradio`
- Widget/tool catalog for station builders
- Compatibility checks for imports, exports, and package-relative assets
- Migration notes for legacy plugin assumptions

**Dependencies on prior phases**

- Stable `.oradio` artifact format
- Multiple source types
- Enough examples to see real ecosystem boundaries

**Explicit non-goals**

- Flattening everything into the old "plugin" mental model
- Breaking existing stations without a migration path

**Exit criteria**

- Builders can tell whether a component is a source, a tool, a widget, or a station, and Studio can package it accordingly.

## Phase 6: Assisted Builder

**Objective**

Make station creation faster with transparent, human-editable assistance inspired by Open Closet-style guided building.

**Concrete deliverables**

- Assisted antenna setup
- Assisted spec drafting and critique
- Suggested station templates from observed source signatures
- Guided repair when profiling/spec/runtime validation fails
- Import/export compatibility explanations in builder language

**Dependencies on prior phases**

- Stable authoring artifacts
- Mature simulator feedback
- Formal component categories

**Explicit non-goals**

- Opaque codegen
- Replacing human-editable station artifacts
- Turning Studio into a source-system-specific dashboard

**Exit criteria**

- A user can build or refine a station faster with assistance, while every durable decision remains visible and editable.

## Cross-Phase Guard Rails

- Radio OS Studio = antenna + meta-plugin + production authoring.
- ATL/LCE = source system, benchmark, or example.
- `.oradio` = deferred portable runtime artifact until Phase 3.
- Studio may simulate drafts for builders; it must not become the required listener/player path.
- `bookmark.py` remains the kernel.
- `shell_bookmark.py` remains the optional library/player manager.
- Generated interpretation must stay transparent and editable.

## Current Chosen Sequence

1. Phase 1: Make stations authorable.
2. Phase 2: Make authoring pleasant.
3. Phase 3: Make stations portable.
4. Phase 4+: Make the ecosystem bigger.

## Next Up After Phase 1

Polish the Studio authoring experience around the source/signature/spec/simulator loop before tackling `.oradio` double-click portability.
