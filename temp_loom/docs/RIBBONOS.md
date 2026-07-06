# RibbonOS — the presence layer (north star)

> Resolved identity, 2026-06-12. The umbrella name for the whole constellation is **RibbonOS**.
> Radio OS, League, ForkUniverse, MoCo, OpenCloset, ATL are **organs**, not the brand. The companion
> map is `PROJECT_CONSTELLATION.md`; this doc owns the *face*.

## Canonical names — the family (decided 2026-06-12)

Two things we make; everything else is a source that plugs into the contract between them.

- **The Loom** — *the Studio.* Where `.oradio` stations are **woven** (authoring / editor). Replaces
  the working name "Radio OS Studio." The metaphor is generative and should drive its UI vocabulary:
  **threads** (sources / antennas) run through a **pattern** (meta-plugin + transition grammar) to weave
  a **ribbon** (the living visual/state); the finished **cloth is the `.oradio`.**
- **RibbonOS** — *the presence layer / player.* Where you **tune in** to what the Loom wove.
  *"Woven in the Loom, played in RibbonOS."*
- **`.oradio`** — *the artifact.* A tiny (KB) living station — **a simulation masking as a radio
  station.** Carries intent (threads + pattern + ribbon recipe + voice), not heavy payload; voices and
  ribbon clips are **club assets**, resolved once per machine.
- **Sources** (Radio/voice, audio CLI, MoCo, OpenCloset, ForkUniverse, ATL, League, the games) plug in
  **upstream of the contract** — threads on the Loom — not absorbed into one app.

> The pitch: *open RibbonOS to tune into a simulation masking as a radio station — woven for you in the
> Loom.*

## The Loom's one question: "What is your simulation?"

The Loom organizes around a single onboarding question — **"What is your simulation?"** — and
everything is downstream of it (and most of it is already the Studio's existing tabs, reframed):

- **What feeds it?** → *threads* (antennas/sources): "my spreadsheets" → a data antenna, "Fallout" → a
  folder, a ForkUniverse seed, your body via MoCo. *(built)*
- **Who tells it?** → *the vantage*: narrator · characters · panel · roundtable · newscast · improv. 
  troupe. *(built — the "Who's On The Air?" show-format + cast system)* any voice can exist here
- **How does it feel?** → *the ribbon* (default visual surface) + production SFX. *(ribbon = gap)*
- **What does it surface?** → *transient windows*: loom-author templates (HTML/CSS or shim-style
  data-container templates) the data pops into, then recedes back to ribbon. *(gap)*
- **How often do you check in?** → *cadence*: ambient vs on-demand (the ForkUniverse elapsed model).

So the Loom = the Studio **reframed around one question**, plus two real gaps (ribbon surface,
transient-surface templates). The hard backend — antennas, club, signal-heat, transition grammar, the
tiny `.oradio` — is done.

**Positioning: lean into dreamy / trippy / inception — don't sand it off.** Your spreadsheet sprouts a
particle-accelerator ribbon that murmurs sweet nothings about your data, pops a transient window, then
settles back to ribbon. That *wonder* is the product; "AI radio" undersells it. The boring-data demo is
the trojan horse — everyone has a spreadsheet.



> RibbonOS is not the mega-app. It is the **presence layer.**
> Not where all your apps go — where their **living state becomes visible.**

First 10 seconds: a fullscreen-ish **living canvas with a ribbon already moving**, and minimal UI over
it — a carousel of stations/worlds/systems, current signal heat, and three actions: **Tune In ·
Inspect · Build**. The first impression is **identity, not utility.**

You **boot a session**, not an operating system. The beautiful boot sequence works because you're
entering a system-state, not taking over the machine. Immersive window, optional fullscreen (Steam Big
Picture energy, without hijacking the PC).

## Why the name is RibbonOS, not Radio OS

The face of the thing is no longer audio — it's the ribbon. "Radio OS" creates a mismatch ("why am I
looking at a glowing evolving organism if this is a radio?"). RibbonOS answers instantly: *because the
ribbon is the system's visible flow.* Radio becomes one organ:

- **Radio** — the **voice** of the ribbon.
- **League** — the **judge** of the ribbon (evidence + evolution).
- **ForkUniverse** — the **worldmaker** behind the ribbon.
- **MoCo** — the **body sensor** feeding the ribbon.
- **OpenCloset** — the **builder** maintaining the ribbon.
- **ATL** — the first serious **live organism** plugged into the ribbon.

## What is a ribbon

> A **visible strand of state change** — how a living system shows its motion.

Every source gets a ribbon: ATL a market/evidence ribbon, MoCo a body/input ribbon, ForkUniverse a
world-generation ribbon, Backmarker a race-state ribbon, Radio a topic/signal ribbon, OpenCloset a
build/task ribbon. The ribbon is the **visual readout of the transition grammar** — the breakthrough:

> **One transition grammar for eyes and ears.** The spoken transition and the visual transition are two
> renderings of the *same state change* (`broadcast_grammar.py` + `signal_heat.py` already produce it).

## The player chrome — locked (the listener surface)

**Principle: the theme is the protagonist.** The evolving ribbon — distorting and morphing to embody
the simulation — is the magic, *possibly the whole magic* for some people, before it even speaks. So
the player is **Spotify's song view, not its library.** Never bury the listener in widgets (bookmark.py
has ~64 — those are *authoring/engine* tools; they live in the Loom or behind the data, not on screen).

