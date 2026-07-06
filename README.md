# OpenCloset

OpenCloset is a desktop-first local harness for Clo. It is separate from PhoneClaw and intentionally avoids hidden session mutation, phantom chat resets, and file-driven runtime state.

## What V1 Does

- Creates and persists chat sessions explicitly
- Persists user turns before model execution
- Talks to a local OpenAI-compatible backend such as `llama.cpp`
- Surfaces provider failures as visible run errors instead of endless loading
- Tracks token pressure visibly and rolls sessions over automatically once the watchdog sees they are past threshold and idle
- Supports optional structured captures without making them the main product

## Run

1. Install dependencies:

```bash
pip install -r opencloset/requirements.txt
```

2. Install UI dependencies:

```bash
cd opencloset/ui
npm install
cd ../..
```

3. Optional environment variables:

```bash
set OPENCLOSET_DB_PATH=d:\openclaw\opencloset\opencloset.db
set OPENCLOSET_API_HOST=127.0.0.1
set OPENCLOSET_API_PORT=5000
set OPENCLOSET_LLAMACPP_URL=http://127.0.0.1:8080
set OPENCLOSET_OLLAMA_URL=http://127.0.0.1:11434
set OPENAI_API_KEY=...
set OPENCLOSET_OPENAI_BASE_URL=https://api.openai.com/v1
set OPENCLOSET_OPENAI_MODEL=gpt-4.1-mini
```

4. Start the local Clo llama.cpp server on the dedicated 5060 Ti GPU:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start-clo-cuda.ps1
```

5. Start the authoritative API backend on `127.0.0.1:5000`:

```bash
python opencloset/run_api.py
```

6. In a second terminal, start the UI dev server on `127.0.0.1:4173`:

```bash
cd opencloset/ui
npm run dev
```

7. Open `http://127.0.0.1:4173`

### Dev Defaults

- The browser UI is served by Vite on `http://127.0.0.1:4173`.
- Vite proxies all `/api` requests to `http://127.0.0.1:5000`.
- `tools/start-clo-cuda.ps1` is the canonical Clo model launcher; it pins `CUDA_VISIBLE_DEVICES=0` so Clo defaults to the RTX 5060 Ti instead of inheriting Buddy's GPU visibility from the current shell.
- The backend runner in `opencloset/run_api.py` uses `OPENCLOSET_API_HOST` and `OPENCLOSET_API_PORT` if you need to override those defaults.
- Runtime channels started with `provider=auto` now prefer the local `llama.cpp` provider/model path for demos before considering remote backends.
- `opencloset/run_api.py` starts the background maintenance, workspace-runtime, and rollover watchdog workers automatically.
- `flask --app api.api.app run` also auto-starts those workers; helper CLIs and tests keep them disabled.
- If the UI reports that the API returned HTML instead of JSON, the usual cause is that the backend is not running on `127.0.0.1:5000` or `VITE_API_PROXY_TARGET` is pointing at the wrong server.

## API

- `GET /api/health`
- `GET /api/health` returns basic runtime readiness, including the active DB path and whether runtime WebSockets are enabled.
- `GET /api/providers`
- `POST /api/providers/test`
- `GET /api/agents`
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/messages`
- `POST /api/sessions/{session_id}/fork`
- `POST /api/sessions/{session_id}/summarize-rollover`
- `GET /api/system/status`
- `GET /api/captures`
- `POST /api/captures`

## Headless Runtime

OpenCloset now exposes a reusable headless runtime layer on top of the existing session and run substrate. Agent channels are long-lived named runtimes that own a backing session, accept structured events, and emit streamable outputs such as assistant messages, decisions, and dashboard patches.

Ambient channels can now self-tick through channel-scoped scheduler jobs. The default `agent_channel_tick` job type is handled by the shared scheduler runner, so background ambient work uses the same persistence and cooldown model as the rest of the runtime.

Domain behavior is no longer hardcoded in the channel manager. `api/api/agent_harnesses.py` provides a registration seam for vertical harnesses to override event classification, prompt shaping, scheduled tick payloads, and structured output adapters. The app registers a built-in `eve` harness, and other verticals can register their own harness objects at startup without modifying the core runtime.

Core routes:

- `POST /api/runtime/agents` creates or resumes a named channel.
- `GET /api/runtime/agents` lists channels.
- `GET /api/runtime/harnesses` lists registered domain harnesses.
- `GET /api/runtime/agents/{name}` returns channel status plus recent events and outputs.
- `GET /api/runtime/agents/{name}/dashboard` returns a harness-built dashboard model for the active domain.
- `POST /api/runtime/agents/{name}/send` submits a user message into the channel.
- `POST /api/runtime/agents/{name}/events` ingests a structured runtime event.
- `GET /api/runtime/agents/{name}/schedule` lists channel scheduler jobs.
- `POST /api/runtime/agents/{name}/schedule` configures the channel self-tick job.
- `GET /api/runtime/agents/{name}/outputs` lists persisted outputs.
- `GET /api/runtime/agents/{name}/stream` subscribes to channel SSE.
- `GET /api/runtime/agents/{name}/ws` mirrors the same channel event feed over WebSocket when `Flask-Sock` is installed.
- `POST /api/runtime/agents/{name}/stop` stops the channel and pauses its backing session.

Example flow:

```bash
python oc.py agent start eveops --mode ambient --domain eve --objective "Watch routes and market deltas"
python oc.py agent schedule eveops --cooldown-seconds 120
python oc.py agent event eveops --type route.changed --text "Niarja route shifted" --payload '{"from":"Jita","to":"Amarr","danger_score":62}'
python oc.py agent send eveops "Summarize the risk and recommend the next move" --sync
python oc.py agent subscribe eveops
```

`oc.py` is a thin CLI over the runtime API. Set `OPENCLOSET_API_BASE` if the API is not running on `http://127.0.0.1:5000`.

