# Club Catalog Python Roundup

Status date: `2026-06-18`

This is the first curation pass over our Python surface for future Club-backed brick management.

Goal:
- identify our real Python potential
- separate human-authored code from vendored noise
- spot monoliths to decompose
- spot smaller modules that already look like brick candidates
- prepare for a later composition/decomposition plan

This document is a roundup, not the final migration plan.

## 1. Scope

Repos reviewed:
- `D:\openclaw\opencloset`
- `D:\moco-mac`
- `C:\Users\evana\OneDrive\Documents\Radio-OS`
- `C:\Users\evana\Documents\freqtradebotchallenge`

## 2. Curation Rule

For this roundup, "curated Python" excludes obvious vendor/generated/archive noise such as:
- `venv`
- `.venv`
- `env`
- `radioenv`
- `site-packages`
- `dist-info`
- `__pycache__`
- `node_modules`
- archive/snapshot folders

Important note:
- `opencloset` still contains duplicate trees like `loom-main`, `temp_loom`, and embedded `Radio-OS` copies
- so its curated count is directionally useful, not a final unique-concept count

## 3. Repo Snapshot

### `D:\openclaw\opencloset`

- curated Python files: `827`
- root-level `.py` files: `32`

Important reality:
- this repo is a convergence workspace, not a clean product tree
- it includes Loom work, temporary copies, embedded Radio-OS code, tests, and utility scripts

Most populated curated areas:
- `loom-main\Radio-OS\plugins`
- `loom-main\Radio-OS\plugins\neikos`
- `loom-main\Radio-OS`
- `loom-main\tests`
- `loom-main\oradio_engine`
- root workspace scripts

Top root-level stray files:
- `analyze.py`
- `analyze_trades.py`
- `app.py`
- `extract_trades.py`
- `gather_trades.py`
- `inspect_all_dbs.py`
- `inspect_db.py`
- `inspect_trades.py`
- `loompedal.py`
- `loom_tape_recorder_engine_ready.py`
- `loom_tape_recorder_ui.py`
- `loom_tape_recorder_ui_engine.py`
- `loom_tape_wizard.py`
- `oc.py`
- `oradio_tape_synth_v2.py`
- `pokemon_bridge.py`
- `pokemon_xy_hook.py`
- `pokemon_xy_memory_scan.py`
- `provider.py`
- `radio_os_studio.py`
- `run_api.py`
- `storage.py`
- `window_registry.py`

Largest curated files worth tracking:
- `loom-main\Radio-OS\plugins\ftb_game.py`
- `loom-main\Radio-OS\plugins_disabled\perhapsuse.py`
- `loom-main\plugins\organs\oracle_kingdom.py`
- `loom-main\Radio-OS\plugins\neikos.py`
- `loom-main\Radio-OS\bookmark.py`
- `loom-main\Radio-OS\oradio_runtime.py`
- `loom-main\Radio-OS\audio_cli.py`
- `loom-main\Radio-OS\experiment.py`
- `loom-main\Radio-OS\shell_bookmark.py`

Healthy current Loom-area modules:
- `loom-main\oradio_engine\packet.py`
- `loom-main\oradio_engine\answer_synthesis.py`
- `loom-main\oradio_engine\local_ingress_server.py`
- `loom-main\oradio_engine\ollama_ingress.py`
- `loom-main\loom\loombit_route.py`
- `loom-main\loom\text_loombit.py`

Immediate split pressure inside `loom-main\oradio_engine`:
- `query_codec_impl.py` at `1232` lines
- `visual_tape.py` at `453` lines
- `ingress.py` at `433` lines
- `query_codec.py` at `350` lines
- `visual_thumbnail.py` at `345` lines

This is actually encouraging:
- the engine package already contains many modules near brick scale
- the main split pressure is visible and localized

### `D:\moco-mac`

- curated Python files: `24`
- root-level `.py` files: `1`

Most populated curated areas:
- `src\moco`
- `src\moco\representation`
- `src\moco\recognition`

Largest files:
- `src\moco\ball_state.py` at `1617` lines
- `src\moco\approval_training.py` at `1180` lines
- `src\moco\control_surface.py`
- `src\moco\recognition\arbitration.py`
- `src\moco\representation\train.py`
- `src\moco\recognition\scoring.py`
- `src\moco\terminal_display.py`

Interpretation:
- smaller and cleaner than the other repos
- already clustered around recognizable concept families
- good candidate for decomposition into a neat brick garden

### `C:\Users\evana\OneDrive\Documents\Radio-OS`

- curated Python files: `328`
- root-level `.py` files: `60`

This is still the clearest source tree for the old shell/runtime shape.

Most populated curated areas:
- `plugins`
- `plugins\neikos`
- repo root
- `tests`
- `tools`
- `oradio_engine`

Root-level stray files are numerous and meaningful:
- `audio_cli.py`
- `bookmark.py`
- `broadcast_grammar.py`
- `club_gate.py`
- `context_engine.py`
- `export_oradio.py`
- `kernel.py`
- `loom_player_ui.py`
- `loom_studio.py`
- `oradio_player.py`
- `oradio_runtime.py`
- `provisioning.py`
- `radio_os_studio.py`
- `radio_os_theme.py`
- `ribbon_bridge.py`
- `shell_bookmark.py`
- `signal_heat.py`
- `voice_provider.py`
- `web_server.py`

Largest confirmed monoliths:
- `plugins\ftb_game.py` at `35556` lines
- `plugins\oracle_kingdom.py` at `13310` lines
- `oradio_runtime.py` at `9799` lines
- `bookmark.py` at `9783` lines
- `experiment.py` at `9217` lines
- `audio_cli.py` at `8605` lines
- `shell_bookmark.py` at `7556` lines
- `plugins\neikos.py` at `6955` lines

