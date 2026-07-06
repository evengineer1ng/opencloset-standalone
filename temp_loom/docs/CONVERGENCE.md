# Radio OS — Convergence Map (vision ↔ codebase)

> Durable "where the unification stands" doc. Product intent lives in
> `NARRATIVE_WORLD_RUNTIME_VISION.md`; the build map in `RADIO_OS_STUDIO_PLAN.md`; this file is the
> **diff** between the two — what's built, what's missing, what's decided, and what comes next.
> Last updated: 2026-06-11.

## The frame

The `.oradio` vision is fully articulated and ~90% of the **spine** is built and test-netted. The work
now is **unification, not invention**: track the full vision, diff it against the codebase, and
combine / upgrade — without eroding the preserved foundations (`bookmark.py`, `shell_bookmark.py`, web
server, audio CLI). Preservation rule: **duplicate-and-continue; never edit the preserved files in
place.**

## A. Built and proven (the 90%)

- **Portability spine** — `oradio_resolver.py` (bundled → machine-cache → provisioned-LLM readiness
  ladder + size/SHA integrity; no degrade tier), `provisioning.py` (machine-level LLM "club" Tune-In),
  `oradio_player.py` (standalone bootstrapper: `--shell`, `--tune-in`, Windows file association,
  `build_launch_env`). `.oradio` = zip with `oradio.json` / `manifest.yaml` / `requirements.json` /
  `requirements.lock.json` (+ optional signature / spec / plugins / voices / art).
- **Two-shim antenna** — `shim_generator.py` makes scoped stdlib scouts (file_watch / log_tail /
  command_output / udp_listen) that write `RadioOSBridge/<source>/events.jsonl|state.json`;
  `plugins/antenna_bridge.py` reads them and emits the **locked normalized candidate**
  `{post_id, source, title, body, priority, ts, type, tags}`. `plugins/antenna_http.py` profiles
  HTTP/JSON sources into signatures. Contract: the antenna **observes**; it never narrates or ranks.
