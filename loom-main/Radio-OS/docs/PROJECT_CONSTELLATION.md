# The Project Constellation — ATL · Radio OS · ForkUniverse · League: Community Edition

> A slow-down-and-record doc. Four entangled projects that have started feeding each other. There is
> **no strict chronological do-first/do-last order** — they compound. This captures what each is, how
> they relate, the DNA they share, and the causal (not calendar) sequence. Owner brain-dump, 2026-06-12.

## The shared DNA (why these are one family)

Every one of these is **an analogy that surfaces real evidence** — the way Radio OS "isn't quite
radio" and a League "isn't quite sports." It's *for show, except the data is real, and observations
are as real as we can prove them.*

- **Genome model.** Pull a thing apart into distinct components (entry strategies, exit strategies,
  organisms…), grade each constantly on performance, grade *assemblies* of components as combinations,
  and run a **championship-style funnel**. Treat the subject as evolving organisms, not a monolith.
- **Make your own tape.** Like ForkUniverse: don't just consume a feed — **compute your own signals
  off your own tape**, which explodes experimentation (hypotheses, predictions, questions — all
  tracked).
- **Extract truths, don't visualize.** The goal isn't dashboards; it's surfacing evidence and the
  relationships between data. The more complex the data, the better the material for rigorous review.

## The four (owner's numbering)

### 1. ATL — AlgoTrading League  *(separate repo; paused)*
The original, richest league. Mid-transition from the **Monolithic Strategy era → Genome era**: the
"Pliers report" surfaced enough structural insight to split strategy into distinct entry/exit/organism
units, graded and funneled championship-style. **Backend is basically ready** and coherent. **Frontend
is stuck in the monolithic era** — and worse, the monolithic strategy *is still running live and served
heavily across the site* because it was never disentangled. ATL is the **purest concept of what a
league is**; how it maps onto League: CE is still unclear, but it's the reference inhabitant.

### 2. Radio OS  *(this repo; current dev)*
The narration/observation OS that ships portable `.oradio` stations (see `CONVERGENCE.md`,
`NARRATIVE_WORLD_RUNTIME_VISION.md`). The ATL data-explosion gave a **new understanding of Radio OS's
intended architecture that kept the shipped spirit but enhanced it.** ATL's consumer here is the
`ATLFM` station.

### 3. ForkUniverse  *(this repo; mid-development)*
A deterministic, **on-demand universe calculator** — more calculator than daemon, regenerates ticks on
command, doesn't hold RAM idle (a *pull* source). The **richest narrative driver for Radio OS yet, as a
concept.** Ownership/boundary already settled in `CONVERGENCE.md §H` + `FORKUNIVERSE_RUNTIME_MODEL.md`.

### 3.5. League: Community Edition  *(to fork from ATL; NEW)*
The keystone realization: **we don't ship "AlgoTrading League" — we ship "League: Community Edition,"**
ATL's format **stripped of finance language** so it can study *any* real thing with market-like
evidence — pokemon cards, or ideally **live-updating data and the relationships within it** — and
**extract its truths** by treating entities as genomes and grading/funneling them, exactly as ATL does.
- A **wizard**: pick your league *subject*, then *configure* the league.
- Unlike ForkUniverse, **League is genuinely a server + container orchestrator** — it **uses RAM** and
  **emits events** (which Radio OS wants). ATL becomes one natural league *hosted on* League software.
- Current limit: **League (as ATL) can only be fed trading data** — generalizing it is the work.

## Architecture: how they plug into Radio OS (two antenna shapes)

This maps cleanly onto the antenna model we just built:
- **ForkUniverse = a PULL source.** Radio OS's antenna *calls* the calculator ("what's true now?");
  `elapsed → owed ticks` gives World Continuity for free.
- **League = a PUSH source.** A running server/orchestrator that *emits events*; Radio OS observes them
  (the classic daemon → observe antenna path, with signal-heat deciding airtime).

Both are just sources an `.oradio` station can be authored around. The `.oradio` stays exactly what
it's always been: an authored station that does what its author made it do.

## What League actually is (grounded in the repo, 2026-06-12)

