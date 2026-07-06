# Radio OS — Narrative World Runtime Vision

> **This document is a statement of product intent, not a feature request.**
>
> Architecture, file formats, and UI will evolve. This intent should not. Its job is to keep
> future implementation decisions aligned with what Radio OS is actually trying to become.
> When an implementation choice conflicts with this document, treat that as a signal to
> re-examine the choice — not the intent.
>
> The implementation companion to this doc is `RADIO_OS_STUDIO_PLAN.md` (the build plan) and
> the code in `radio_os_studio.py` / `bookmark.py`. Those describe *how*. This describes *what*
> and *why*, and is allowed to outlive any of them.

---

## What Radio OS Is

Radio OS is **a runtime for persistent narrated worlds.**

It is not an AI radio player. It is not a dashboard. It is not an assistant. It is not a chatbot.

A **station** is a living world connected to one or more data sources.

- The station **observes** those worlds through **antennas**.
- The station **narrates** those worlds through **hosts**.
- The station **visualizes** those worlds through **transient evidence surfaces**.
- The station **continues to exist** whether the user is actively watching or not.

---

## Core Philosophy

Most software assumes: *"The user continuously monitors the system."*

Radio OS assumes: *"The user periodically checks into a living world."*

The world keeps moving. The station notices what matters. The station tells the story. The
user rejoins whenever they choose.

---

## The Antenna — the source of truth

The antenna is the most important component in the system.

Its purpose is **not to retrieve data**. Its purpose is to **transform moving systems into
narratable events.**

An antenna should be able to observe: APIs, RSS feeds, databases, files, folders, logs, local
telemetry, WebSocket streams, simulations, games, sports feeds, calendars, sensors — and source
types that don't exist yet.

The antenna **normalizes every observation into events.** Everything downstream operates on
normalized event structures. The station should not care whether an event came from NHL, ATL,
weather, Bluetooth, a simulator, a file watcher, a local PC, or a smart home.

**The antenna hides source complexity. The station consumes events.**

---

## The Role of AI — interpret reality, never invent it

The antenna is the source of truth. **AI is not.**

AI's job is interpretation, not fabrication:

- event ranking and prioritization
- event summarization and compression
- narration and recap generation
- conversational interpretation

The ideal question is not *"What happened?"* The ideal answer is *"Here is what matters."*

AI should interpret reality. AI should never invent reality.

---

## The Medium Is Live LLM Narration

Radio OS is **live, LLM-narrated radio.** This is not negotiable and not a tier. The magic depends
on an LLM being **fed data so well that it articulates cleanly and does not hallucinate** — which is
exactly why a strong meta-plugin over a good feed makes a station far stronger than a weak one. The
craft is in *how the model is fed*; the audio-production layer and the Studio exist to make the
result **inhabitable.**

A station **never silently downgrades** into something simpler. Deterministic, bucket-classified
text is too thin to carry an ambient show — it is **not a fallback.** It isn't forbidden as dark
magic either; it appears only in two honest roles:

1. a **creator-side authoring scaffold** — iterate the spec offline without burning calls; never
   shipped, never heard by a listener;
2. **scripted TTS interstitials *between* LLM segments** — station IDs, time checks, bumpers,
   transitions — deterministic *because determinism is appropriate there*: production seasoning
   around the live narration, never the narration itself.

The LLM is therefore a **first-run requirement**, handled as **club membership.** The first time you
open a `.oradio` on a PC, you set your provider up once (point at Ollama and pull the model, or paste
an API key and validate); it's saved at the **machine level**, and every future station is already
tuned in. Do it once, you're in the club. A station is either tuned in or it's getting tuned in —
it is never fake radio.

---

## The Host Model — understand broadly, respond narrowly

The station behaves like a **host** — not an assistant, search engine, or generic chatbot.

A host understands broad conversation but responds through the lens of the station. The
mechanism is **contextualize, not redirect.** The host does not refuse off-topic input and it
does not change the subject; it acknowledges the input, recognizes it sits outside the
station's world, preserves the conversational flow, and re-anchors to station reality.

> **User:** "Did you see that basketball game last night?"
>
> **ATLFM:** "The league office doesn't track basketball results — but it does track
> competition. Speaking of which, the Turners moved into second place after yesterday's session."

