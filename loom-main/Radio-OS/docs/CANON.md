# CANON — what is currently authoritative

> Read this first. The project accumulated many docs across sessions; some carry **stale assumptions**.
> To avoid coding an old assumption by mistake, treat the docs below as the **current source of truth**,
> and treat everything else as **history / vision — verify against canon, canon wins on conflict.**
> Last set: 2026-06-12. The **owner is the ultimate source of truth**; these docs track owner intent.

## Current canon (authoritative, build against these)

- **`docs/THE_LOOM.md`** — the creation form. The Loom's **four** questions (universe · signals · skin ·
  transient surfaces) = the `.oradio` descriptor humanized. The one hardcoded design surface.
- **`docs/SIMULATION_ENGINE.md`** — the engine / what `.oradio` decodes. Codec framing, the 5-verb organ
  contract, determinism + the intake tape, the evidence service, the alignment layer (descriptor →
  registry → loader → declared lens), and the binding layer (telemetry → world → effector). Mirrors the
  real code in `oradio_engine/`.
- **`docs/ORGAN_CATALOG.md`** — the organs (worlds/sources), their seams, tests, and findings.
- **`oradio_engine/` + `tests/`** — the code is canon by definition; docs describe it, tests prove it.

## Settled vocabulary (2026-06-12)

- **RibbonOS** = the presence layer / player ("tune in"). **The Loom** = the Studio (authoring).
  **`.oradio`** = the tiny artifact (a simulation masking as a radio station). Organs = sources that plug
  in upstream (Radio/voice, ForkUniverse, ATL/League, MoCo, OpenCloset, the games).
- **One unit of `.oradio`** = one decodable contract → one gradable, replayable behavior. (Keep crisp.)
- **The four form questions**: universe (`world`) · signals (`telemetry`) · skin (ribbon `surface`) ·
  transient surfaces (locally-served artifacts reached via notification+link — **NOT** overlay bubbles;
  player-purity law: the only interaction is the media transport). *(Voice/vantage + cadence: OPEN.)*

## History / vision (useful, but verify against canon)

These hold real thinking and history; they are **not wrong to read**, but where they conflict with the
canon set above, the canon set wins (they predate current assumptions):

- `RIBBONOS.md` — the presence-layer north star + the ribbon design (still the best ribbon reference;
  its "Loom's one question" list predates the confirmed 4-question form in THE_LOOM.md).
- `CONVERGENCE.md`, `PROJECT_CONSTELLATION.md` — how the organs converged; the loop/league framing.
- `NARRATIVE_WORLD_RUNTIME_VISION.md`, `LOOM_ORADIO_ARCHITECTURE.md`, `ORADIO_FORMAT.md`,
  `ORADIO_SCHEMA_V2.md`, `RADIO_OS_STUDIO_*` — earlier vision/architecture drafts; treat as drafts.

## How to keep canon honest

- When an assumption changes, **update the canon doc in the same session** (don't let the head-canon
  drift from the written canon — that gap is how errors get coded).
- New canonical decisions get a dated line in the relevant canon doc + a pointer here.