Read `C:\Users\evana\Documents\freqtradebotchallenge\wanda\`. League today is a **local-first
research-league server** — far more built than "a dashboard":

- **A FastAPI server + container orchestrator.** `algo_trading_league/main.py` (a ~18k-line monolith)
  runs the league; each competitor is a live Freqtrade bot in its own container (docker-compose per
  team: cosmo-wanda, timmy, the-turners, second-act, dany, roadrunner, slaking…). It polls their REST
  APIs, imports trade DBs, normalizes everything into `league.sqlite`.
- **League surfaces:** standings, team pages, power rankings, timeline/Chronicle, trade explorer,
  exit-tag reports, backtest archive, version registry, research questions.
- **A scouting/evolution layer (the genome machinery):** ML hypotheses, feature library, model
  registry, **draft board**, candidate pipeline, **promotions**, lineage, telemetry essays, evolution
  reviews — plus a **Dev League** where an LLM generates candidate strategies (spec → compiled `.py` →
  validated → containerized bot).
- **The Pliers** (`scripts/pliers/`) is the genome-scoring engine: EntryRaw / ExitAlpha / ExitDamage /
  ChurnAmp + a cross-universe consistency table.
- **Daily full-state archive snapshots**, AI chat, maintenance cadences.

**The Genome Era (the transition, mid-flight).** The Pliers found a strategy decomposes into
independent organs — **Entry / Exit / Management genomes** traded in a **Universe/Habitat**. Doctrine:
*the `.py` file is a **compiled artifact, not an identity**; the real objects are the organs, and a
"strategy" like Timmy is a **successful assembly** of organs* (Entry + Exit + Management + Universe +
Config → compiled `.py`). Genomes graded independently, assemblies graded as combinations, funneled
championship-style (Dev League → validity-gated Entry×Exit×Universe matrix → "Day Zero" genome season).
Held to an **evidence standard**: cross-universe sign stability, validity gates, and explicit skepticism
about confounds (force-exit harvesting, organ separability). See `wanda/docs/ATL_GENOME_ERA_MIGRATION_AUDIT.md`.

**The strip line for League: CE.** Finance-specific = the *intake* (Freqtrade APIs, trades, exit tags)
and the organ *semantics* (entry/exit/management of trades). Generic = the *machine*: orchestrate
competitors, decompose them into graded genomes, assemble + funnel them on real evidence, archive and
narrate. League: CE keeps the machine, swaps the intake, and lets each subject define its own organs.
(The `pokemon_harness/` + `pokemon_x_harness.py` already in the repo are the first non-finance probes.)

## The unifying skeleton — one machine wearing four masks

The deepest finding from reading all four: they are the **same shape** — `spec/form → compile →
graded/observed running artifact` — and all four already insist the running file is a **compiled
artifact, not the identity.**

| Project | The spec (identity) | Compiles to | The artifact (not identity) |
|---|---|---|---|
| **ATL / League** | genome (entry/exit/mgmt) + universe | assembly compiler | the `.py` Freqtrade bot |
| **ForkUniverse** | seed + laws (creation form) | universe compiler | the running universe / ticks |
| **Radio OS** | station-author intent (Studio) | `.oradio` export | the baked, tiny `.oradio` |
| *(Studio itself)* | antenna + meta-plugin spec | spec → narration | the live broadcast |

So "League stress-tests ForkUniverse," "ForkUniverse feeds Radio OS," and "ATL becomes a League
inhabitant" aren't loose analogies — they're the **same compiler skeleton composing with itself.**
League grades genomes; a ForkUniverse universe *is* a gradable genome; a Radio OS station *narrates*
the grading. The **evidence standard** (prove your observations) is the shared currency that lets one
feed the next.

## The compounding map (causal, not chronological)

All four are **relatively equal in importance**, but right now **League compounds the rest**:

```
League: CE  ──(generalized harness/orchestrator + evidence/genome machinery)──►  stress-tests & grows ForkUniverse
ForkUniverse ──(richest narrative driver, to our complexity + evidence standard)──►  feeds Radio OS
Radio OS Studio ──(finish authoring + simulator)──►  output a .oradio of the universe/league → listen + LIVE-TUNE
ATL ──(genome-era frontend; disentangle the live monolith)──►  becomes a natural inhabitant of League: CE
```

- **League stress-tests ForkUniverse** — "isn't League how we truly stress-test something like
  ForkUniverse?" League's evidence/grading/funnel rigor is how ForkUniverse earns its complexity bar.
- **ForkUniverse → Radio OS**: once ForkUniverse is "ready" to our complexity + evidence standard, the
  pivot is to **finish Radio OS Studio** so we can output a `.oradio` and *listen to our universe*,
  live-tuning the station in the Studio simulator.
- **ATL → League**: ATL needs the genome-era frontend and must be disentangled from the live monolith;
  it then re-homes as one league on League: CE.

## Repo topology (so future sessions don't guess)

- **ATL / League** — separate repo at `C:\Users\evana\Documents\freqtradebotchallenge\wanda\` (the
  `algo_trading_league/` FastAPI package + per-team Freqtrade containers; backend rich, frontend
  monolithic-era; **mid genome-era migration**, planning in `wanda/docs/ATL_GENOME_ERA_MIGRATION_AUDIT.md`).
  Paused. The non-finance probe (`pokemon_harness/`) lives at the freqtradebotchallenge repo root.
- **Radio OS + ForkUniverse** — *this* repo. Holds the `ATLFM` station (ATL's Radio OS consumer) and
  the full `forkuniverse/` subsystem.
- **League: Community Edition** — to be **forked from ATL** (repo home TBD — see open questions).

## Open questions to sharpen later (not blocking the record)

1. **Where does League: CE get forked** — new repo off ATL, or developed alongside ATL in its repo?
2. **Definition of "ready"** — what concrete complexity + *evidence standard* bar makes "ForkUniverse
   ready" (→ pivot to Radio OS Studio) and "League generalized enough to host non-finance subjects"?
3. **ATL → League mapping** — what parts of ATL are league-generic vs. finance-specific (the strip
   line)?
4. **Near-term leverage** — since League compounds, is the practical next focus *generalizing League:
   CE* (so it can host ForkUniverse + arbitrary subjects), even with no strict order?

## One-line essence

> **League** is the arena that grades genomes on real evidence. **ForkUniverse** is a deterministic
> world you can grade. **Radio OS** is how you *hear* any of it. **ATL** is the first, purest league —
> and the reason we learned all of this.

---

## The One Thing — is it one project, or six? (resolved framing, 2026-06-12)

**One thing, but it's a *loop*, not an app.** You ship the organs + the protocol that lets them
compose — not a monolith. (Full roster now six; MoCo, OpenCloset, and the existing Radio OS games join
ATL / Radio OS / ForkUniverse / League.)

**One line:** *A loop for living systems — spin one up, prove its truths, evolve its parts on real
evidence, and hear it — and the loop can run on itself.*

The pieces are **verbs** in that loop, each individually expressive and kept as its own product:

- **ForkUniverse — generate.** A deterministic world from a seed ("Minecraft for universes").
- **FTB / Oracle Kingdom / Neikos — pre-built worlds.** Rich sims; can run standalone and be observed.
- **MoCo — sense.** Real-world motion → deterministic, classified data ("seeing you without seeing
  you"). A live source *and* a League subject (deterministic motion → Right Trigger).
- **League — grade & evolve.** Decompose any system into genomes, grade on real evidence, funnel and
  breed the winners. ML + live feed.
- **Radio OS — voice.** Makes any of it inhabitable; the front door (download a `.oradio`, tune in).
- **OpenCloset — build.** The agentic harness ("the project for making projects") that improves the
  loop itself.

**What it IS:** not "simulation" as a topic but a **stance** — *anything that behaves like a system can
be made legible, gradable, evolvable, and audible, and improvement must be **proven, not asserted***
(the evidence standard is the soul). Body = the `spec → compile → graded/observed artifact` skeleton.
Heartbeat = **self-driven improvement** (League evolves the subjects; OpenCloset evolves the tools).

**Product topology (recommended):** six expressive products · **one protocol** (organism-on-evidence +
spec→compile→observe) · **one front door** (Radio OS, the voice). Like the web = many sites + one
browser + one HTTP, or Steam = many games + one platform. *Not* a mega-app — merging them would crush
the individual expressiveness that's the point.

**Flow / why a stranger downloads it — two doors, same house:**
- *Consumer (the voice):* download a tiny `.oradio` to **inhabit something alive** — a universe, a
  league season, a motorsport world, even your own motion narrated. It just plays.
- *Creator (the workshop):* spin up / connect a system (ForkUniverse, MoCo, a game, a live feed) →
  grade & evolve its organs in League → author a station in Radio OS Studio → export a `.oradio`.
  OpenCloset is the agentic hand. The loop closes: each output is another's input.

**The open fork:** is the unifying *front door* the **voice** (Radio OS, consumer-first: "tune into
living systems") or the **builder** (OpenCloset, creator-first: "a forge for self-improving systems")?
Current lean: **voice-first** — the `.oradio` is the most downloadable, already-tiny, already-magical
artifact — with the builder as the second act.