The mechanism being demonstrated:

1. Understand the input.
2. Recognize it is outside the station's scope.
3. Preserve the conversational flow (don't refuse, don't deflect coldly).
4. Re-anchor to the station's reality.

The host is not refusing conversation. The host is **maintaining identity.**

---

## Roles — who makes and uses a station

A station is not "the creator's" station in any single sense. There are distinct roles, and
**Radio OS must not assume they are the same person:**

```text
Radio OS creator     (builds the engine — e.g. the author of Radio OS itself)
    ↓
Station creator      (authors a station in Studio — anyone using Radio OS Studio)
    ↓
Station operator     (configures/runs a station; chooses its world, weights, priorities)
    ↓
Station listener     (tunes in)
```

Sometimes one person fills all four roles. Sometimes they are four different people. Anyone who
uses Radio OS Studio is a station creator; creation is not exclusive.

A station therefore **reflects the interests, priorities, and world chosen by its operator** —
not "the creator" in the abstract.

---

## Personal / Life Stations

A station can be composed of **multiple worlds**: sports, weather, projects, trading, calendars,
local telemetry, games, simulations, news, music. The operator assigns weights and priorities:

```text
40% trading
20% sports
15% weather
10% local system telemetry
10% news
 5% music
```

The station becomes a personalized ambient channel — not because it reflects its author, but
because it reflects **its operator.**

---

## Scouts, Signal Heat, and Ambient Source Activation

The life-station is not a set of categories you switch between. It is a standing field of **scopes
you have consented to**, and narration follows **signal heat** wherever it rises. You tune once; the
world drives the mix.

### A source is anywhere you point a scout

The shim/probe collapses the old distinction between "a source Radio OS supports" and "anything
observable." A **scout** (a generated, co-located shim) is placed by the operator into a scope —
*this folder, this log, this file type, this process output, this telemetry source* — and reports
what it sees. A source is no longer a thing Radio OS implemented; it is a scope you aimed an antenna
at. Coverage becomes effectively unbounded: ten scouts, a hundred scouts — a tapestry.

> **Anything that changes can become an event.**

That does not mean everything deserves narration. It means everything observable enters the **event
economy**, and Radio OS decides what matters.

### Observation ≠ event ≠ airtime — three separate jobs

A code-folder scout must never narrate "file changed, file changed, file changed." It emits **raw,
typed observations** — `file_changed`, `test_failed`, `test_passed`, `dependency_changed`,
`large_diff_detected`, `branch_changed`, `error_log_updated`, `build_output_changed`,
`agent_summary_written` — and the **station** decides whether any of it is worth airtime right now.
**The antenna observes; the orchestrator ranks; the host narrates what matters.** The scout stays
dumb and honest — it never editorializes.

### Signal heat — the mix is emergent, not switched

There are no mode switches. The world changes the mix:

```text
Cyberpunk telemetry hot for 3 days   → Cyberpunk dominates narration
Cyberpunk closes                     → station falls back to low-intensity ambient sources
Code directory starts changing       → the coding source heats up
Tests fail / files churn / agent output appears → the coding storyline gets airtime
```

Heat is a **time-decayed measure of a source's activity**. A source that just lit up earns airtime; a
source that went quiet recedes. **Silence is the default, valid state** — most scouts are quiet most
of the time, and "online but no events" is the most common reading when you drop a probe into a
random folder. The station must be graceful about dead air and weak signal, never filling it with
noise.

### The killer behavior: you do not bend your routine

You don't ask the station to care; you tune it once, then it listens. Play Cyberpunk for three days
and it narrates Cyberpunk. Close it, take a break, and it narrates the dead time with whatever is
available. Open your editor and start coding — a folder you long ago aimed a scout at — and the same
station you've had on for days **shifts gears on its own** and starts talking about the code you're
changing. No mode switch, no command, no computer vision, no spyware — just a scope you decided
mattered, lighting up. You live your life; the configured world that goes hot speaks.

### Consent and scope — this is not surveillance