- **Meaning layer** — `plugins/meta/generated.py`: `generate_meta_plugin_spec(signature, station)`
  drafts an editable `meta_plugin_spec.json`; `GeneratedMetaPlugin.curate_candidates` +
  `generate_script` narrate via LLM (deterministic template only as a creator-side scaffold).
  `broadcast_grammar.py` performs runtime-decided in-character transitions ("Breaking news…" / "Yo,
  hold up.") with style presets (news_desk / sports / mission_control / casual_podcast / hype_bro).
- **Signal Heat engine** *(new, 2026-06-11)* — `signal_heat.py` turns the antenna's static `priority`
  hint into **time-decayed source activity** so airtime is emergent ("the hot world speaks; the quiet
  one recedes; silence is valid"). Wired into `curate_candidates`; authored via the spec's
  `signal_heat` block + per-source `heat` overrides. See §E.
- **Studio** (`radio_os_studio.py`, Tkinter, 6 tabs) — Source · Feeds & Cast · Meaning ("Who's On The
  Air?" + a Signal Heat panel) · Production (SFX + event rules + interstitials) · Simulator (previews
  through `bookmark.py`) · `.oradio` (export + Check Readiness).

## B. The gap map (the 10% + unification)

1. ~~**Signal heat is a hint, not an engine.**~~ **CLOSED 2026-06-11** — see §E.
2. **Transient surfaces are still text cards.** No data-driven template/visual-genome runtime
   selection, slot-filling, pinning, or drag-drop Surface Designer. The "transient window widget" is
   unbuilt. **Decision: render as embedded webview HTML/CSS templates** (matches the vision's
   "templated html/css" language; unifies toward the web platform). **Additive** — a new surface type
   that washes in/out *inside* the windows/widgets desktop shell; it does **not** replace the frontend.
3. **No forked Library/Vault.** `oradio_player_ui.py` opens one `.oradio`; `shell_bookmark.py` is
   preserved/un-forked. The "Studio on one side, vault on the other" native app doesn't exist as a
   converged descendant.
4. **Transport half-wired.** Pause/Rewind/Forward are disabled (no external command seam into the
   `bookmark.py` kernel); call-in (mic) exists as plugins (`plugins/callin.py`,
   `plugins/cp2077_voice_input.py`) but isn't wired into the new shell's transport row.
5. **Simulator is start/stop, not live hot-edit.** Vision wants hot-editing meaning while it plays.
6. **World continuity / backfill** ("resume now, backfill only what matters") — not implemented as a
   vision-aligned behavior.
7. **Web app + audio CLI unification** — real but not converged onto the spine. **Deferred** (focus is
   the open-source downloadable version).

## C. Decisions locked

- **Surfaces = embedded webview (HTML/CSS slot templates).** AI fills slots; never generates apps.
  Additive — inside the desktop shell, not in place of it.
- **Theme: keep green monokai — it is correct.** (Owner correction; the earlier "drop monokai →
  charcoal/cyan carousel" framing in the handoff/vision docs was wrong.) The **good** theme system is
  the library's named-preset set (`shell_bookmark.py` `COLOR_THEMES`: dark/light/nord/dracula/
  **monokai**); the runtime's `self.art` theme path is the weaker one. **Integrate the named-preset
  system OVER the runtime for the palette (theme + accent); the runtime keeps its own per-station
  BACKGROUND/wallpaper customization.** Canonical source now lives in `radio_os_theme.py`
  (`palette(name)` + `runtime_art(name, global_bg=...)`), `DEFAULT_THEME = "monokai"`.
- **Runtime = modernize `bookmark.py`'s frontend; do NOT discard it.** Its windows-and-widgets
  desktop-shell, theme editor, and wallpaper system are load-bearing ("backend rich, frontend weird
  good *and* weird bad"). The job is **fix the theme integration + streamline** (one media-transport
  row, hide the authoring workbench, turn `Window 1/2/3` into ambient surfaces) — **not** reinvent the
  layout, and **not** run the kernel headless under a thin from-scratch chrome.
- **Preservation rule holds** — never edit `bookmark.py` / `shell_bookmark.py` in place. (How far to
  fork vs. a minimal additive change is being decided with the owner — see §F.)
- **Docs caveat: the intent docs have known inaccuracies.** Treat `NARRATIVE_WORLD_RUNTIME_VISION.md`
  / `RADIO_OS_STUDIO_PLAN.md` / the handoff as *drafts to verify against the owner*, not gospel. This
  file is corrected against owner intent as we go.

## D. Runtime fork — verified finding (read before touching the runtime)

`oradio_player_ui.py` (Jun 11, 40KB, **uncommitted**) is the "bookmark fork from yesterday." There is
no other frontend-fork file. **Finding:** it launches `bookmark.py` via `subprocess` **headless**
(`CREATE_NO_WINDOW`) and renders its *own* minimal Tk text-cards — it does **not** inherit bookmark's
windows/widgets/theme-editor frontend. Per the owner's correction ("the frontend is not disposable"),
this is **the wrong conclusion expressed in code**: it threw the desktop shell away instead of
modernizing it. **Redirect:** drive the `.oradio` runtime from `bookmark.py`'s real GUI (keep green
monokai; integrate the named-preset palette via `radio_os_theme.py`), rather than headless-wrap it.

**Progress (2026-06-11):** forked `bookmark.py` → **`oradio_runtime.py`** (full copy; bookmark.py
preserved) and wired its palette to `radio_os_theme.py` — monokai default, inherits the Library's
theme when installed (`RADIO_OS_INHERIT_LIBRARY_THEME`, `RADIO_OS_THEME` override), per-station
background/wallpaper preserved. Compiles; theme logic verified headless (GUI render is owner-side).
**Owner verified the monokai theme on screen (2026-06-11).** Launch path **lined up**:
`oradio_player.py` `RUNTIME_PATH` → `oradio_runtime.py`, and the Windows association drops `--shell`
so double-click opens the real GUI runtime (not the headless thin-chrome). `run_runtime.py` opens a
station in the fork for eyeballing.

**Club gate built (2026-06-11).** The asset club now mirrors the LLM club: `provisioning.py` remembers
voice dirs + Piper bin machine-level (`save_voices_dir` / `get_voices_dirs` / `save_piper_bin` /
`get_piper_bin` / `assets_summary`); `oradio_resolver.py` consults those remembered locations so a
folder shown ONCE resolves every future `.oradio` (and dead paths drop, so we only re-ask when a
location genuinely vanishes). CLI seams: `oradio_player.py --remember-voices/-piper/--club-status`.
The not-ready path is now earnest + actionable, and `club_gate.py` is the Tk "ask once, remember"
moment, wired into `launch_oradio(gui_gate=…)` for interactive opens only (off in tests).
**Owner-verify:** `python club_gate.py <station.oradio>` (GUI render is owner-side). Engine + resolver
+ CLI are covered by `tests/test_club_assets.py` (6 tests). Full suite: **68 passed**. Confirm eyes-on (a full GUI run of
`oradio_player_ui.py` and `radio_os_studio.py` was *not* done this session — verification here was via
tests/imports) before committing to the redirect.

## E. Signal Heat (closed 2026-06-11)

`signal_heat.py` — pure, stdlib, standalone-testable (mirrors `broadcast_grammar.py`). State lives in
`mem["_signal_heat_state"]`; no preserved file touched.

- **Model:** each observation folds its `priority` hint into its source's heat (`gain * priority`,
  capped at `max_heat`); heat decays exponentially by a per-source `half_life_sec` (lazy decay on
  read, so callers can't desync). `rank_candidates` orders by a blend of a candidate's own priority
  and its source's **live** heat; sources under `quiet_floor` recede; survivors are annotated with
  real `heat` (0–1) and `interrupt` (≥ `interrupt_threshold`). `is_silent` ⇒ dead air is valid.
- **Authoring:** `generate_meta_plugin_spec` now emits a global `signal_heat` block + a per-source
  `heat` override on every source. Studio Meaning tab has a **Signal Heat** panel (how loud / when it
  cuts in / quiet floor / cools-after) writing the four globals; per-source overrides via Advanced
  Details JSON. The real `heat` feeds `broadcast_grammar` so `heat_change` transitions fire on genuine
  emergent shifts, not a `priority/100` proxy.
- **Proven:** `tests/test_signal_heat.py` (10 tests, incl. an integration test showing airtime shift
  from a spiked source to a steady one as heat decays). Full named suite: **62 passed**.

## F. Recommended sequence

1. ~~Signal Heat engine~~ ✅ (this session).
2. **Runtime-fork redirect** — eyes-on audit of `oradio_player_ui.py` (§D), then re-fork
   `bookmark.py`'s frontend and modernize rather than headless-wrap.
3. **Transient Surface system** — webview render path + runtime template selection + Studio designer.
4. **Library/Vault fork** — descendant of `shell_bookmark.py`.
5. **Transport + call-in seam** — kernel external-command seam; wire mic.
6. **Simulator hot-edit**, then **world-continuity backfill**.
7. **Web/CLI unification** (deferred).

## G. What Radio OS *is* — the container, not the dependencies (artifact + club)

This is the identity answer that shapes portability. (Owner intent, 2026-06-11.)

- **We are the container, not the sum of our dependencies.** Radio OS's own footprint is *tiny*. The
  weight is dependencies — LLMs, TTS/voice models, the Python runtime itself. **We do not ship those;
  we ship the container that knows how to *connect* to them.** The clearer we separate our role from
  the deps, the truer the product.
- **The `.oradio` is a baked artifact.** It is the generated output of Studio — **small**, with some
  ceremony, but **not raw source on display**. Model it like a Steam game: local folders exist, but
  the source isn't the surface; it's been "baked." (Today's export still ships `plugins/*.py` as
  source → **baking strategy is an open gap**, deferred but tracked.)
- **A `.oradio` is tiny because models are connected, not bundled.** The LLM may be a local model or a
  cloud API; voices may be local Piper/Kokoro or a hosted voice — **we ship neither**. The artifact
  carries the contract that knows how to reach them. **Proven 2026-06-11:** `export_oradio.py` bakes a
  station headless; bundling the 8 BasketballFM voices made a **217 MB** artifact, while the default
  **reference-only** export is **13.2 KB** and still resolves all 8 voices via `machine-cache` (the
  club). Reference-only is the default; `--bundle-voices` opts into the fat artifact for a bare machine.
- **Tiny ⇒ headroom for identity: cover art + metadata.** `export_oradio.py` embeds a `cover.jpg`
  (auto-generated branded cover, or `--cover <img>`) and a media-style `metadata` block in `oradio.json`
  (title/artist/album/genre/comment/artwork — a nod to MP3/MP4 tags). `--polyglot` prepends the cover
  JPEG so the **file is simultaneously a valid JPEG and a valid `.oradio`** (ZIP reads from the end,
  JPEG from the start; verified: a 29.7 KB polyglot opens as a 512×512 image *and* resolves all voices).
  **Honest limit:** foreign apps key off the `.oradio` *extension/handler*, not bytes — surfacing the
  cover as an Explorer thumbnail needs a **Windows thumbnail handler** (real, heavier, later); the
  polyglot guarantees the bytes are an image for any content-sniffing viewer / `.jpg` rename today.
- **Club model = configure once, reuse forever.** The first open of *any* `.oradio` preconfigures
  machine-level settings for every future `.oradio`. The LLM already does this (`provisioning.py`
  machine-level membership). **Do not re-nag.** Ask **once**, earnestly — *"I noticed your voice
  models aren't here — can you show me where they are?"* — persist it machine-level, and re-ask **only
  when something genuinely goes missing** (*"I can't find the voice models anymore — can you point me
  again?"*). Same for API keys: create/enter once, reused as long as the engine is reachable.
- **Dependencies resolve by consent, not fiasco.** Missing python / deps / LLM / TTS should surface as
  an earnest *"You're missing X — want me to set that up? [yes / no]"* that actions the fix on yes and
  steps aside on no. **Python itself is just-another-dependency** (the key for now); a missing-python
  first run is a guided, consented install — never a wall.
- **The club also helps the antenna find its target — per shape.** `antenna_resolver.py` (2026-06-11)
  resolves what a station listens to, then remembers it machine-level like voices. **Folders/games are
  auto-discovered in good faith** — it enumerates every Steam library across drives
  (`libraryfolders.vdf`) + registry + common roots and matches by appmanifest `installdir`/name (a
  `fallout.oradio` finds the Fallout folder on C: *or* D: on its own; verified it found both Steam
  libraries here). Each shape fails differently and is handled honestly: `http` unreachable = a
  service/launch problem (not a path); `rss` dead = the feed moved (not your machine); `files` missing
  = point me; `credential` feeds (social/API logins) are the *key* club's job, not a path to find;
  `no_target` = a query/internal feed with nothing to locate. Anything not auto-found is **pointed once
  and persisted** (`provisioning.save_antenna_target`, keyed) — paths vary per machine. Covered by
  `tests/test_antenna_resolver.py` (8 tests). **Wired into the open flow (2026-06-11):** `launch_oradio`
  surfaces antenna readiness on every open (non-blocking — only the LLM hard-blocks; silence is valid),
  and `club_gate.py` now offers a "Show me where '<antenna>' is…" picker per unfound folder/file target
  (persists via `remember_antenna_target`), alongside the voices/Piper pickers. **Resolution → use done
  (2026-06-11):** `apply_resolved_targets(manifest)` rewrites each folder/file antenna's config key
  (`bridge_dir`/`scope`/`path`/…, tracked via `infer_target`'s `source_key`) to the resolved/remembered
  path; `launch_oradio` writes the patched `manifest.yaml` into the extract dir before the runtime reads
  it, so the **running antenna watches the real install, not the baked path**. Non-path antennas are
  untouched; input is never mutated. Covered by `tests/test_antenna_resolver.py` (10 tests). **Still
  open:** Studio authoring of an explicit `target` block (kind/names/steam_appid) for crisp
  auto-discovery, and `files`-list antennas (e.g. `document`) aren't auto-patched yet (ambiguous).

## H. ForkUniverse — a sibling engine, kept correctly distinct (ownership)

ForkUniverse is a separate side project: a deterministic engine for **causally observable universes**
that **regenerate or generate ticks on command** — *more a calculator than a daemon*. It lives in
`forkuniverse/` (compiler · engine · ontology · runtime) with its own docs (`docs/FORKUNIVERSE_*`).
The connection to Radio OS is obvious — it's a generator of narratable data — but the two must stay
**distinct, not fused.** (Intent already nailed in `docs/FORKUNIVERSE_RUNTIME_MODEL.md`: "treat
ForkUniverse as a telemetry source, not a fused subsystem.")

**The ownership knot, resolved.** A universe is not a process, so nobody "drives" it — you **call** it.
Separate three things and the confusion dissolves:
- **ForkUniverse owns the laws** — compiler + engine + seed. Frozen, deterministic, regenerable; idle =
  zero RAM; closing ForkUniverse Studio is irrelevant (you authored laws, not a running thing).
- **The `.oradio` owns a bookmark + a question** — *which* universe (the seed/creation-request, which
  lives in the station manifest's `forkuniverse:` block → the universe **travels as data** and
  regenerates anywhere; the snapshot is an optional checkpoint) and *how often to ask*.
- **Radio OS owns the asking + the playhead** — not the laws. Its antenna asks "what's true now?", the
  engine turns elapsed real time → owed ticks (`compute_absence`) and returns the delta; Radio OS
  narrates. "It lives on in Radio OS" the way a song lives on in a music player: a reference + a
  playhead, never the master recording or the studio.

This is a **pull antenna**: most antennas observe a push source (daemon→scout→bridge→read); ForkUniverse
inverts it — the antenna *calls* the calculator. And `elapsed → owed ticks` hands Radio OS its **World
Continuity** (leave for days, come back, backfill what matters) for free.

**Assessment of the existing bridge (2026-06-12):** less overstep than feared — the engine, laws, seed,
and persistence are all ForkUniverse's; `plugins/meta/forkuniverse_meta.py` only holds a *handle* and
narrates. The one lean toward fusion was the **default**: it shipped `advance_mode: fixed` on a fixed
clock (ForkUniverse's *optional* daemon mode, run inside Radio OS) instead of the doc's recommended
on-demand default. **Realigned (accept-and-continue, removed nothing):** ForkUniverseFM now uses
`advance_mode: elapsed` (routes advancement through `compute_absence`, the on-demand truth-query),
sized to the prior cadence; `forkuniverse_feed.py` reframed from "Tick-driver/drives" to a **check-in
clock that asks** the universe for owed truth. The bridge tests are unaffected (they use the code
default `fixed` with their own config). **Open:** make `elapsed` use *true wall-clock* elapsed (the
feed already passes `timestamp`; wire it into the meta plugin's `_advance` so real gaps backfill), and
package a ForkUniverse universe into a tiny `.oradio` (seed travels as data; snapshot optional).

## I. Fortify the `.oradio` — listen and/or visualize (ribbon = the default visual surface)

Direction (owner, 2026-06-12): after the constellation work, **pour energy into making the `.oradio`
itself excellent.** It is the **seam** — the contract both the voice runtime and the ribbon renderer
consume — so hardening it unblocks audio *and* visuals at once, and needs no Godot to be ready.

- **A `.oradio` is a tiny (KB) living station file that can LISTEN to and/or VISUALIZE a simulation.**
  The club is settled ("when you're in, you're in"). Some stations are listen-only (headless/voice),
  some visualize, most do both — the package **declares its surfaces.**
- **Ribbon is the default visual surface** — *how the simulation is feeling, visually* — driven by the
  **same state grammar as the voice** (`signal_heat` + `broadcast_grammar`): one grammar, eyes + ears.
  **Theme-mods** are alternative, pluggable visual surfaces, *later* (ribbon is the reference surface).
- **Key law (consistent with the club): the ribbon clip library is a CLUB ASSET, not shipped.** The
  `.oradio` carries only the **ribbon-genome recipe** — which genome, effect params, transition class —
  a few KB, exactly like it references voices instead of bundling them. The resolver/gate resolve the
  ribbon library machine-level ("fetch once / point once"). So the artifact stays KB-tiny *and*
  visualizes richly. (Same "connect, don't ship" law that took BasketballFM from 217 MB → 13 KB.)

**Fortify punch-list:**
1. **Format v1 spec + validation + versioning** — lock required/optional, harden integrity + portable
   warnings, keep the polyglot cover + media-metadata.
2. **Visual-surface block** in the manifest/package — `surface: ribbon | theme:<id>`, the ribbon-genome
   recipe, and the **transient reporting surfaces** (the templated evidence surfaces from the vision,
   now visual).
3. **Ribbon library as a club asset** — resolver + gate learn it, mirroring voices/piper/antenna.
4. **The state-grammar → visual binding** — the contract mapping `signal_heat`/`broadcast_grammar`
   events to ribbon params, so any renderer (Godot now, others later) consumes one declared intent.
5. **Theme-mod surface plugin** — later; ribbon is the reference implementation.

## J. The `.oradio` time substrate — transport as general-purpose simulation time controls

The unlock behind "what is your simulation?": *it's all simulation* — even the AI is a **data-indexed
simulation of reality** (skilled, mostly-grounded, occasionally wrong — a photographer's Photoshop),
not reality itself. And a simulation has a **timeline** — so the player's transport row stops being
audio scrubbing and becomes a **time machine over the simulation.** This is what gives the
(currently-stubbed) Pause/FF/Rewind buttons a real meaning.

- **▶/⏸ Play/Pause** — advance at the station's natural cadence, or hold.
- **⏪ Rewind / skip-back** — the recorded past: transcript + state ledger. *Always honest* (it happened
  or was computed). Deterministic sources can re-derive any past moment from the seed.
- **⏩ Fast-forward** — advance *ahead of now*, in one of two honest modes by source nature:
  - **Deterministic (ForkUniverse-class, replays):** *compute* the future ticks — real, lossless,
    repeatable; true time travel. (ForkUniverse's `compute_absence(owed_seconds)` is exactly this.)
  - **Live/real (RSS, APIs, markets, MoCo):** you can't fast-forward reality, so *project* a future from
    the present — a **marked prediction** that becomes a **tracked hypothesis scored later against what
    actually happens.** Fast-forwarding a live station *generates gradable predictions* → this is the
    League/ATL **evidence loop**: the time machine and the grader are the same mechanism.
- **Speed (slow/fast)** — the simulation *rate* (the cadence / time-policy "universe law" already
  encoded; e.g. 1 min real = 1 day simulated).

**The honesty rule (the owner's "data-indexed not to hallucinate," made structural):** the transport
**never lies about which future you're seeing.** A *computed* future is presented as fact; a *projected*
future is presented as prediction and logged as a hypothesis. That single rule is the line between
*simulated reality* (the product) and *hallucinated reality* (the failure). A source's determinism
level decides which transport actions a station even offers.

**Contract implication (v1.x substrate):** a small **`time` capability** in the `.oradio` —
`deterministic: bool`, `cadence` (real→sim ratio), and the supported `transport` actions — so the
validator, resolver, and the runtime transport row agree on what this station can do across time.
**Already proven:** ForkUniverse is the reference time-scrubbable source; the runtime already has the
transport row (Play/Stop live; Pause/FF/Rewind stubbed "no kernel seam" — now they have a *meaning*).
The seam to build is a kernel time-command + the declared `time` capability.
- **The engine underneath may change; the artifact should not.** Long term the container may not be
  Python (Rust / C / "fits on the tip of your finger"). Python is the key *for now*. Design so the
  `.oradio` definition outlives the implementation language.
- **Launch-path implication:** the runtime GUI (`oradio_runtime.py`) is the player; the first-run gate
  is the club setup (persisted, earnest, ask-once); a machine that's already in the club just opens the
  window.