**Permanent chrome — only this, in every `.oradio`:**
- **Theme (fullscreen): the ribbon.** Loads the `.oradio`'s theme pack **from the club**, runs the
  seamless-transition state machine, and mutates via runtime filters/blends over the base clips, reacting
  to the simulation. (The Loom generates theme packs.)
- **Bottom bar:** **subtitles** (the captioned voice — *indispensable*) + **simulation transport**
  (play / pause / fast-forward / rewind / speed — generic media controls = the §J time controls).
- Top: minimal / peek-only (station identity, home). **Nothing else is permanent.**

**Everything else surfaces as bubbles — never windows.** (The transient-evidence surfaces, made concrete.)
- A transient window is a **Loom-authored template** (an HTML/CSS page filled with expected antenna data,
  triggered by thresholds).
- It announces itself as a **soft, gradual fade-in notification** — never an interrupt.
- Click it → the content surfaces **in a bubble** (rounded, floating, organic — *not* a chrome window).
- **Pin** it (persists) · **pop** it (closes) · or **let it fade** (auto-dismiss if ignored).

**The ribbon is the single feedback surface — it reacts to everything (locked).** Two sources, one grammar:

| Event | Ribbon reaction |
|---|---|
| play | flow resumes / accelerates |
| pause | holds / suspends |
| fast-forward | surges, ripples forward |
| rewind | reverses / echoes |
| speed up / down | the tempo of the flow |
| notification appears | a gentle glint toward it |
| bubble pinned | a sustained eddy / glow near it |
| bubble popped | ripple-out / dissipate |
| notification / bubble fades | settles back to baseline |
| sim heat / topic shift | the transition-grammar reactions (visual = audio) |

So the ribbon expresses **both what the world does and what you do** — one visual grammar for the
simulation *and* the interaction. **Contract:** the `.oradio` declares its **theme pack** (club-resolved)
+ its **transient-window templates** + their **trigger thresholds**; the player provides the fixed chrome,
the bubble system, and the ribbon-reaction grammar.

## Clips as visual genomes

Treat the 20 Sora ribbon loops not as video files but as **visual genomes**:

> ribbon genome = **base clip + metadata + effect-parameter ranges + transition rules**

Metadata: mood · intensity · color family · motion direction · loop compatibility · entrance frame ·
exit frame · heat range · subject fit · station compatibility · transition class. The runtime
**selects, mutates, chains, and grades** them. The frame-matched transition system (end-on-last-frame →
start-on-first-frame, forward + backward clip per edge) is the skeleton; already built in a working
**Godot** project + structured a year ago.

## How procedural *is* it — the answer (a 3-tier ladder)