A scout is **deliberately placed and explicitly scoped** by the operator. It listens only to the
folder/log/file-type/process/telemetry it was pointed at — "I tuned an antenna to this project," not
"watch my whole computer." This boundary is a **hard product rule**: the shim generator templates
*safe, scoped, consented* transports first (log-tail, file-watch, telemetry, mod APIs); broad or
invasive capture is never the default.

### What this makes Studio

This is the clarifying answer to *what Studio is for*. Studio is the **tuning bench** for a standing
relationship with many observable worlds. You author:

- **what worlds matter** to this station, and **where the scouts are** (scopes);
- **what counts as signal** (which observation types, and how hot they run);
- **how loud each world is**, when it may **interrupt**, and when it should **stay quiet**.

Tune once; live your life; the hot world speaks. (Use cases like a coding station *prove* this
generic model — they are not separate products to be shoehorned in.)

## World Continuity

The defining behavior of Radio OS is **persistence**: the world continues while the user is away;
the station remains.

When the user returns, the intent is **resume immediately and backfill only what matters** — NOT
reconstruct everything since they left. Precisely:

1. Resume live operation immediately.
2. Establish current state.
3. Surface the most important missed developments.
4. Continue forward.

The system must **not block startup while reconstructing history**, and must **not require a full
replay**. Present awareness comes first; retrospective awareness comes second and can be
**backfilled in the background** ("oh — and while you were gone, this also happened"). The goal is
narrative continuity, not alert spam, and not a long "regenerate everything" stall.

> Station starts:
>
> "Good evening. While you were away, Timmy closed two positions and moved into second place.
> Current leader remains Cosmo & Wanda. The next shift begins in twenty minutes."
>
> Then it just keeps moving.

---

## Transient Evidence Surfaces

Radio OS should **not** become a permanent dashboard platform. Permanent dashboards belong to the
**source** systems (ATL, MoCo, and other applications).

Radio OS instead displays **transient evidence surfaces**: temporary visual organisms that appear
when relevant and disappear when no longer relevant. They are **visual interpretations of events**,
not the source of truth. Examples: standings changes, contract offers, weather alerts, hypothesis
activations, game events, anomaly reports, league briefings, research discoveries.

---

## Narrative Decision Points

Some surfaces are informational. Some are **interactive** — narrative decision points such as
`Accept` / `Reject` / `Negotiate` / `Delegate`.

- If the user responds, apply the choice.
- If the user ignores the surface, the world continues, the station continues, the simulation
  continues.

The goal is **not to pause reality** — it is to allow **participation** in reality. This is
per-station: an operator who wants to build a game or interactive content *inside* their station
can do so through Studio, without leaving the station's world.

---

## Radio OS Studio — an authoring environment, not a dashboard builder

**We are not building a dashboard builder.**

Radio OS Studio is a **broadcast production studio for persistent worlds.** A station creator
isn't designing screens — they're designing **moments, interruptions, briefings, alerts,
overlays, interactions, and broadcasts.** The screens are merely the delivery mechanism.

Studio is not VS Code and is not a programming environment. It is an **authoring** environment. A
creator should be able to author — through visual tools wherever possible — :

- hosts, voices, and audio behavior
- antennas and event rules
- priorities and weights
- transient surfaces and interactive decision points
- visual themes

Advanced coding should remain **possible**. It should never be **required.**

### Transient Surface Designer (drag-and-drop — concrete, even if hard)

A creator builds transient surfaces visually. Example workflow:

1. Create surface.
2. Choose layout.
3. Drag text regions.
4. Drag image regions.
5. Drag charts.
6. Drag buttons.
7. Define the expected data fields.
8. Define animations.
9. Define expiration / priority behavior.
10. Save the template.

```text
Standings Card
Fields:  team_name · rank · change · score
Behavior: slide in · remain 20s · fade out
```

The creator should not hand-author HTML/CSS for common cases. Templates are **data-driven**: the
**AI fills slots, it does not generate arbitrary applications.** The runtime selects a template
based on event compatibility:

- **Templates are visual genomes.**
- **Telemetry is the environment.**
- **The runtime selects the best visual organism for the moment.**

---

## Radio OS Is the Whole — not any one part

