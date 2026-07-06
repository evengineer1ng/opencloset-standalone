# THE LOOM — the creation form (CANON)

> **Canon status: AUTHORITATIVE as of 2026-06-12.** This is the current, owner-confirmed definition of
> the Loom authoring form. Where any older doc (`RIBBONOS.md`, `CONVERGENCE.md`,
> `NARRATIVE_WORLD_RUNTIME_VISION.md`, `LOOM_ORADIO_ARCHITECTURE.md`, the Studio docs) describes the form
> differently, **this doc wins** — build against current assumptions, not archived ones. See
> `docs/CANON.md` for the canonical set. Engine internals: `docs/SIMULATION_ENGINE.md`.

## The frame

**The Loom is the one place we stamp a hardcoded human design.** Everything downstream — the running
`.oradio` experience — is emergent. So the Loom form is small on purpose, and it is exactly the
**`.oradio` descriptor with a human face**: the things a person must *say*, compiled into the
machine-readable descriptor the engine decodes (`world / telemetry / lens / surfaces`).

**Law:** the LLM is only ever *eyes* (perception) or *voice* (narration) — **never the author**. The form
is hardcoded Python guidance; the engine is deterministic. No step depends on an LLM to *decide* what the
simulation is. (See `SIMULATION_ENGINE.md` — codec framing, determinism.)

## The form is FOUR questions

The first three feel like the whole thing; the fourth (transient surfaces) is easy to forget and must
not be — a 3-step form would bake an error.

### 1. Define the universe — the seed of truth ("inception")
> *"What is your simulation?"* → compiles to `world` (which organ/seed/premise).

Not a silly question — it's the seed of a simulation, the inception. We **soft-guide** with a
cycling/typing placeholder of **real examples**: *"a pokémon card tracker" … "an ant colony" … "a homelab
automation surface" … "Westworld S1 if it forked here (describe the fork)"* — transitioning like it's
thinking (*"how about…" "what about…" "hmm, maybe…"*).

- **The examples are HARDCODED Python, a large curated library — NOT LLM-generated.** Each must be a real,
  loadable template that actually runs. An LLM placeholder is a promise we can't keep; a hardcoded library
  is a promise that *whatever you type behaves like these do*. The examples ARE the spec, humanized —
  they teach "what one unit of `.oradio` is" by showing its range.

### 2. Define the signals — ingestion (antenna / threads)
> *"What feeds it?"* → compiles to `telemetry` (sources + binds).

The pure onboard for http · json · pdf · video · rss · files · APIs · sensors · another `.oradio`. This is
the technical magic of intake. (Naming: keep **antenna / threads** from `RIBBONOS.md` for continuity;
"signals" is an acceptable synonym.)

- **The "nonsense pairing" is VALID, by design — it must never error.** Put a hockey-talent world (Q1)
  with only the IMDB API (Q2): the engine **reads signal *dynamics*, not signal *meaning*** — bursts,
  novelty, spikes, silence (this is what `signal_heat` already does). IMDB's *activity shape* drives the
  hockey world's *dynamics* (a flurry → a call-up; a quiet stretch → a slump; a sudden hit → a breakout).
  The result is **surreal but internally honest.**
- **Why this is right, not a bug:** the engine is a *coherence-imposer*, not a *coherence-requirer*.
  "99.9% of existence makes no sense"; coherence is rare and local; minds manufacture it from noise. The
  natural pairing is the lucky special case. Payoff: zero validation friction, **and the nonsense is the
  wonder** (the dreamy/trippy/inception positioning). The funnel/evidence layer sorts it — most weird
  pairings feel low-coherence and plateau; the rare one that clicks ascends (emergence from noise).
- **Build implication:** a default lens that maps *any* foreign event onto a world by its heat / novelty /
  timing (not by semantic match), so the foreign-data case works out of the box.

### 3. The skin — the theme (ribbon)
> *"How does it feel?"* → compiles to a persistent `surface` (the ribbon).

The ribbon is the visible strand of state change (player = RibbonOS; see `RIBBONOS.md`). The Godot project
(`C:/Users/evana/OneDrive/Documents/ribbon-os/main.gd`) is a **clip** state machine: per category an
entry/loop/exit triplet, states `HOME_IDLE→ENTERING→LOOPING→EXITING` (+`REVERSING_ENTRY`,
`WAITING_FOR_LOOP_END`), seamless via frame-matched bridge clips + loop-boundary waits + a 0.35s crossfade
(~20×3 hand-authored clips = the labor we are deleting).

