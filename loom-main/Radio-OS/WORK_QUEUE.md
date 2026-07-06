# WORK_QUEUE.md

This is the durable task queue for fresh OpenClaw sessions.

## Rules

- Pull only one active task at a time.
- If a task is ambiguous enough to change the implementation, ask on Telegram before proceeding.
- Keep entries short and operational.
- When a task continues across sessions, update `HANDOFF.md` before stopping.

## Inbox

- Add new owner requests here until they are clarified and prioritized.
- The owner-corrected product center now starts at `docs/LOOM_ORADIO_ARCHITECTURE.md`.

## Active

- Loom / `.oradio` unification: turn the owner-corrected architecture in `docs/LOOM_ORADIO_ARCHITECTURE.md` into an executable `.oradio` contract. Immediate focus: telemetry input, interpretation lens, world model, expression outputs, theme-first playback, ribbon mutation/state machine, subtitle lane, transport-feedback semantics, and transient bubble surfaces.

## Parked

- Use this for blocked tasks waiting on owner input, external systems, or approvals.

## Completed

- 2026-06-12: Captured the owner-corrected Loom pivot in `docs/LOOM_ORADIO_ARCHITECTURE.md`. The Loom is now the single app and `.oradio` the single artifact; prior app identities are reframed as capability layers. Updated memory and durable project files to steer future work from the new center.
- 2026-06-11: Runtime theme + launch path + club gate. `radio_os_theme.py` (canonical named-preset themes, monokai default, inherits Library theme if installed). Forked `bookmark.py` -> `oradio_runtime.py` (preserved original) with palette from the theme system + per-station background kept; owner verified monokai on screen. `.oradio` launch path now opens the themed GUI runtime (`oradio_player.py` `RUNTIME_PATH` -> fork; assoc drops `--shell`). Asset club: `provisioning.py` remembers voices/Piper machine-level, `oradio_resolver.py` reuses them for every future station, earnest ask-once CLI + `club_gate.py` Tk gate. `run_runtime.py` helper. Identity captured in `docs/CONVERGENCE.md` section G (we are the container, not the deps; tiny baked artifact; club persistence). Full named suite 68 passed.
- 2026-06-11: Signal Heat engine (`signal_heat.py`): time-decayed per-source heat so airtime is emergent ("hot world speaks, quiet recedes, silence valid"). Wired into `plugins/meta/generated.py` curate + spec gen; Studio Meaning tab gained a Signal Heat panel (per-source overrides via Advanced JSON). 10 new tests; full named suite 62 passed. Vision-vs-codebase diff captured in `docs/CONVERGENCE.md`.
- 2026-06-11: Added Broadcast Grammar v0: runtime-side transition detector, generated meta-profile grammar block, LLM transition prompt handoff, Studio text demo, portable `.oradio` helper bundling, and tests.
- 2026-06-11: Added Who's On The Air? v0 in Studio: show-format cards, cast fields, station-instinct bubbles with strength cycling, custom tag guardrails, station voice compilation, and tests.
- 2026-06-11: Polished Who's On The Air?: secondary show-format flavors now compile into station voice, and custom-tag search suggests related descriptors like Shakespearean/Poetic/Classical before create.
- 2026-06-11: Deepened `.oradio` shell theme inheritance: Studio bundles/repoints global background art, and the forked shell resolves/renders packaged color, gradient, image, GIF, and video-poster/fallback wallpapers.
- Add newest-first one-line completions here with date and result.
