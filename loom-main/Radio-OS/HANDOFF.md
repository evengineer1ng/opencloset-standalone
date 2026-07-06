# HANDOFF.md

This file is the canonical cross-session resume packet.

Update it whenever:

- a run stops with unfinished work
- context is getting tight
- a blocking question must be sent to the owner
- the next isolated session should be able to continue without chat history

## Current Run

- Status: active
- Repo: `C:\Users\evana\OneDrive\Documents\Radio-OS`
- Branch:
- Task: Loom / `.oradio` product-boundary pivot. Next: turn the new architecture into an executable `.oradio` contract.
- Owner approval needed: no

## What Was Done

- 2026-06-12: Owner-corrected the product boundary. The Loom is now the single app and `.oradio` the single standalone artifact. Radio OS, ForkUniverse, ATL, Oracle Kingdom, FTB, and related systems are no longer separate product identities; they are capability layers. Added `docs/LOOM_ORADIO_ARCHITECTURE.md` to capture the new center: `.oradio` as telemetry interpreter + world model + expressive surface, theme-first playback, ribbon as the reference visual surface, transient bubbles instead of classic windows, and all feeds/sensors/controls treated as telemetry interpreted through a simulation lens. Updated memory and durable project files to steer future work from this new center.
- 2026-06-11: Wrote `docs/CONVERGENCE.md` (durable vision-vs-codebase diff: what's built, the gap map, locked decisions, sequencing). Built the Signal Heat engine (`signal_heat.py`) - time-decayed per-source heat making airtime emergent - wired into `plugins/meta/generated.py` (curate + spec gen) and surfaced a Signal Heat panel in the Studio Meaning tab; per-source overrides via Advanced JSON. Added `tests/test_signal_heat.py` (10 tests including an integration test); installed pytest into `radioenv`; full named suite 62 passed. Locked decision (owner correction): `bookmark.py`'s frontend is not disposable - the runtime fork must modernize it, not headless-wrap it (`oradio_player_ui.py` currently does the wrong thing). No preserved file edited.
- Created `radio_os_studio.py` as the native Studio fork/redesign, separate from the old launcher and web shell.
- Added `radio_os_studio.bat` as a Windows launcher.
- Reworked Studio around generic station authoring: source editing, signature profiling, generated spec editing, production basics, builder simulation, and `.oradio` export.
- Removed the wrong product framing where Studio acted like an ATL loader or normal listener/player.
- Added `docs/NARRATIVE_WORLD_RUNTIME_VISION.md` as product intent, with Radio OS framed as persistent narrated worlds.
- Added `provisioning.py` for machine-level LLM Tune-In / club membership.
- Added `oradio_resolver.py` for `.oradio` readiness: bundled assets -> machine cache -> provisioned LLM.
- Added `docs/RADIO_OS_STUDIO_PLAN.md` as the implementation companion to the vision doc.
- Updated `docs/ORADIO_FORMAT.md` to match current exports: `requirements.json`, `requirements.lock.json`, and bundled voice assets.
- Updated Studio's `.oradio` tab so package preview includes descriptor, declared requirements, bundled assets, and lock/resolution preview.
- Added `tests/test_oradio_contract.py` to lock Studio export and resolver expectations together.
- Added Studio "Check Readiness" action: it packages the current draft to a temporary `.oradio`, runs `oradio_resolver`, and shows ready/blocking Tune-In/cache status in the `.oradio` tab.
- Added `oradio_player.py`, the first standalone `.oradio` bootstrapper: extracts a package, runs the resolver, refuses fake radio when Tune-In/cache items block readiness, and launches `bookmark.py` without Studio or `shell_bookmark.py`.
- Added player-side Phase 3 seams: `--tune-in`, `--print-windows-association`, and `--install-windows-association`.
- Fixed package portability for bundled voices: exported `manifest.yaml` now rewrites bundled voice refs to package-relative `assets/voices/...` paths instead of author-machine absolute paths.
- Hardened `oradio_resolver.py` so bundled voice files are checked against `requirements.lock.json` size/SHA-256 before launch.
- Added `oradio_player_ui.py`, the first forked listener shell: charcoal/cyan chrome, top toolbar, media row, center transient surfaces, surface palette, bottom HOST subtitle row, Tune-In gate, headless `bookmark.py` launch, and audio-pipe watcher.
- Routed `oradio_player.py --shell <station.oradio>` to the new shell; Windows association now opens the shell route.
- Added basic manifest theme inheritance for the shell from `art.global_bg`, `art.panels.toolbar`, `art.panels.subtitle`, and `art.accent`.
- Made runtime log, audio pipe, and signal-monitor surfaces live-updating from kernel/audio events in `oradio_player_ui.py`.
- Replaced transport placeholders with honest controls where the preserved kernel seam supports them: Play, Stop, Restart, Open Audio Pipe, and Spawn Surface. Pause/Rewind/Forward are disabled until the kernel exposes external commands.
- Deepened manifest/theme inheritance for the forked shell: Studio now bundles global background image/video art into `assets/art/...`, rewrites exported `manifest.yaml` to package-relative art paths, and `oradio_player_ui.py` resolves/renders packaged color, gradient, image, GIF, and video-poster/fallback wallpapers in the center stage.
- Added Broadcast Grammar v0: `broadcast_grammar.py` decides transition reasons from show-state memory, generated meta-plugin specs now include `broadcast_grammar`, `plugins/meta/generated.py` passes structured transition requests to LLM prompts or deterministic fallback wording, Studio has a text transition demo, and `.oradio` export bundles the helper for generated-meta stations.
- Added `docs/BROADCAST_GRAMMAR.md` as the durable concept/implementation note.
- Added Who's On The Air? v0 in Studio: the normal-facing station voice crafter now has show-format cards, On-Air Talent/Cast fields, station-instinct tag bubbles with strength cycling, custom tag creation/guardrails, Try This Voice, Save Station Voice, and Advanced Details for the compiled runtime artifact.
- Polished Who's On The Air?: optional secondary show-format flavors now round-trip and compile into the station voice, and custom-tag search now suggests related descriptors before creating a user tag.
- Added `docs/WHO_ON_THE_AIR.md` as the durable UX/data-contract note.

## Current State

- Files touched: `broadcast_grammar.py`, `plugins/meta/generated.py`, `radio_os_studio.py`, `radio_os_studio.bat`, `provisioning.py`, `oradio_resolver.py`, `oradio_player.py`, `oradio_player_ui.py`, `docs/BROADCAST_GRAMMAR.md`, `docs/WHO_ON_THE_AIR.md`, `docs/NARRATIVE_WORLD_RUNTIME_VISION.md`, `docs/RADIO_OS_STUDIO_PLAN.md`, `docs/RADIO_OS_STUDIO_ROADMAP.md`, `docs/ORADIO_FORMAT.md`, `docs/HANDOFF_runtime_fork_and_metaplugin.md`, `docs/LOOM_ORADIO_ARCHITECTURE.md`, `tests/test_broadcast_grammar.py`, `tests/test_oradio_contract.py`, `WORK_QUEUE.md`, `HANDOFF.md`, `DECISIONS.md`.
- Validation run: earlier named suites passed for the existing `.oradio`/broadcast work; this 2026-06-12 run was documentation/admin-file work only and did not require tests.
- Key observations: older Radio OS and station-era docs still contain useful implementation seams, but the owner-corrected product center is now The Loom and the `.oradio` artifact. Future architecture decisions should start there, then reconcile older docs as needed.
- Before resuming, read `docs/LOOM_ORADIO_ARCHITECTURE.md` first. Older docs like `docs/CONVERGENCE.md`, `docs/NARRATIVE_WORLD_RUNTIME_VISION.md`, and `docs/ORADIO_FORMAT.md` still contain useful implementation seams, but their product framing must be reconciled against the new Loom-era boundary before decisions are made.

## Open Questions

- None.

## Next Actions

1. Read `IDENTITY.md`, `SOUL.md`, `USER.md`, `AGENTS.md`, `WORK_QUEUE.md`, `DECISIONS.md`, `MEMORY.md`, and this file.
2. Confirm the active repo and branch before editing.
3. Read `docs/LOOM_ORADIO_ARCHITECTURE.md` and treat it as the owner-corrected product center.
4. Next implementation target: define the executable `.oradio` contract for telemetry input, interpretation lens, world state, ribbon/theme mutation, subtitle lane, transport-feedback, and transient bubble surfaces.
5. Resume only the top active task or ask the owner if the queue is ambiguous.

## Rollover Checklist

- Safe stopping point reached
- `WORK_QUEUE.md` updated
- `DECISIONS.md` updated if a durable decision was made
- `MEMORY.md` updated if a stable fact or pitfall was learned
- Owner notified on Telegram if there was meaningful progress, risk, or uncertainty
