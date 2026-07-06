# Handoff — Runtime Fork & Repaint (next), + Meta-Plugin upgrade (after)

> For the next coder. You have some context; this gets you current and scoped. **Read the two
> intent docs first**, they are the source of truth:
> - `docs/NARRATIVE_WORLD_RUNTIME_VISION.md` — the *what/why* (esp. the sections: **"The .oradio
>   Runtime — an Ambient Desktop Shell"**, **"The player chrome"**, **"The cohesive aesthetic already
>   exists — unify the runtime onto it"**, and **"Scouts, Signal Heat, and Ambient Source Activation"**).
> - `docs/RADIO_OS_STUDIO_PLAN.md` — the build plan.

## Hard rules (do not violate)

1. **PRESERVATION — fork, don't erode.** `bookmark.py`, `shell_bookmark.py`, the webserver, and the
   audio CLI are load-bearing and **preserved**. Advance by **duplicate-and-continue**: create a NEW
   player file and build there. **Never edit `bookmark.py` / `shell_bookmark.py` in place.**
2. **Live LLM is the medium, not a fallback.** A station is either tuned in or getting tuned in; never
   "fake radio." First open on a PC = one-time Tune-In (provisioning), saved machine-level.
3. **The runtime OBSERVES and NARRATES; it never impersonates a source.** (Matters for harness feeds.)

## Where things stand (so you're current)

The **ungated `.oradio` spine + Scout pipeline is built and test-netted (41 named tests)**:

- **Authoring**: `radio_os_studio.py` (Tkinter Studio). Tabs: Source · **Feeds & Cast** · Meaning ·
  Production · Simulator · .oradio. It now surfaces the whole antenna roster via static discovery
  (`discover_feed_plugins`), multi-feed + characters + mix authoring, LLM Tune-In, production rules,
  SFX, and `.oradio` export (contract + bundle + lockfile).
- **Spine**: `provisioning.py` (Tune-In / club membership), `oradio_resolver.py` (bundled→cache→
  provisioned readiness ladder + integrity gate), `oradio_player.py` (player entry: `--shell`,
  `--tune-in`, Windows `.oradio` association, `build_launch_env`), `oradio_player_ui.py` (new
  charcoal/cyan ambient shell fork), `sfx_sourcing.py` (Freesound).
- **Scouts**: `plugins/antenna_bridge.py` (reads `RadioOSBridge/<source>/` → emits raw typed
  observations) + `shim_generator.py` (generates lightweight scoped scout shims: file_watch / log_tail
  / command_output / udp_listen). Contract: a shim appends JSON observations to `events.jsonl` (or
  writes `state.json`); the antenna reads them. **Two ends of one pipe, proven by test.**
- **Tests**: run **named files** (`python -m pytest tests/test_oradio_contract.py tests/test_antenna_bridge.py tests/test_shim_generator.py -q`).
  ⚠️ Do **not** run `pytest tests/` (whole dir) — `tests/test_neikos_sim.py` calls `sys.exit()` at
  import and crashes collection (pre-existing, not ours, don't fix unless asked).

---

## TASK 1 (in progress): Fork & repaint the `.oradio` runtime

Current progress:

- Added `oradio_player_ui.py` as the first forked listener shell. It is a new file and does not edit
  `bookmark.py` or `shell_bookmark.py`.
- `oradio_player.py --shell <station.oradio>` opens the shell. Windows file association now points to
  `oradio_player.py --shell "%1"`.
- The shell resolves the `.oradio`, shows a Tune-In gate when not ready, then launches `bookmark.py`
  headlessly underneath when ready.
- The shell uses the carousel palette language (charcoal/cyan), top toolbar, media transport row,
  center ambient surfaces, surface palette, closable transient surface cards, and bottom HOST subtitle
  row.
- The shell inherits basic manifest art (`art.global_bg`, `art.panels.toolbar`, `art.panels.subtitle`,
  `art.accent`) before window construction.
- It includes an audio-pipe watcher for emitted WAV segments.
- Runtime log, audio pipe, and signal-monitor surfaces are now live-updating from kernel/audio events
  instead of static placeholder cards.
- Transport row now exposes honest controls supported by the preserved kernel seam: Play, Stop,
  Restart, Open Audio Pipe, and Spawn Surface. Pause/Rewind/Forward remain present but disabled until
  an external kernel command exists; no fake controls.

Still next:

- Deepen theme inheritance for wallpapers/images/GIFs/MP4s from the preserved theme system.
- Continue subtracting authoring/workbench concepts from listener runtime.

Create a **new** player UI (e.g. `oradio_player_ui.py`, or fork `bookmark.py` into a new file) — never
edit `bookmark.py`. Goal: the runtime should *feel designed by whoever designed the carousel.*

### The problem (eyes-on)
Two aesthetics live in one app. **Keep**: the Station Browser carousel + the "Visual Surface" runtime
shell in `shell_bookmark.py` (charcoal, soft vignette, rounded cards, single cyan accent, clean
toolbar). **Replace**: the current `bookmark.py` runtime window — full-bleed green→red gradient (it's
the **monokai** theme), MDI `◆ Window 1/2/3` frames, a multicolor dev-ribbon toolbar (`Panel▾ /
Widget▾ / Add Widget / Save·Load·Reset Layout / Prompts`), widget-from-dropdown → numbered-window
mechanic. It reads Windows-XP/1990s; target is clean 2026.

### What to build (keep the shape, modernize the skin, streamline the runtime)
- **One palette** — adopt the carousel's charcoal + cyan across the runtime; drop the gradient.
- **Chrome layout** (preserve & extend): **top toolbar** · a **NEW media-transport row** (play · pause ·
  ff · rewind, iTunes/Spotify style — we're audio) · **bottom subtitle row** (the spoken HOST line,
  captioned — keep it) · **center stage** = the ambient windows.
- **Ambient windows, not MDI** — replace `Window 1/2/3` + `Panel:[1] Widget:[x]→Add` with surfaces you
  **spawn and close willy-nilly** (start with one; add freely; let them wash away). These are the
  "transient evidence surfaces" from the vision doc.
- **Widget palette, not a dropdown** — pick widgets from a card-style picker in the carousel's language.
- **Inherit the existing theme system, don't rebuild it.** `bookmark.py` already has a full Theme
  Editor (`_open_theme_editor_impl` ~L3679: colors, gradients, **wallpapers incl images/GIFs/MP4s**,
  live preview), a threaded `ui_theme` palette dict, named schemes, and **manifest-driven themes applied
  on startup** (~L3671) — so each `.oradio` already carries its own look (incl desktop background).
  Per-station customization is an ASSET to inherit and harden, not a gap.
- **Wire the spine**: on open, run `oradio_resolver.resolve_station(...)`; if not ready, show the
  first-run **Tune-In** gate (reuse `provisioning.py` / `oradio_player.tune_in_membership`); else launch
  with the resolver's launch env. The artifact must run standalone (no `radioos.exe` dependency).

### Net framing
The runtime is mostly **subtraction + promotion + consistent theming**: hide the authoring workbench
(authoring belongs in the Studio), promote the scattered audio controls (Radio Dial VOL/SPEED,
FutureSight PLAY/PAUSE/STOP) into the **one** media-transport row, and turn `Window 1/2/3` into
runtime-selected ambient surfaces. Keep: the big captioned HOST subtitle, live LLM narration,
manifest-driven theme/wallpaper.

---

## TASK 2 (after the fork — capture now): Meta-Plugin generator/editor upgrade

The meta-plugin is the **narrative tissue** — *not the data, but how the LLM talks about whatever comes
through*: panel vs single author, tone, what it surfaces, what stories it weaves. The contract is
already defined and battle-tested: `plugins/meta/generated.py` (`generate_meta_plugin_spec(signature,
station)` drafts an editable `meta_plugin_spec.json`; `GeneratedMetaPlugin` reads it →
`curate_candidates` + `generate_script`). The Studio "Meaning" tab edits that spec today.

**Why it gets much better now:** we have **standardized data intake**. Everything an antenna emits — incl
the new scout/bridge path — is now a normalized candidate/observation with a stable shape
(`{post_id, source, title, body, priority, ts, type, tags}`). Because intake is uniform, the
meta-plugin generator/editor no longer has to be hand-authored per source. The Studio "Meaning" tab can
become a **near-UI authoring surface** for narrative *characteristics* (host persona(s), panel vs solo,
tone, which observation `type`s get airtime and how loud, pacing, interrupt rules) instead of raw JSON.

The question the whole architecture now collapses to — and it should drive this UI:
> *"The meta-plugin talks about whatever comes through your antennas. **How do you want it to talk
> about it?**"*

Design notes:
- Drive the generator off the **observation `type` vocabulary** + the antenna **signature**, not
  per-source special-casing.
- This is where **signal heat** authoring lives (per-source loudness, interrupt thresholds, quiet
  floors) — see the vision doc's "Scouts, Signal Heat" section. Keep the antenna dumb; the meta-plugin
  is where "what's worth airtime" is shaped.
- Keep deterministic templates strictly as a **creator-side authoring scaffold** (preview without
  burning LLM calls) — never the shipped listener experience.

---

## Key file map
- `bookmark.py` — kernel/runtime (PRESERVED). Theme editor ~L3679, manifest themes ~L3671.
- `shell_bookmark.py` — library/carousel (PRESERVED, the keeper aesthetic). Wizard `StationWizard`
  ~L3514, `discover_plugins` ~L270, settings ~L1636.
- `radio_os_studio.py` — the Studio (active build file; safe to edit).
- `oradio_resolver.py` / `oradio_player.py` / `provisioning.py` / `sfx_sourcing.py` / `shim_generator.py`.
- `plugins/antenna_http.py`, `plugins/antenna_bridge.py`, `plugins/meta/generated.py`.
- `docs/NARRATIVE_WORLD_RUNTIME_VISION.md`, `docs/RADIO_OS_STUDIO_PLAN.md`.