- **Owner's refinement DISSOLVES the seam problem.** The author uploads **ONE 3–15s loop** = the
  organism's visual DNA (small; a club asset, KB-referenced from the `.oradio`). A "state" is no longer a
  *different clip* but a **parameter vector over the same continuously-running loop** — so there is no
  splice, nothing to frame-match, and transitions become **parameter interpolation**. The discrete clip
  state machine collapses into a **continuous manifold** the simulation drives.
- **The morph engine is a pure DETERMINISTIC function `sim_state → shader_params`** (the Tier-1
  non-destructive effect stack: hue · glow · speed/frame-offset · blur · chromatic aberration · direction ·
  vignette · masks; see `RIBBONOS.md` §"How procedural is it"). Same state → same visual ⇒ an *honest*
  readout (benchmark axis #7, expressive fidelity).
- **One grammar for eyes and ears:** the same `signal_heat` + transition grammar drives ribbon params and
  the voice. Heat→intensity, era/thread→color family, spike→aberration pulse, play/pause/FF→flow tempo.
- **Form = "upload one loop."** Morph mapping has a sensible default; advanced users tune it.

### 4. Transient surfaces — what it surfaces (a doorway OUT, not a bubble IN)
> *"What moments should it surface, and when?"* → compiles to transient `surfaces` (template + trigger + local serve).

The forgettable-but-essential fourth question — and the one with a real design tension, **resolved
2026-06-12 (owner).**

**Player-purity law.** The `.oradio` player is a calm study surface — theme (ribbon) + subtitles +
transport. **The only interaction we want is the media transport.** No random clicking. And **nothing
overlays the visual you are studying** — a bubble over the ribbon doesn't clash *internally*, but it
clashes with *perception*. So transient surfaces are **NOT** floating bubbles/windows over the player.
*(This supersedes the bubble / pin / pop / fade model in `RIBBONOS.md`.)*

**Instead, a transient surface BREAKS CONTAINMENT.** When a threshold fires, the world emits a real
artifact from inside the simulation — a Loom-authored **template** (HTML/CSS) filled with live sim data,
**served LOCALLY** (a small local web server), reached via an **unobtrusive notification with a link** —
never an overlay over the studied visual. You click the link and open a standalone, locally-served page:
**you are actually reading a news article from that universe** (or a dossier, a ticker, a match report, a
wiki entry…). It feels like a genuine document leaking out of the world onto your desktop, read in its own
page — the player stays pure behind it.

- The author defines: the **template** (a layout with slots), the **trigger** (a threshold/condition over
  the world/telemetry), and the **binding** (which slots fill from what). AI fills slots; it never
  generates apps.
- Optional in the Loom — with none authored, the simulation simply has no transient artifacts; the player
  is unaffected.
- This is "extract truths, don't build dashboards," made concrete — and it keeps the player a single,
  uncluttered living surface.

## Defaulted / secondary dimensions (OPEN — confirm with owner)

`RIBBONOS.md` established two more authoring dimensions that today's 4-question framing did not name.
Current assumption: they are **smart-defaulted** ("we write it for them in the background"), not part of
the core first-run flow — but this is **unconfirmed**:

- **Voice / vantage — *"who tells it?"*** narrator · characters · panel · roundtable · newscast · improv.
  (Already built: the cast / show-format system.) Default: a single narrator vantage.
- **Cadence — *"how often do you check in?"*** ambient vs on-demand (the ForkUniverse elapsed model).
  Default: ambient.

**Open question for the owner:** are voice and cadence (a) folded into the 4, (b) defaulted/advanced as
above, or (c) genuinely questions 5 and 6? Resolve before building the form UI.

## How the form compiles (the answers → the descriptor)

| Form question | Descriptor field |
|---|---|
| 1. Universe | `world: { organ/seed/premise }` |
| 2. Signals | `telemetry: [ sources + binds ]` |
| 3. Skin | `surfaces: [ { kind: ribbon, loop: <clip>, morph: <default> } ]` |
| 4. Transient surfaces | `surfaces: [ { kind: transient, template, trigger, binds, serve: local, announce: notification } ]` |
| (Voice, defaulted) | `surfaces: [ { kind: voice, vantage } ]` |
| (Club, derived) | `club: [ … capabilities the above require … ]` |

The lens and bindings are **derived/emergent**, not asked. The `.oradio` stays KB (clips, voices, models
are club assets resolved once per machine).

## One-line definition

> **The Loom is a four-question form — universe, signals, skin, surfaces — that compiles a human's
> intent into a tiny `.oradio` the engine decodes into a living simulation.** It is the only thing we
> design by hand; the `.oradio` is the only thing that emerges.
