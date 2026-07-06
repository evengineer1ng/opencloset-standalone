# Plan — make the headline Loom path RUN, then make it SPEAK

Goal: close the gap between "the pipe runs" and "I loomed a world I can listen into."

## What's empirically true right now (verified 2026-06-13 by running it headless)

- The pipe is real: 4-question form → `.oradio` descriptor → `oradio_engine` federation → a running, ticking bus.
- The idea *perturbs dynamics*: same seed (42), different premise+genre → different bus volume (54 vs 138 events). forkuniverse reads *some* of your words.
- The idea does NOT become content: bus headlines are abstract engine events (`thread_opened`, `prediction_settled`, `contract_warning`) — identical in kind across "goosebumps neighborhood" and "corporate boardroom". The genre/semantics are unmodeled. **This is the gap.**
- Bug found by running it: the headline Q1 default (forkuniverse) CRASHES on load — `CreationRequest` requires `time_period`, which `loom_studio.build_loom_descriptor()` never sends. Tests only checked descriptor *shape* + the signals-only (`world_kind="none"`) engine path, so it stayed hidden.

## Step 0 — Reconcile the divergence FIRST (this work is in the wrong repo)

`loom_studio.py`, `loom_player_ui.py`, the `oradio_player.py` dispatch change, and `tests/test_loom_studio.py` were built in the OLD `Radio-OS` repo, not `oracle-radio`. Port them in before anything else:
- `loom_studio.py` → `oracle-radio/loom/loom_studio.py`
- `loom_player_ui.py` → `oracle-radio/` player area (root for now; `club/player/` when that level lands)
- reconcile the `oradio_player.py` descriptor-vs-packaged dispatch with `oracle-radio/oradio_player.py`
- `tests/test_loom_studio.py` → `oracle-radio/tests/`
- fix imports for new locations (`from loom import ...`); run `pytest`; confirm green.
- **Acceptance:** studio imports cleanly in `oracle-radio`; suite green.

## Step 1 — Headline path actually loads + the idea gains specificity

The fix is in `loom_studio.build_loom_descriptor()` (the forkuniverse `creation` block).

1a. **Add the missing required field.** `creation["time_period"] = <value>`; add a Q1 dropdown.
    Check the allowed enum in `plugins/organs/forkuniverse/compiler/models.py` (`CreationRequest`).
    Required set today: `universe_title, premise, setting_kind, time_period, story_mode, world_scale, starting_population, seed_mode`. Only `time_period` is missing.

1b. **Wire the idea-carrying OPTIONAL knobs that are currently unused** (this is where premise gains real specificity):
    - `genre_mix: dict[str,float]`  ← genre multiselect → `{"horror": 1.0}`
    - `tone_mix: dict[str,float]`   ← tone multiselect (dread / tense / comedic…)
    - `location_flavor: str`        ← free text
    - `starting_context: str`       ← free text (the opening scene)
    Confirm exact keys/enums in `models.py`.

1c. **Regression test that EXERCISES THE ENGINE, not just the dict** (the test that would've caught the bug):
    - build a forkuniverse descriptor → `load_oradio(desc)` → tick N → assert bus non-empty, no exception.
    - stronger: two premises/genres at same seed → assert bus differs (volume or content).
    - **Acceptance:** "Open In Player" on default Q1 runs without error; the engine-load test passes.

## Step 2 — Thin narration render: bus → words that sound like the world

Goal: replace raw `thread_opened` with a sentence grounded in *this* world.

2a. **Inspect before assuming.** Dump a full `NormalizedCandidate` (`title, body, tags, source, type, priority`) for a forkuniverse run, and `forkuniverse organ.read_truth()` (digest + active threads/entities). The specifics (entity names, thread titles) may already live in `body`/`tags` — render from those, not `.title`.

2b. **Tier 1 — deterministic templated narrator (ships first, no LLM):**
    - maps (event type + candidate `body`/`tags` + world-truth entities) → a sentence.
      e.g. `thread_opened` → "A new {tag} stirs around {entity}: {body}."
    - a narration surface/lens over the bus; deterministic + unit-testable (same seed → same lines).
    - render target: subtitles lane in the player + feed to TTS (voice provider already in the descriptor).
    - This alone turns `thread_opened` into world-grounded prose and PROVES "listen into."

2c. **Tier 2 — LLM dressing (genre-faithful, club-gated, optional):**
    - pass (structured beat + premise + `genre_mix` + `tone_mix`) to the configured LLM/voice provider for genre prose ("The dummy's painted grin twitched…").
    - reuse Radio OS `broadcast_grammar` / narration (the proven layer; "comes last").
    - gated by club `llm` capability; **falls back to Tier 1 when absent** so it always runs, even offline (keeps the determinism/intake-tape boundary clean).

- **Acceptance:** a goosebumps-premise loom narrates recognizably different lines than a boardroom loom (Tier 1 grounded; Tier 2 genre-dressed); subtitles + TTS speak them in the player.

## Sequencing & definition of done
- Step 0 → 1 → 2, each a tracked commit in `oracle-radio`.
- **North-star demo:** open a "goosebumps" loom → it runs, the organism loops, subtitles speak world-grounded lines, TTS reads them. That's "I loomed a world I can listen into," proven — not asserted.

## Notes
- Keep Tier 1 provider-free so the proof never depends on an LLM being configured.
- The narrator is itself a future OUTPUT/format plugin (per the plugin-outlet model) — build inline first, extract to a plugin later.
- A re-runnable proof of the current state lives at `Radio-OS/_loom_probe.py`.
