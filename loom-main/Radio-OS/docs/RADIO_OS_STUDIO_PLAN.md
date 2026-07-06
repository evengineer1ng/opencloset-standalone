# Radio OS Studio Plan

This is the implementation companion to `NARRATIVE_WORLD_RUNTIME_VISION.md`.
The vision doc owns product intent. This plan owns the current build map.

## Current Frame

Radio OS is a runtime for persistent narrated worlds.

Studio is not a dashboard builder and not the normal listener/player. Studio is the authoring
environment where a station creator defines:

- antennas and event rules
- host identity, voices, and production behavior
- meaning/spec artifacts
- transient evidence surfaces and decision points
- the `.oradio` package that listeners eventually open

The station runtime remains artifact-first. A listener should be able to receive a `.oradio`
without needing Studio.

## Preservation Rule

The proven surfaces are preserved:

- `bookmark.py` remains the kernel/runtime foundation.
- `shell_bookmark.py` remains the library/vault/player-manager foundation.
- the web server and audio CLI remain load-bearing foundations.

Major product redesigns happen by duplicate-and-continue, not by eroding those files in place.
New surfaces should fork or wrap the proven foundations until the descendant proves itself.

## Current Implementation Inventory

- `radio_os_studio.py` is the native Tkinter Studio surface.
- `plugins/antenna_http.py` profiles HTTP/JSON sources into signatures.
- `plugins/meta/generated.py` turns signatures into editable station meaning specs.
- `radio_os_studio.py` exports `.oradio` packages with:
  - `oradio.json`
  - `manifest.yaml`
  - `requirements.json`
  - `requirements.lock.json`
  - optional `signature.json`
  - optional `meta_plugin_spec.json`
  - referenced plugins
  - bundled voice assets when resolvable
- `provisioning.py` owns machine-level LLM Tune-In / club membership.
- `oradio_resolver.py` owns the `.oradio` readiness ladder:
  bundled asset -> machine cache -> provisioned LLM.

## Phase 1: Authorable Stations

Goal: a station creator can create, profile, author, preview, and export a generic station without
touching code.

Current status:

- Native Studio exists.
- Source profile flow exists.
- Generated/human-editable meaning spec exists.
- Builder simulator exists and uses `bookmark.py` as the kernel.
- LLM Tune-In panel exists.
- `.oradio` export exists with requirements and lockfiles.

Remaining Phase 1 work:

- Keep Studio generic, not ATL-shaped.
- Make export/readiness feedback clearer in Studio.
- Keep simulator framed as builder preview, not the public playback path.
- Start shaping transient surface authoring as station moments, not dashboard pages.

Exit criteria:

- A user can create/edit an ATLFM-style station through Studio without touching code.
- A builder can preview the current draft and hear/inspect real runtime output.
- Phase 1 is not judged by `.oradio` double-click playback.

## Phase 2: Pleasant Authoring

Goal: make the authoring loop comfortable.

Build targets:

- Better source inspection and field mapping.
- Safer spec regenerate/edit/compare.
- Production controls for voices, pacing, mix, and event audio.
- Simulator polish: clearer status, recent segments, fast restart, and audio preview affordances.
- First transient surface designer slice:
  - create template
  - define required fields
  - choose layout
  - preview with sample event data

Non-goals:

- No source-specific dashboard builder.
- No arbitrary AI-generated apps.
- No requirement that authors hand-edit HTML/CSS for common surfaces.

## Phase 3: Portable Stations

Goal: exported `.oradio` artifacts can open and play without Studio.

Build targets:

- Forked `.oradio` player/bootstrapper that consumes `oradio_resolver.py`. First shell exists in `oradio_player_ui.py`, routed by `oradio_player.py --shell`.
- First-run Tune-In gate for missing LLM membership. CLI seam exists in `oradio_player.py --tune-in`.
- Asset resolution from the package, machine cache, or setup flow. Bundled voice refs are package-relative and hash-checked.
- Launch handoff into the kernel/runtime without requiring Studio. First handoff exists via `oradio_player.py`.
- File-association strategy for double-click open. Windows per-user association seam exists via `--install-windows-association` and opens the shell route.

Non-goals:

- Studio does not become the player.
- `shell_bookmark.py` is not required to open one `.oradio`.
- Deterministic text is not a listener fallback for missing LLM.

## Phase 4: Ambient Runtime Fork

Goal: duplicate-and-continue from `bookmark.py` toward the ambient desktop shell described in the
vision doc.

Build targets:

- Forked runtime/player surface, not in-place erosion of `bookmark.py`. First fork exists in `oradio_player_ui.py`.
- Modern player chrome:
  - top toolbar
  - global transport row
  - center transient evidence surfaces
  - bottom subtitles
- Preserve and reuse the manifest-driven theme/wallpaper system.
- Hide authoring workbench controls from listener runtime.
- Promote existing audio controls into one global media row.

Non-goals:

- No permanent dashboard platform.
- No rewrite of working kernel behavior before the fork proves itself.

## Phase 5: Antenna And Surface Ecosystem

Goal: make more worlds narratable.

Build targets:

- File/folder/log antennas.
- Local structured-data antennas.
- Script/process adapters.
- WebSocket/streaming adapters.
- Surface template compatibility rules.
- Component categories: antenna, meta/meaning, widget/tool, transient surface, full station.

## Immediate Next Slice

Make Studio and `.oradio` exports tell the same story as the resolver:

1. Keep `ORADIO_FORMAT.md` aligned with `requirements.json` and `requirements.lock.json`.
2. Surface readiness/resolution feedback in Studio after export.
3. Use `oradio_resolver.py` as the contract for the future forked player.