"What is Radio OS?" resolves to: **all of it.** No single surface *is* Radio OS; the identity is
the union of two signature experiences and the surfaces that deliver them.

**Two signature experiences, equally first-class:**

- **Listen.** Open a `.oradio` and just *receive* — immerse in what an author tuned for you. This
  needs nothing but the artifact.
- **Create.** Open the Studio and *author* a `.oradio` yourself — antenna, host, surfaces,
  production. Full creative immersion.

Neither is "the" experience. Radio OS is the relationship between them.

**The surfaces that carry it (all real today, none canonical on its own):**

- **Native app** — download `radioos.exe`, launch, and you get **Studio on one side, a local vault
  of `.oradio` stations on the other**. Author locally; play locally.
- **Web platform** — the same thing in a browser: accounts, Studio-in-browser, a hosted vault of
  your stations, sharing/discovery. The point is **fostering creativity**, not a dashboard. (The
  existing web app is rough but real — "broken, not broken broken" — with a lot of good in it.)
- **The standalone `.oradio` artifact** — ships to run **without `radioos.exe`** (per the
  artifact-independence rule). A station handed to someone who never installed Radio OS still plays.
- **Audio CLI + headless** — first-class Radio OS, not afterthoughts. A station can live with no
  screen at all — pure ambient narration. (Both already exist and are exercised.)

Streamlining "who Radio OS really is" across these surfaces is the open work. The pieces are real
today; the through-line is what we're tightening — in a **move-fast, break-fast, learn,
get-back-up-fast** phase. Bias toward strides over polish.

## Preservation Rule — duplicate and continue, never edit in place

The existing artifacts are **load-bearing and preserved**, not scaffolding to be overwritten:

- **`shell_bookmark.py`** — the library / iTunes-style manager: springboards `.oradio`s, carousel,
  drives the audio CLI, turns the server on/off, settings for API integrations, themes.
- **`bookmark.py`** — the `.oradio` kernel/runtime.
- the **webserver**, the **audio CLI**, and the other proven foundations.

These are ripe for **aggressive** upgrades toward the desktop-app vision — but the way we get there
is **duplicate-and-continue**: fork into a new file/surface and build the new vision there. We do
**not** modify `shell_bookmark.py`, `bookmark.py`, or the other important existing files in place.
Preserve the heck out of what works; evolve on copies.

## The `.oradio` Runtime — an Ambient Desktop Shell (the target for the forked runtime)

The runtime that plays an `.oradio` should feel like **a small, ambient desktop shell** — like
you're running a tiny OS tuned to one world. Keep the desktop-shell theming; shed the Windows-XP
heaviness for something clean and modern. Lean *into* the windows-and-widgets metaphor, not away.

The defining behavior is **ambience, not arrangement**:

- It is **not** a picture-perfect OS painting to be preserved.
- Start with **one** window. Add as many as you want (within reason). **Delete them willy-nilly.
  Let them wash away.**
- The shell keeps up **in tandem and autonomously** with whatever the antenna is tuned to.

> A `.oradio` station is a **scratchboard that scratches itself** — and talks to you about whatever
> its antenna is tuned to.

Windows here are **transient evidence surfaces** with a size dial: they surface **small** when
they're a passing note, and **maximize big** when a moment deserves the whole screen. Widgets and
windows are the delivery mechanism for the narrated world — born when relevant, dismissed freely,
never sacred. This is the target the *duplicated* runtime grows toward — the current `bookmark.py`
stays as-is underneath until the fork proves out.

### The player chrome — modernize the shape, don't redraw it

`bookmark.py` already has the **majority of the UI/UX figured out**; the fork's job is to
**modernize the skin and streamline the runtime, not reinvent the layout.** Today it reads
Windows-XP / late-90s — it should read clean 2026. (That dated look is almost a vibe, which is part
of why we **fork rather than erode** what works.) The proven shape is preserved and extended:

- **Top row: toolbar.**
- **New row — media transport:** play / pause / fast-forward / rewind / skip, iTunes/Spotify style.
  Radio OS is audio; it should *feel* like a player.
- **Bottom row: subtitles** — the spoken narration, captioned.
- **Center: ambient windows/widgets** — opened and closed *willy-nilly* (not three static windows);
  they wash in and out with the world. Streamlining the runtime toward that is part of the fork.