## Pokemon Bridge

The Pokemon/Citra bridge sidecar can run in `file`, `window`, `hook`, or `hybrid` mode. `window` captures the live emulator frame, while `hook` or `hybrid` can add semantic state from Citra's native UDP memory scripting API.

Live frame-only example:

```powershell
python opencloset/pokemon_bridge.py --api-base http://127.0.0.1:5000 --channel kalos-live --snapshot-source window --window-title Citra --focus-window --once
```

Semantic hook probe:

```powershell
python opencloset/pokemon_xy_hook.py --profile opencloset/pokemon_xy_hook_profile.sample.json --probe --pretty
```

Omega Ruby semantic hook probe:

```powershell
python opencloset/pokemon_xy_hook.py --profile opencloset/pokemon_omega_ruby_hook_profile.sample.json --probe --pretty
```

Hybrid bridge example:

```powershell
python opencloset/pokemon_bridge.py --api-base http://127.0.0.1:5000 --channel kalos-live --snapshot-source hybrid --window-title Citra --focus-window --hook-command "D:\openclaw\.venv\Scripts\python.exe D:\openclaw\opencloset\pokemon_xy_hook.py --profile D:\openclaw\opencloset\pokemon_xy_hook_profile.sample.json"
```

The bundled sample profile is an address map template for Pokemon X/Y. Fill in the guest-memory addresses for your ROM build before using it for real team, route, battle, or encounter extraction. The hook producer will fail clearly if Citra's UDP scripting endpoint is not live.

The same transport also works for ORAS. The sidecar now infers `Pokemon Omega Ruby` and `Pokemon Alpha Sapphire` from the Citra window title, and `opencloset/pokemon_omega_ruby_hook_profile.sample.json` gives you a starter ORAS profile template to fill with live addresses.

For live address discovery while the game is running, `pokemon_xy_memory_scan.py` can search guest memory for visible strings or diff two captures before and after a movement/input change. Example:

```powershell
python opencloset/pokemon_xy_memory_scan.py --range 0x08000000:0x0807FFFF capture --output before.json
```

Move one tile in-game, then:

```powershell
python opencloset/pokemon_xy_memory_scan.py --range 0x08000000:0x0807FFFF capture --output after.json
python opencloset/pokemon_xy_memory_scan.py diff --before-file before.json --after-file after.json --range 0x08000000:0x0807FFFF --unit 2 --limit 50
```

If the Citra UDP scripting endpoint starts timing out during larger scans, slow the walk down instead of assuming the address space is wrong:

```powershell
python opencloset/pokemon_xy_memory_scan.py --range 0x14000000:0x140FFFFF --delay-ms 25 --retries 4 capture --output before.json
```

## Eval Harness

OpenCloset also includes a product-style end-to-end eval harness for behavioral regression testing.

Examples:

```bash
python oc.py eval run --suite e2e_basic
python oc.py eval run --scenario debug_existing_bug_no_rewrite
python oc.py eval compare --suite e2e_basic
```

The eval harness runs realistic prompts through the real OpenCloset runtime, captures traces, and can optionally run a second evaluator pass for scoring and patch-target suggestions. See `opencloset/evals/README.md` for scenario shape, suite structure, and report outputs.

If you want browser dashboards to consume runtime events without SSE, install the updated requirements and connect to the channel WebSocket route instead of the SSE endpoint. The WebSocket path replays recent channel events from the dedicated stream hub and then fans out live events to each subscriber.

For the built-in `eve` harness, the dashboard endpoint is now structured around the hauling-companion workflow rather than a generic EVE dashboard. Representative event types include:

- `current_run.updated`
- `route.changed`
- `contract.scored`
- `market.opportunity`
- `asset.snapshot`
- `wallet.snapshot`
- `session.plan`
- `decision.record`
- `knowledge.gap`
- `doctrine.updated`
- `training.goal`

Those events roll up into dashboard panels for current run, route risk, contracts, market opportunities, assets, wallet/profit, session companion, decision timeline, knowledge gaps, training plan, and personal doctrine. The `eve` harness also keeps the top-level dashboard answers to four operational questions: what are we doing, why are we doing it, what should we watch out for, and what should we do next.

## Notes

- SQLite is the source of truth for live runtime state.
- JSONL transcript export is append-only and for audit/debug only.
- V1 ships with one first-class agent: `Clo`.
- `PhoneClaw` can become a client of this harness later without sharing internals.