Interpretation:
- this is the richest migration source for Ribbon OS
- it proves many hard shell/runtime problems were already solved
- it is also the clearest warning against new monolith authorship

Important classification correction:
- `bookmark.py` should no longer be read mainly as a future brick-decomposition target
- it is better understood as simulator lineage
- it is the authoring/runtime environment where `.oradio` stations are inhabited, configured, and exported
- bricks should still be extracted from surrounding logic, but the simulator itself is a higher-order surface, more like a viewport/editor than a concept brick

### `C:\Users\evana\Documents\freqtradebotchallenge`

- curated Python files: `59`
- root-level `.py` files: `7`

Most populated curated areas:
- `wanda\user_data\strategies\backtest`
- `wanda\user_data\strategies\development`
- root
- `wanda\user_data\strategies`
- `cosmo\user_data\strategies`

Top root-level strays:
- `cleo_worker.py`
- `logoutdetector.py`
- `mousemover.py`
- `pokemon_professor_overlay.py`
- `pokemon_x_harness.py`
- `signal_server.py`
- `tiimmyturntup.py`

Largest curated files:
- `wanda\algo_trading_league\main.py` at `22352` lines
- `cleo_worker.py`
- strategy files in `wanda\user_data\strategies`
- `pokemon_x_harness.py`
- `pokemon_professor_overlay.py`

Interpretation:
- one very large app monolith plus many smaller strategy-like concepts
- strategy folders may already behave more like future bricks than the main app does

## 4. Monolith Pressure Map

Highest-pressure files confirmed by line count:

| File | Lines | Why it matters |
|---|---:|---|
| `Radio-OS\plugins\ftb_game.py` | `35556` | largest known monolith; extreme decomposition target |
| `freqtradebotchallenge\wanda\algo_trading_league\main.py` | `22352` | major app monolith in ATL path |
| `Radio-OS\plugins\oracle_kingdom.py` | `13310` | rich world logic, but far beyond brick scale |
| `Radio-OS\oradio_runtime.py` | `9799` | legacy runtime substrate |
| `Radio-OS\bookmark.py` | `9783` | shell substrate, not a brick |
| `Radio-OS\experiment.py` | `9217` | large shell/runtime experimentation surface |
| `Radio-OS\audio_cli.py` | `8605` | large output/control surface |
| `Radio-OS\shell_bookmark.py` | `7556` | shell/UI substrate |
| `Radio-OS\plugins\neikos.py` | `6955` | world/system monolith |
| `opencloset\radio_os_studio.py` | `3080` | already smaller, but still beyond target |
| `moco-mac\src\moco\ball_state.py` | `1617` | candidate for concept splitting |
| `loom-main\oradio_engine\query_codec_impl.py` | `1232` | immediate Loom split target |

## 5. Early Brick Gardens

These areas already look more brick-friendly than others:

### `loom-main\oradio_engine`

Why:
- many modules already near 100-200 LOC
- contracts are becoming explicit
- packets/receipts mindset already fits
- direct relevance to Loom/Ribbon OS future

Best current candidates:
- `packet.py`
- `answer_synthesis.py`
- `local_ingress_server.py`
- `ollama_ingress.py`
- `contract.py`
- `loader.py`
- `club.py`

### `D:\moco-mac\src\moco`

Why:
- concept families are already named clearly
- smaller scope than Radio-OS or ATL monoliths
- easier to carve into interoperable pieces

Likely families:
- `representation`
- `recognition`
- `control_surface`
- `terminal_display`
- `live_floor_trainer`

### strategy surfaces in `freqtradebotchallenge`

Why:
- many strategy files already behave like bounded concepts
- likely easier to wrap than the app shell in `main.py`

Likely families:
- strategy execution
- overlays
- signal serving
- analysis scripts

## 6. Interoperability Law

Most bricks do not interoperate yet.
That is expected.

The point of this curation pass is to track potential, not pretend the contract already exists everywhere.

What needs to become consistent later:
- packet envelope
- manifest shape
- receipts
- hand-in contract
- hand-off contract

## 7. Hand-In / Hand-Off Rule

This should become a coding law.

At the start of every brick:
- declare the hand-in contract
- state what packet/type/context the brick accepts

At the end of every brick:
- declare the hand-off contract
- state what the next brick can rely on

Practical version of the size law:
- `0-300` LOC: core concept
- `300-400` LOC: exit territory

Meaning:
- the last ~100 lines after soft cap should push toward handoff
- exports, next-step expectations, receipts, and packet-out shape should become explicit there

Illustrative shape:

```python
EXPORTS = {
    "provides": ["race_state", "driver_memory"],
    "expects_next": ["event_resolver", "radio_renderer"],
    "packet_out": "RaceTickPacket",
}
```

This is not frozen code yet.
It is the right discipline.

## 8. Immediate Takeaways

1. We already have plenty of Python potential.
2. Radio-OS and ATL contain serious monoliths that should be treated as decomposition sources, not future authoring models.
3. `loom-main\oradio_engine` is the healthiest current place to prove the brick discipline.
4. `moco-mac` looks like a promising cleaner repo for interoperable concept carving.
5. `opencloset` needs catalog discipline because it contains duplicates, temporary trees, and convergence clutter.

## 9. What Comes Next

Next document should not repeat this census.
It should answer:
- what brick families to compose
- what monoliths to decompose first
- what should be ignored or archived
- where interoperability should be attempted first
- what the first explicit hand-in / hand-off contracts should be
