# The Simulation Engine — what `.oradio` decodes

> Owner-grounded definition, 2026-06-12. This is the canonical "what we are building **now**" doc —
> distinct from the organs we already built (`docs/ORGAN_CATALOG.md`) and the product framing
> (`docs/LOOM_ORADIO_ARCHITECTURE.md`). Treat the other docs as fuzzy; this one is the build target.

## 1. The codec framing (load-bearing)

- **`.png` is a codec.** It holds encoded instructions a decoder turns into pixels. Any decoder renders
  any PNG without knowing what the picture is.
- **`.oradio` is a codec for simulations.** It holds the *contract* (telemetry + lens + world +
  expression), not the simulation itself.
- **The engine (in the club) is the decoder.** Given any `.oradio`, it produces a living, narrated,
  visualized, time-scrubbable world *without knowing what is being simulated.* That generality **is** the
  product. An engine that only simulates one domain is a game, not a codec.

**The Loom is the encoder** (Photoshop, answering "what is your simulation?"). The organs (ForkUniverse,
Oracle Kingdom, Neikos, FTB, ATL/League, MoCo) are *proof the pieces work in isolation*; the engine is
the general substrate they collapse into.

## 2. What is new (vs. the six bespoke world-models we have)

Five general services the engine provides that no single organ does:

1. **Universal world-model substrate** — one entity/relationship/thread/prediction/memory store; each
   organ's hand-rolled world becomes an *instance* of it.
2. **Lens runtime** — maps *any* telemetry → world deltas (today each organ hardcodes its interpretation).
3. **Prediction → resolution → calibration loop** as a first-class service (today only ATL has it). This
   is the honesty engine; it is what makes a feed *a simulation instead of a dashboard.*
4. **The intake tape** — the determinism boundary (§3).
5. **The federation bus** (shared clock + cross-organ telemetry) and the **benchmark harness** (§5).

## 3. Determinism and live, reconciled — never blurred, always both

**Every simulation is `world(t) = f(seed, tape[0..t])`** — a pure deterministic function of a seed plus an
**intake tape**: immutable, timestamped recordings of live inputs.

- ForkUniverse: tape empty, seed only → fully computable; real time-travel.
- A live market/card station: tape grows as the feed arrives; the *intake* happens once and is live, but
  *everything downstream is a pure function of the recorded tape.*

The line is never blurred because it is a **literal data boundary**: below it, raw live inputs are
*recorded* (nondeterministic, stamped, never re-derived); above it, the whole world is *derived*
(replayable forever). "Make your own tape," then compute your own signals off it. This is why you always
have both — live skin, deterministic core, physical recording line between them.

**Transport honesty falls out of this:** for a deterministic source `f` is total, so fast-forward
*computes* the real future; for a live source you cannot fabricate tape entries, so fast-forward *marks
predictions and grades them later.* Same line, two behaviours. A source's determinism class decides which
transport actions it is even allowed to offer.

## 4. The Loom toolbox — the format of building

| Loom tool | Photoshop analog | The authoring act |
|---|---|---|
| **Telemetry sources** (antenna) | Place / import | "What do you observe?" RSS · API · file-watch · sensor · game-capture · an organ |
| **The lens** | Adjustment layers | "What does it *mean*?" observation → world event / pressure / evidence |
| **World model / ontology** | Document + layers | "What exists and persists?" entities · relationships · threads · predictions · memory |
| **Prediction loop** | *(no analog — the magic)* | "What does the world expect — and was it right?" |
| **Expression** | Canvas + export | voice · subtitle · ribbon · bubble, one grammar driving all |
| **Transport / time** | History + playhead | scrub / FF / rewind over the world's timeline |

**Organs are smart filters.** A blank `.oradio` = "watch this API and grow a world from scratch." An
organ-backed `.oradio` = "run *this* pre-built world." Same canvas.

## 5. The benchmark — what makes someone say "that's a simulation"

Image codecs have test images (Lena) and quality metrics (PSNR/SSIM). The 7 **simulation-quality axes**
are the SSIM of `.oradio` and the thing cross-organ testing measures:

