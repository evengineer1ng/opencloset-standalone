# Organ Catalog — the bodies we're merging into one

> Owner-directed audit, 2026-06-12. The plan (locked with the owner this session): **do not rewrite**
> the simulation stack. Merge the existing monolithic backends into **one body** under a **central
> engine** (which lives in the **club**, not inside any `.oradio`), with a **thin shim per backend** so
> each becomes an *organ*. Merge depth = **FEDERATION**: every organ keeps its own world/state and runs
> sovereign; the engine gives them a shared clock + event bus + telemetry contract and narrates them as
> one. This doc is the meticulous record of *what each organ simulates*, *its test/quality bar*, and
> *its natural shim seam*.
>
> Treat the other architecture docs as fuzzy guides; the owner is the source of truth (see
> `docs/LOOM_ORADIO_ARCHITECTURE.md`, `docs/CONVERGENCE.md`, `docs/PROJECT_CONSTELLATION.md`). Notably
> those docs argue *against* a merge — the owner has overridden that: we are merging, federated.

---

## 0. The body / organ model

```
                THE CLUB  (machine-level, configure-once)
                +--------------------------------------------+
                |   CENTRAL ENGINE (the .oradio runtime)     |
                |   shared clock · event bus · telemetry     |
                |   contract · world-model registry · voice  |
                +----+----------+----------+---------+-------+
                     | shim     | shim     | shim    | shim
        +------------+--+   +---+------+  +-+--------+  +-----------+
        | ForkUniverse |   |  Oracle  |  | Neikos   |  |   FTB     |  ... (pull / sovereign worlds)
        +--------------+   +----------+  +----------+  +-----------+
        +--------------+   +----------+  +----------+
        | ATL / League |   |  MoCo    |  |OpenCloset|              ... (push / live / meta)
        +--------------+   +----------+  +----------+
```

An `.oradio` ships a **tiny reference** to which organs it observes + how to interpret them; the central
engine in the club instantiates the organs and runs the federation. No organ is bundled into the file.

---

## 1. The shared shim contract (already discovered, not invented)

The key finding: **the mature sims already converged on one shape.** `forkuniverse/engine/world_core.py`
documents it verbatim — every sim independently grew: forked-namespace deterministic RNG, a causal
ledger, a true tick seam, absence reconstruction, an operator-input seam, and a hot-layer export. That
convergence *is* the federation interface. A shim adapts an organ to these five verbs:

| Verb | Meaning | Source shape |
|---|---|---|
| `advance(dt) -> events` | move the world forward; for pull organs, `compute_absence(elapsed) -> owed ticks` | pull |
| `observe() -> candidates` | emit the **locked normalized candidate** `{post_id, source, title, body, priority, ts, type, tags}` (the repo's existing antenna contract) | push / live |
| `read_truth() -> snapshot` | hot-layer export of current world state for narration/visuals | all |
| `apply_input(event)` | accept external telemetry / operator action / cross-organ ripple (the `apply_operator_input` seam) | all |
| `identity()` | `seed` + determinism class (`deterministic` \| `live`) — decides which transport actions are legal | all |

**Determinism law (from world_core):** every stochastic decision is keyed by
`(canonical_seed, namespace, tick)` — so advancing one tick at a time == reconstructing N ticks in a
batch. This is what makes World Continuity (`elapsed -> owed ticks`) free for the deterministic organs.

**Two antenna shapes (already in the repo's model):** *pull* organs (ForkUniverse, Oracle, Neikos, FTB)
the engine *calls*; *push/live* organs (ATL/League, MoCo) *emit* and the engine observes.

---

## 2. The organs

### 2.1 ForkUniverse  —  *generate* (pull, deterministic)
- **Location:** `forkuniverse/` (this repo) — `compiler/ · engine/ · ontology/ · runtime/`.
- **Simulates:** a deterministic, on-demand "universe calculator" — a compiled world package advanced by
  pure function of (seed, prior state). More calculator than daemon; idle = zero RAM.
- **Seam (real symbols):** `engine/world_core.py` → `UniverseState.advance_tick`, `.simulate_epoch`,
  `.compute_absence` (the pull/absence seam), `.apply_operator_input` (decree ripple); `runtime/query.py`;
  existing bridge `plugins/meta/forkuniverse_meta.py` (holds a handle + narrates — the model shim).
- **Determinism:** strong — `SeededRNG.stream(namespace, tick)`, `CausalLedger`.
- **Tests:** `tests/test_forkuniverse_{engine,compiler,ontology,bridge}.py` → **33/33 pass**.
- **Shim work:** smallest of all — it already exposes the canonical contract. Wire `compute_absence` to
  true wall-clock elapsed (open item noted in CONVERGENCE §H).

### 2.2 Oracle Kingdom  —  *decree → ripple* (pull, deterministic)
- **Location:** `plugins/oracle_kingdom.py` (13.3k lines) + `plugins/oracle_court*.py`,
  `oracle_kingdom_web/`, `oracle_kingdom_tk.py`; sim drivers in `tools/oracle_sim.py`,
  `oracle_court_sim.py`, `oracle_geosim.py`.
- **Simulates:** belief-centered socio-political kingdom. The Oracle emits symbolic decrees that cascade
  through layered simulation; effects opaque at the surface, fully traceable in a **CausalLedger**.
  Cold layer = pure math; Hot layer = LLM presentation only. 6 phases incl. multi-kingdom, scar tissue,
  era identity, state-coherence collapse/recovery.
- **Seam:** controller + plugin contract; decree application = `apply_input`; Cold/Hot layer split =
  `read_truth`; lazy on-demand history = `compute_absence` analog.
- **Determinism:** strong — `(seed, Oracle build, choices, real-time gaps)`.
- **Tests:** `tools/oracle_sim.py` runs deterministic, **637 ticks/sec, 4 eras → distinct outcomes**,
  "Oracle personality drives meaningfully different outcomes." (Owner cites this as the crystallization
  moment.) **No `pytest` wrapper yet — promote the sim script to an asserting test in the merge.**
- **Risk:** large, GUI-coupled (tkinter + web). Shim must drive the headless controller, not the UI.

### 2.3 Neikos: Hundred Islands  —  *seed-world + ecology* (pull, deterministic)
- **Location:** `plugins/neikos.py` (6955 lines). **NOTE: was deleted from the working tree this session
  (accidental — live `web_server.py` imports it); restored from git `4f96e24`. Confirm the restore is
  intended.**
- **Simulates:** a fully seed-derived island world — topology, 300 species, encounter tables, 3v3 battle,
  genetic breeding, factions, outcome bands, gates, hidden-knower mystery, NGP+ behavioral persistence,
  memory-echo, fragment discovery.
- **Seam:** `NKController` driven by a command queue (`nk_cmd_q`) emitting UI events (`nk_ui_q`) — already
  a clean `apply_input`/`observe` seam; `{"action":"advance","ticks":N}` is the tick seam; `get_state` =
  `read_truth`.
- **Determinism:** strong — `SeededRNG(seed).fork(namespace)`, `IslandLedger`.
- **Tests:** `tests/test_neikos_sim.py` (standalone walkthrough, §1–§25) → **132/132 pass** (force
  UTF-8 on Windows: `PYTHONIOENCODING=utf-8`). Shim: `oradio_engine/shims/neikos_shim.py`,
  `tests/test_neikos_shim.py` (6 pass, incl. the first 2-organ federation with ForkUniverse).
- **FINDING (shim, 2026-06-12): Neikos is an INTERACTIVE organ, not an ambient one.** Ticked passively
  (no player commands) its bus output is near seed-invariant — different seeds emit the same TIER-escalation
  beats; emergence lives in world *content* (island/species/knower) and in response to `apply_input`
  (move/encounter/explore/battle), not in idle ticking. Implication for authoring: a Neikos-backed
  `.oradio` should drive commands (or treat the player/another organ as its input source), or it reads as
  a quiet world. Contrast ForkUniverse, whose passive tick stream is richly emergent on its own.

### 2.4 From the Backmarker (FTB)  —  *competition + economy* (pull, deterministic)
- **Location:** `plugins/ftb_game.py` (35.5k lines, the largest organ) + `plugins/ftb_names.py`.
- **Simulates:** racing-management sim — entity stat models (Driver ~26 stats, Engineer ~24, …), event
  system, economy (money is the only constraint), job board. "ZenGM-style depth"; **pure math, NO LLM.**
- **Seam:** the **feed worker** already converts sim events → audio candidates (a ready-made `observe`
  seam); the sim engine is the tick seam; entity state = `read_truth`.
- **Determinism:** math-pure (`random` seeded). Verify a canonical-seed entry point for the contract.
- **Tests:** no dedicated suite found in `tests/`; covered indirectly via station/plugin contracts.
  Shim: `oradio_engine/shims/ftb_shim.py`, `tests/test_ftb_shim.py` (3 pass, 1 **xfail** — see findings).
- **FINDING A — determinism LEAK (2026-06-12).** FTB claims "seed for deterministic replay" but two
  same-seed runs diverge in event *volume*. Causes: (1) unseeded global `random.*` in `ftb_game.py`
  (`random.shuffle(ai_teams)` ~4660, `random.random()` poaching ~4698/4801) bypass `state.get_rng`;
  (2) wall-clock live play-by-play (`_live_pbp_start_ts = time.time()` ~9267, 2s/event) ⇒ pbp volume
  depends on real elapsed time. Fix (deferred to engine-iterate): route those paths through
  `state.get_rng`; gate or tick-pace the live pbp instead of wall-clock. *The federation harness caught
  this — exactly the cross-organ purity bar working.*
- **FINDING B — volume FLOOD (2026-06-12).** Ambient FTB emits ~11k pbp micro-beats per clock tick
  (mostly empty-bodied `audio`/`outcome` race frames); in a 4-organ demo grid produced 66,931 of 66,979
  bus beats, swamping the other worlds. The shim must observe *world truth* (race results, contracts,
  financials, retirements), not every pbp frame — add a granularity filter at the shim (drop the pbp
  micro-stream), or aggregate a race weekend into one result beat. Deferred to iterate.

### 2.5 ATL / League: Community Edition  —  *grade & evolve* (PUSH, live server)
- **Location:** separate repo `C:\Users\evana\Documents\freqtradebotchallenge\wanda\`
  (`algo_trading_league/main.py`, ~18k-line FastAPI monolith) + per-team Freqtrade containers; consumer
  station here = `ATLFM`. Genome scoring = `scripts/pliers/`.
- **Simulates:** a research league — orchestrates live competitors, imports their trade DBs into
  `league.sqlite`, decomposes each into graded **genomes** (Entry/Exit/Management), funnels them
  championship-style on an **evidence standard**. Mid "Genome Era" migration (see
  `wanda/docs/ATL_GENOME_ERA_MIGRATION_AUDIT.md`).
- **Seam:** PUSH — `observe` by polling its REST API / reading `league.sqlite` → normalized candidates.
  It *uses RAM and emits events* (unlike ForkUniverse). The strip line for CE: keep the machine
  (orchestrate/decompose/grade/funnel), swap the finance-specific intake.
- **Determinism:** live (not replayable) → transport offers *projection/prediction*, not computed futures.
- **Tests:** no `test_*.py` under wanda; quality lives in the Pliers cross-universe consistency tables.
- **HOOKED UP (2026-06-12): `oradio_engine/shims/atl_shim.py` + `tests/test_atl_shim.py` (5 pass).** The
  adapter does NOT import wanda or run docker — it polls `league.sqlite` **read-only** (the antenna
  model). Event candidates from `dev_runtime_events` + `timeline_posts` (cursor by `id`); gradable
  claims from `ml_promotion_recommendations` → the evidence service. LIVE class → records to the intake
  tape (record→replay byte-identical). **The smoke test runs against the REAL wanda DB** (708 events +
  25 promotion recs surfaced). DB path: `algo_trading_league/data/league.sqlite` (root
  `algo_trading_league.db` is empty). Next: add resolution joins so promotion/genome claims *settle*
  (hit/miss), and surface more tables (research_threads, ml_descendant_hypotheses, backtest_results).

### 2.6 MoCo  —  *sense* (LIVE, real-world telemetry)
- **Location:** separate drive `D:\mocomac\` (`src/moco/`, ~30 test files in `tests/`).
- **Simulates / does:** real-world motion → deterministic, **classified** controller intent
  (`event`/`hold`/`axis` labels → Xbox-style controls; currently emits via vJoy). MediaPipe pose +
  trained per-label profiles. "Seeing you without seeing you." A live source *and* a gradable League
  subject. WIP but wanted.
- **Seam:** LIVE PUSH — `observe` subscribes to the classified-label stream (already a clean
  classification → output boundary). The *classifier* is the deterministic core worth grading.
- **Determinism:** classification is deterministic given input frames; the input (you, moving) is live.
- **Tests:** rich suite (`test_gesture_classifier`, `test_pose_gate_adaptive_runtime`,
  `test_motion_insights`, `test_runtime_telemetry`, …). Hard deps: Python 3.11/3.12 +
  `mediapipe==0.10.18` + `numpy<2` for the *runtime*.
- **HOOKED UP (2026-06-12): `oradio_engine/shims/moco_shim.py` + `tests/test_moco_shim.py` (4 pass).** The
  adapter needs NO mediapipe — only MoCo's runtime does. MoCo writes a JSON telemetry SNAPSHOT
  (`logs/ui_runtime/<pid>.json`, overwritten ~5×/sec) with `recognition` (committed_label + top_score =
  the classification) and `output` (active_action/axes/buttons = controller intent). The shim is a
  `MoCoTelemetrySource` (LiveSource) that polls the snapshot and emits a candidate on each intent
  *change* (top_score → confidence), reusing the proven `LiveFeedOrgan` for tape/record/replay. LIVE —
  the canonical 'you moving' source. Tested with scripted payloads AND a real snapshot file on disk.
  Next: feed classifications into the evidence service (the classifier is the gradable core), and the
  "LLM-as-eyes" flagship benchmark (Sense+act).

### 2.7 OpenCloset  —  *build* (meta-organ, not a world)
- **Location:** separate drive `D:\openclaw\opencloset\`.
- **What it is:** a desktop-first local **agentic harness** for an LLM ("Clo") — explicit chat sessions,
  token-pressure watchdog, OpenAI-compatible local backend (llama.cpp/Ollama). It's the *hand that
  builds/improves the loop*, not a simulation that emits world-truth.
- **Seam:** NOT a world organ. Two honest roles: (a) a **club capability** (the local-LLM provider the
  voice/expression layer already needs), and (b) the **authoring/build agent** behind The Loom. Should
  plug in as tooling, not as a federated world. Flag for owner: confirm OpenCloset is meta, not an organ.
- **Tests:** has `evals/`, `coverage.xml`. Off-drive, llama.cpp/GPU deps — not executed.

### 2.8 Radio OS  —  *voice* (this repo; the body's nervous system)
- Not an organ to be shimmed — it's the host. Contributes: narration discipline, subtitle/host logic,
  the **antenna** (locked normalized candidate), **signal-heat** (emergent airtime), **broadcast
  grammar**, the **club** (configure-once resolution), and the `.oradio` package/runtime. The central
  engine is a descendant of this layer.
- **Tests this session:** `forkuniverse + signal_heat + broadcast_grammar + oradio_contract` → **81/81**.

---

## 3. Test scoreboard (this session)

| Organ | Suite | Result |
|---|---|---|
| ForkUniverse | `test_forkuniverse_{engine,compiler,ontology,bridge}` | 33/33 ✅ |
| Radio OS spine | `test_signal_heat` + `test_broadcast_grammar` + `test_oradio_contract` | 48/48 ✅ |
| Neikos | `tests/test_neikos_sim.py` (UTF-8) | 132/132 ✅ |
| Oracle Kingdom | `tools/oracle_sim.py` (deterministic, 4 eras) | runs ✅ (no pytest wrapper) |
| FTB | — | no sim-quality suite (gap) |
| ATL/League | — | off-drive, docker deps (not run) |
| MoCo | `D:\mocomac\tests` (~30 files) | off-drive, py3.11+mediapipe (not run) |
| OpenCloset | `evals/` | off-drive, llama.cpp (not run; likely meta) |

---

## 4. Gaps & open questions (for the owner)

1. **Neikos restore** — confirm the working-tree deletion was accidental (restored from git this session).
2. **OpenCloset's role** — meta/tooling (club LLM + Loom build agent) rather than a federated world organ?
   Recommend yes.
3. **Missing sim-quality suites** — FTB has none; Oracle Kingdom's lives as a print-script, not an
   asserting test. The merge's "test aggressively for purity across organs" bar needs these promoted.
4. **Determinism classes for the contract** — confirm `deterministic` (ForkUniverse/Oracle/Neikos/FTB)
   vs `live` (ATL/MoCo); this decides which transport actions each organ legally offers.
5. **Cross-organ ripple** — federation keeps worlds sovereign, but the event bus *can* let one organ's
   `observe()` become another's `apply_input()` (e.g. MoCo motion → FTB pressure). How much cross-talk
   does the owner want in v1 vs. strict isolation?

## 5. Recommended first incision

Define the shim contract as code in the club (the five verbs + the locked candidate shape + determinism
class), then implement the **smallest two shims to prove federation**: ForkUniverse (pull, already 90%
there) + Neikos (clean controller queue), running on one shared clock, narrated through the existing
signal-heat + broadcast-grammar spine. Promote Oracle's sim script and add an FTB sim test alongside.