### Customization is a feature, not a curiosity

Stations are inhabitable. A `.oradio` can carry its own look — even a **desktop background** for the
station — and we want to **harden and expand** that customization surface, not treat it as a novelty.

### Two descendants: the Studio vs the Library

There are two distinct descendants, and they are different products:

- the **Studio** (`radio_os_studio.py`) **authors** stations;
- the **Library / Vault / "Radio OS Player"** — the descendant of `shell_bookmark.py` —
  **catalogues and switches between `.oradio` stations at will.**

The end-to-end experience lives inside the `.oradio` itself; the Library is how you collect and move
between them. (Both are forks/descendants — the originals stay preserved.)

### Eyes-on direction (from the v1.06 screenshots)

Observed: the **Library** screens — the Station Browser/carousel and the Visual Surface shell
(`shell_bookmark.py`) — already read as a finished product (one charcoal palette, single cyan accent,
art-forward uniform cards, calm top bar). The **in-play runtime** (`bookmark.py`) does not yet — it
wears its plumbing on the outside. The fork closes that gap, and it is mostly **subtraction, promotion,
and consistent theming**, not redrawing.

**Hook onto the theme system that already exists — don't reinvent it.** `bookmark.py` already has a
full theme engine: an Advanced **Theme Editor** (colors, gradients, **wallpapers incl. images/GIFs/MP4s**,
live previews; `bookmark.py:_open_theme_editor_impl`), a threaded `ui_theme` palette dict, **named
schemes** (the runtime's green is monokai), and themes **applied from the manifest on startup**
(`bookmark.py:3671`) — i.e. each `.oradio` already carries its own look. The Library hooks onto this
system with restraint; the runtime applies the *same* monokai as a full-bleed gradient that fights its
multicolored dev-buttons. So **per-station customization is an asset to inherit, not a gap to build** —
the fork gets palettes, gradients, and wallpapers for free; the work is applying them cohesively.

The other fork moves (mostly subtraction/promotion):

- **Hide the workbench.** The dense dev-ribbon (Panel ▾ / Widget ▾ / Add Widget / Save·Load·Reset
  Layout / Prompts) is *authoring* and belongs in the **Studio**, not the listener's runtime.
- **Windows → designed transient evidence surfaces.** Replace generic "Window 1/2/3" panels (diamond
  titlebars, engineering tab-strips, raw Offset/Label/Source/Seek fields) with the **visual genomes** the
  runtime *selects and washes in/out* — not "pick a widget from a dropdown, drop it in numbered window N."
  This is the "open/close willy-nilly" streamline made concrete.
- **Scattered transport → one global media row.** Audio transport already exists but is buried inside
  widgets (Window 2 Radio Dial VOLUME/SPEED; Window 3 PLAY/PAUSE/STOP). **Promote** it into the single
  iTunes/Spotify transport row above the subtitles. Promote, don't rebuild.

Keep what already bridges to the vision: the **big captioned HOST subtitle row**, the **live LLM
narration content** itself, the existing audio controls (unified), and the manifest-driven theme/wallpaper
system. Reskin toward the carousel's discipline: one palette + single accent, generous negative space,
clear type hierarchy — all of which the theme system can already supply.

## The Station Is the Artifact

A station is a portable artifact (the `.oradio` direction) that **must not depend on Studio to
run.** Studio authors the organism; the kernel/player runs it. A station is **defined by its
world, not by its technology.**

Long term, anyone can author and combine stations — ATLFM, HockeyFM, WeatherFM, HomeFM,
BackmarkerFM, ResearchFM, ProjectFM — or fold many worlds into one personal channel.

## ATL Is the Benchmark, Not the Target

ATL is the rich, living system used to **prove** Radio OS can tune into something complex and make
it understandable. ATL stays frozen as an honest benchmark; Radio OS listens to it without ATL
needing to know Radio OS exists. Radio OS is the ears; the source systems are the worlds.

---

## Final Definition

**A Radio OS station is a narrated relationship with a world.**

The purpose of the platform is to help people **create, observe, narrate, and interact with**
those worlds.