1. **Reproducibility** — same `(seed, tape)` → identical world. *Replay divergence = 0.* (Neikos, ForkUniverse.)
2. **Causal traceability** — every state explains back to a cause. *(CausalLedger.)*
3. **Continuity** — leave, return, get backfilled, not reset. *(compute_absence.)*
4. **Responsiveness** — telemetry *changes the world*, never just displayed. *A spike must move pressure.*
5. **Emergence** — distinct inputs → meaningfully distinct worlds, above a floor and below chaos. *(Oracle's divergence metric.)*
6. **Predictive calibration** — forms predictions, gets graded; computed = fact, projected = scored hypothesis. *(ATL evidence loop.)*
7. **Expressive fidelity** — one world-state drives voice + theme + bubbles coherently.

Each organ already benchmarks one or two of these. The merged engine's suite = run all 7 across all
organs.

## 6. The four telemetry-coupling modes (scenario coverage)

The engine must cover four ways telemetry couples to a world. Pokemon is the worked example because it
spans three of them with one familiar domain (the engine makes **no** hardcoded Pokemon assumptions —
Pokemon is a *test subject*, like Lena is a test image).

| Mode | What it is | Pokemon probe | Organ that proves it |
|---|---|---|---|
| **Generate + advance** | seed world, no live input, pure-computed | — | ForkUniverse · Oracle · Neikos · FTB |
| **Watch + value** | live feed → emergent economy + scored beliefs | **cards** | ATL → League: CE |
| **Sense + act** | vision in → world → action out, graded on progress | **games** | MoCo (motion→intent→actuation) |
| **Decompose + evolve** | split into graded genomes, funnel on evidence | **strategy** | League genome / Pliers |

### Flagship benchmark — "LLM as eyes, not brain"
The single most important test. A prior experiment drove an LLM-as-brain to play Pokémon Scarlet on a
Switch via capture-card + gamepad injection (harness at `D:\New folder (2)`) and it played badly.
**Hypothesis:** demote the LLM to *eyes only* (frame → structured observation = a fallible telemetry
sensor) and let the **deterministic world-model + lens do the playing**. If structure-with-LLM-as-eyes
beats LLM-as-brain (graded on game progress), it empirically proves the engine's core thesis: *a
data-indexed simulation outperforms raw generation.* This is the "Sense + act" benchmark fixture.
Note: "play well" (Sense+act) and "know strategy" (Decompose+evolve) are the **same loop** — the policy
that plays is graded by the strategy evidence.

## 7. The emergence pipeline + OpenCloset's real role

"Hook up an API, #tracked, watch xyz, and it *becomes* a simulation" = `source → lens → world+prediction
→ expression`. The hard gap is the **lens**: raw observations → meaning without hardcoding the domain.

The agent that *authors the lens on the fly* — drafts the interpretation, world ontology, and meta-plugin
for a domain nobody pre-built an organ for — **is OpenCloset.** It is not club LLM plumbing; it is the
**emergence engine's authoring hand**, the thing that closes the gap between "I hooked up an API" and "it
became a simulation," and it is the loop running on itself (self-driven improvement). That is OpenCloset's
non-underwhelming role.

## 8. The shim contract (engine ⇄ organ), grounded in real seams

Every organ adapts to five verbs. Confirmed against the real code:

| Verb | ForkUniverse (pull) | Neikos (pull, queue) | League (push) |
|---|---|---|---|
| `identity()` | seed + `deterministic` | seed + `deterministic` | `live` |
| `advance(clock)` | `state.compute_absence(elapsed)` → delta | `{"action":"advance","ticks":N}` on `nk_cmd_q` | no-op (runs itself) |
| `observe()` | normalize `delta.new_events` → candidates | drain `nk_ui_q` → candidates | poll REST / `league.sqlite` |
| `read_truth()` | `delta` threads/predictions/heat | `{"action":"get_state"}` | sqlite snapshot |
| `apply_input(e)` | `apply_operator_input` | `_handle_cmd(e)` | (n/a v1) |

The candidate is the repo's **locked normalized shape**: `{post_id, source, title, body, priority, ts,
type, tags}`. Implemented in `oradio_engine/` (see `contract.py`, `federation.py`).

## 9. Push/live + evidence — how ATL and MoCo integrate properly

ATL and MoCo are off-drive (docker/Freqtrade; py3.11+mediapipe) and **push/live**, not pull. The proper
move is **not** a cold shim — it's to build the two contracts they need *here*, proven locally, so the
off-drive code shrinks to a thin, well-specified adapter.

**A. The push/live contract + intake tape** (`oradio_engine/live.py`). The determinism boundary (§3)
made concrete: a live organ RECORDS what arrived into an immutable, timestamped `IntakeTape`; replaying
the tape reproduces the bus byte-for-byte. Proven with a scripted source (`tests/test_live_organ.py`:
record→replay byte-identical; mixed pull+push federation; a LIVE organ correctly taints
`is_fully_deterministic`). **A real adapter only implements `LiveSource.poll()`** — ATL: read its REST
API / `league.sqlite`; MoCo: read the classified motion-intent stream. Everything else (recording,
replay, normalization, determinism accounting) is done and tested.

**B. The evidence / calibration service** (`oradio_engine/evidence.py`) — ATL's foundational
contribution generalized. Any organ's prediction rows normalize to one shape (mirroring ForkUniverse's
`Prediction`); the service tracks open/resolved claims + running **hit-rate, Brier score, calibration
error** (benchmark axis #6). Attached to `FederationEngine(evidence=…)`, it grades every organ's
predictions each tick. Proven with exact synthetic scoring *and* against ForkUniverse's real predictions
(`tests/test_evidence.py`). ATL later becomes a richer *provider* of the same contract — genome scores,
promotions, and research questions are just gradable claims.

## 10. The alignment layer — an `.oradio` is a DECLARATION the engine decodes

The codec made literal (built 2026-06-12). Before this, the engine ran *hand-registered Python*
(`eng.register(FTBOrgan.from_seed(...))`) — handcrafting. Now an `.oradio` is a tiny declaration
and the loader decodes it. Owner-approved descriptor shape (`world / telemetry / lens / surfaces /
club`); all domains are this same shape wired differently.

- **`oradio_engine/descriptor.py`** — `OradioDescriptor.from_dict` parses the declaration.
- **`oradio_engine/registry.py`** — `kind` → lazy factory for worlds (`ftb`/`neikos`/`oracle`/
  `forkuniverse`) and telemetry sources (`atl_league`/`moco`/`simulated_spatial_array`). Adding a
  world/source to the whole product = registering a factory; no engine change.
- **`oradio_engine/loader.py`** — `load_oradio(descriptor)` / `load_oradio_file(path)` →
  resolves kinds + applies the declared lens → a wired `FederationEngine` (evidence attached).
- **`oradio_engine/lens.py`** — the deepest piece: interpretation is **declared and composable**,
  not organ-bound. An organ's `observe()` is its world's *native projection* (only it knows its
  event schema); the **lens** is a declared pipeline of ops (`drop_types`/`keep_types`/
  `floor_priority`/`boost`/`retag`/`cap`) applied over that projection via a transparent
  `LensedOrgan` wrapper. The same world read through different lenses = different broadcasts, by
  declaration. *Example proven:* FTB's pbp flood (finding B) is tamed by a declared
  `drop_types` + `cap` lens in `motorsport-ladder.oradio` — `ftb_game.py` untouched.

Proven (`tests/test_oradio_loader.py`, 7 pass): the descriptor parses; a world is just a
declaration; the lens is declared + composable (same world, different read); multi-world `.oradio`;
**the spatial array emerges with no hardware** (`simulated_spatial_array` telemetry); the FTB flood
is tamed by declaration; and the example `.oradio` files load + run. Artifacts:
`examples/motorsport-ladder.oradio`, `examples/home-region.oradio`.

## 11. The binding layer — telemetry drives worlds; world actions drive effectors

Built 2026-06-12 (`oradio_engine/binding.py`). A **binding** routes a source's candidates into a
target's `apply_input` via a declared **transform** — the generalization of cross-organ ripple, and
what makes telemetry actually *drive* worlds (not just share a bus). **Bidirectional by
construction:**

- inbound: telemetry → world (`presence_to_signal`; a captured `frame_to_observation`)
- outbound: world → **effector** (`action_to_button` → gamepad; `presence_to_speech` → voice)

**Effectors** are surfaces that *act on the space* (`GamepadEffector`, `VoiceEffector`): organs that
only consume `apply_input`, record every act (auditable/replayable), and surface a confirmation.
Transforms and effectors are declared by kind, so routing lives in the `.oradio` (`effectors:` +
`bindings:` sections), not Python.

**Both flagships are the SAME machinery** (`tests/test_binding.py`, 5 pass):
- **Spatial house** — `examples/home-region.oradio`: presence at a node → the house *speaks*
  (`array --presence_to_speech--> voice`). Proven: the house reacts to each room you enter.
- **Pokémon Scarlet via VIDEO CAPTURE** — `examples/pokemon-scarlet.oradio`:
  `capture (eyes) --frame_to_observation--> navigator (brain) --action_to_button--> gamepad (hands)`.
  Perception is *vision*, not memory reads, so the eyes are a **transform** (the LLM-as-eyes seam —
  swap `frame_to_observation` for an `llm_perception` transform on real frames). The navigator is a
  deterministic brain; proven to drive real button sequences (up, up, A, right…) and to be replay-
  deterministic. The real rig swaps the simulated capture + gamepad for the capture-card + injection
  adapters at `D:\New folder (2)`; nothing else changes.

This is the unification the owner named: IRL spatial endpoints and the virtual space of a video game
are the *same problem* (observations drive a world that acts back into the space) yet *totally
different* (ESP32 presence vs. capture-card frames) — one Loom → `.oradio` → club handles both, and
the `.oradio` stays KB.

**Next:** the world-side projection (e.g. Neikos house-node ↔ map-node) so presence drives a *game
world* move, plus the iterate phase (FTB purity, ATL claim resolution, MoCo→evidence).

## 13a. The machine is the scientific method (framing, canon 2026-06-12)

The end-to-end run is not a feature list — it is **expansion then reduction** (a full breath):
**declare → federate** *generates* a world from a seed (the DOG move, adding information); then
**decompose → derive → observe → grade → funnel → trace** *distills* it down to proven, traceable
signal (the Index move, removing information). Inhale a universe from a seed; exhale it down to what
survived contact with reality. That is the scientific method: declare a universe, observe it,
generate claims, compare claims to reality, promote what survives, preserve lineage, repeat. Each
prior project is a *front end* on this one loop (ATL→evidence, ForkUniverse→derivation, DOG→
addressability, Radio OS→federation); **the Index is the middle layer that makes them one machine.**

Three canon emphases from the run:
- **§Open = instantiate a federation, not load software.** `open(x.oradio)` → a living federation
  *emerges*. `atl.oradio`, `market.oradio`, `pokemon.oradio` are all the same operation.
- **§Index: the breakthrough is *addressability*, not size.** "1 derivation," not "732M positions" —
  arbitrary access to an enormous *derivable* structure.
- **§Evidence: the engine refuses certainty** — not "I don't know" but **"I am not allowed to know
  yet."** A claim with no stored observation stays OPEN; it is never graded on a derived outcome.
- **§Funnel = survival under rising standards, not "higher score wins."** The bar is the *frontier of
  the surviving pool* (`observation.evolutionary_funnel` / `frontier_threshold`), so it rises because
  the weak die — endogenous, percentile-of-reality, never an absolute clock. The old absolute `gate`
  schedule was a cliff (50→1); the frontier funnel is graceful (50→24→10→4→1) with a rising bar.
- **§Thread = causal provenance** turns a simulation from a toy into an instrument: `lineage` answers
  *why is this output here* by walking back through cause (over the CausalLedger the organs carry).

## 13. The Index — derivable addressing (store the generator, not the output)

Built 2026-06-12 (`oradio_engine/index.py`). Named for the owner's acrostic EP *Index* (which
preceded *.D.O.G.*); the album is the existence-proof that a tiny seed + rules can *generate and
address* an infinitely deep structure where every position is derivable. The Index applies that to
engine data. **It is engine/club substrate, NOT a payload in the `.oradio`** (the file stays seed +
rules), and it adds **no new unit of simulation** — at library scale "the thing that indexes
`.oradio`s" is a recursive `.oradio` (composition), and the Index is what keeps that cheap.

The triple (same as the album):
- **`Address`** — a derivable coordinate, e.g. `("t", 144, "pred", 3)`, not a stored id.
- **`Index.resolve(address)`** — recompute the element from `(seed, generator, address)`; never
  stored (= ForkUniverse's `compute_absence`, generalized to all data).
- **`gate(level, t)`** — the rising bar (90% → 99% → 99.9% …), a derivable schedule like bitcoin
  halving / the noise floor it never beats. The **funnel** is the address tree: ascending = an
  address moving up; "what's surfaced" = `funnel(...)` keeps addresses whose *derived* score clears
  the gate. `lineage(address, parent)` collapses any deep address back to the seed in O(depth).

**Scales because the Index is O(seeds + rules) ≈ KB, never O(content).** Proven
(`tests/test_index.py`, 6 pass): predictions become coordinates; the gate rises toward but never
reaches 1.0; the funnel surfaces fewer as the standard tightens; **1 seed addresses 732M positions at
layer 12 and resolves the last one in a single derivation**; lineage collapses a deep address back
to the seed.

**The boundary (canon correction, 2026-06-12): derive the claim, STORE the verdict — NOT everything
is derivable.** A universe that derives its own outcomes is a closed system grading its own homework;
ATL's identity is *evidence outranks theory*. So: the *hypothesis* (claim + confidence + coordinate)
and the *rule* that produced it are derivable (Index); **reality's response is an observation —
recorded once, immutable, never computed** (`oradio_engine/observation.py` `ObservationLog`; the live
intake tape in `live.py` is the live-source form of the same store). `grade(index, addresses, log)`
JOINS derived claims with stored evidence; a claim reality hasn't answered stays **OPEN, never
assumed**. The loop is `Index → derives hypotheses → reality → evidence (STORED) → funnel grades →
Index updates`. Proven (`tests/test_observation.py`, 4 pass).

**The thread (canon framing).** An index is a *thread*: `lineage(address, parent)` walks an address
backward through its causes ("why is this genome here / why did this prediction surface / why did the
station say this") — over the **CausalLedger** that Oracle/ForkUniverse already carry. Three views of
one thing: **the thread** = user-facing (you pull, it leads backward through causality); **the Index**
= engine-facing (address + derive + lineage); **the evidence funnel** = which threads are worth
following (the rising gate, graded against *stored* evidence). Prediction, versioning, evidence, and
"pulling the thread" are the same thing from different angles. *(Generation vs. addressing: organs are
the generators — DOG-nature, seed→world; the Index is the locator — seed→coordinate→derive.)*
Engine total: **70 passed, 1 xfailed.**

## 12. The Club + the open lifecycle (MK1 power-on)

Built 2026-06-12. An `.oradio` is tiny because it ships references + contracts; the **Club**
(`oradio_engine/club.py`) is the machine-level membership that resolves those dependencies and
**asks the human only when something is new, or changed/vanished since last time** — configure
once, reuse forever.

- **Default theme packs ship with the Club** (`ribbon` [default], `smoke`, `aurora`, `ember`) so
  step 3 of the Loom form is never a wall; bring-your-own loop is optional (a custom path the Club
  remembers).
- `Club.resolve(descriptor)` → `ClubReport(ready, resolved, asks, missing_required)`. Asks carry a
  reason (`new` / `changed` / `vanished`). Optional deps (voices/llm/hardware) **never block** the
  deterministic engine; only a dep marked `required` gates readiness.
- Machine memory: `remember/recall/forget` to a JSON store (`~/.oradio_club/club.json`, override
  `ORADIO_CLUB_DIR`). URLs/endpoints aren't treated as filesystem paths (no false "vanished").
- **`open_oradio(spec, club=…, gate=True)`** (`loader.py`) is the full lifecycle: parse → Club
  resolves → (gate only on required) → build the federation → return an `OpenResult`.
- **CLI (the cave exit):** `python -m oradio_engine open <file.oradio> [--steps N]` resolves +
  runs + prints the bus/evidence summary; `python -m oradio_engine club` shows membership status.
  Proven: opening `motorsport-ladder.oradio` runs FTB (lens-tamed); `pokemon-scarlet.oradio` drives
  the gamepad (up/up/A/right/A) — both from a KB file via the CLI.

Tests: `tests/test_club_and_open.py` (5 pass) — default packs never ask, configure-once,
re-ask-only-on-vanish, full open lifecycle. Engine total: **60 passed, 1 xfailed.**

**What the off-drive adapters reduce to:**
- **ATL adapter** (in `wanda`'s env): a `LiveSource.poll()` over its REST/`league.sqlite` emitting
  standings/promotion/genome events as candidates, plus mapping its genome/Pliers verdicts into the
  evidence `Prediction` shape. Determinism class = LIVE (records to tape).
- **MoCo adapter** (in `D:\mocomac`'s py3.11 env): a `LiveSource.poll()` over the classifier's
  classified-intent stream (`event`/`hold`/`axis` → candidates). LIVE; its *classifier* is the
  deterministic core worth grading via the evidence service.