The key reframe (yours, and it's correct): **don't regenerate video — run a non-destructive effect
stack over the base clips at runtime.** Tenability is high.

**Tier 1 — Effect-stack expression (cheap, do this first).** Never touch the MP4 on disk; play it as a
texture and run a **shader stack** over it each frame, params driven by live state. The whole list is
just fragment-shader params: hue shift · glow/bloom · blur · speed/frame-offset · mirror/direction ·
contrast curve · chromatic aberration · vignette · scanline/noise · particle/mask overlay ·
audio-reactive pulse · topic/heat color wash. "Subtly morph over time, like a real substance" =
slow-drift the params with LFOs/noise so even a looped clip is never static. **Non-destructive ⇒ you can
stack modifications forever without quality loss** — the same 20 clips become an unbounded expression
space. Do-it-once ⇒ do-it-twice ⇒ infinite, exactly as you said.

**Tier 2 — Procedural ribbon *simulation* (medium, later, and the spicy one).** Render the ribbon as a
real-time GPU sim — curl-noise / flow-field / particle-strip — **driven by the actual signal data.**
Then the ribbon isn't a video of a living system; it **is** one. This is the "particle accelerator based
on our systems" you mused about: the visual is literally the data flowing. Bigger rabbit hole, but it's
the purest expression of the whole thesis.

**Tier 3 — New AI base clips (when a tool exists).** Sora is gone; you don't need it for a long time
(Tier 1 multiplies what you have). When you want fresh base material: Runway/Kling/Luma/Pika or open
models (Wan/LTX/Mochi/CogVideoX). Optional.

**Genome fixation (the ATL parallel):** Tier-1 params are the *live, evolving* genome; when an
expression is great, **bake it** to a new clip — a *fixated* genome you can version. Live = param space;
bake = snapshot a winner. Same move ATL makes with strategies. Later, **League grades ribbon genomes**
(which clip+params+rules fit which station state) — see soft spot below.

## Architecture implication (the engine question)

- **Presence layer = Godot.** Shaders, video textures, particles, gamepad/wheel input,
  fullscreen/windowed, boot sequences — Godot is built for exactly this, and you already have a working
  ribbon project. **Resurrect/extend it as the RibbonOS shell.** (Tkinter, the current `oradio_runtime`
  fork, is the wrong tool for the *face*; it stays fine for headless/dev.)
- **Systems backend = Python (unchanged).** Antennas, `signal_heat`, `broadcast_grammar`, the club,
  resolvers, `.oradio` — all stay Python and **feed** the Godot front over a simple local protocol
  (the runtime already streams events via subprocess/stdout/JSON; promote that to a socket).
- **`.oradio` becomes a *package type*, not the brand.** It now also carries **ribbon-genome metadata**
  (which clips/params/transition-class the station expresses) alongside its antenna/voice/spec.
- **Already in hand:** the kernel has rendered manifest backgrounds as **mp4/gif** since the
  `bookmark.py` days — that hook was always for *this exact clip sequence*. The carousel exists
  (`shell_bookmark` Station Browser). The transition grammar exists (audio side). RibbonOS is the
  **re-frame + the Godot face**, not a from-scratch build.

### The two halves, observed (2026-06-12 screenshots)

Two screenshots side by side *are* the thesis:

- **Godot RibbonOS** — striking face, **empty guts.** The rainbow ribbon under a clean carousel +
  taskbar. A beautiful body with no pulse yet.
- **bookmark.py / Algotrading League FM** — dated face (dev-ribbon, MDI windows), **rich guts.** A live
  HOST **transcript** narrating ATL's ML research in real prose; a **FutureSight** widget
  (PLAY/PAUSE/STOP over timed items); a Flows/production window. A working engine in a 1999 coat.

**Combine them: ribbon = feeling/face (Godot) · bookmark = truth/engine (Python).** Two already-built
seeds confirm we're not theorizing: **FutureSight is the §J time-substrate already as a widget**, and
the **live ATL transcript proves the voice/engine is real.** The window→surface mapping is ~1:1:
Transcript → the captioned voice surface over the ribbon · FutureSight → a transient prediction surface
· Flows → Loom authoring (hidden from the listener) · and **bookmark's mp4/gif background hook was the
low-fidelity ribbon all along** (Godot is the hi-fi version of the same wiring). The seam: bookmark
(headless) emits the streams — narration, transcript, FutureSight state, heat — and Godot renders them
as ribbon + transient surfaces + carousel + transport. *One event stream between two principles.*

## Soft spots / open questions (kept honest)

1. **Grading visual "feel" needs a proxy.** League can grade strategies on P&L and universes on
   evidence; "which ribbon feels best" is subjective. To let League judge ribbon genomes you need a
   signal — dwell time, pins, A/B, or a learned aesthetic score. The *machinery* (mutate/select/grade)
   transfers; the *fitness function* is the open design problem.
2. **Window vs fullscreen** — immersive app window with optional fullscreen is the lean; confirm.
3. **Godot ↔ Python protocol** — local socket with a JSON event stream (heat, transitions, station
   state) is the obvious seam; design it small.
4. **Tier-2 vs Tier-1 first** — Tier 1 (effect stack on the 20 clips) is the cheap proof; Tier 2
   (data-driven sim) is the destination.

## One-line definition

> **RibbonOS is a living launcher for systems that turns state changes into visual and spoken
> transitions.** You open it to enter something that is alive.
