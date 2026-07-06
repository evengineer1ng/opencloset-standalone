# loom ↔ Night City Motorsports — the bridge

Open a web page, start a race in Cyberpunk. The page controls **state** (open / stage /
green-flag / abort) and shows a live read-only snapshot (grid, results, standings, paddock).
It never touches game controls — the sim is Cyberpunk; this is the iRacing-style UI + forum
around it.

```
browser (ncm.html)  ──HTTP──▶  ncm_bridge.py  ──files──▶  loom_bridge.lua  ──▶  MTE.Racing.*
        ▲                          (local relay)            (CET, in-game)
        └──────────── state.json ◀── snapshot ◀─────────────────┘
```

The browser is sandboxed (can't poke a process or the CET folder; a Pages https page can't
hit localhost). So the relay is the only thing in the middle, and the seam is two files in
`<mod>/bridge/`: `command.txt` (page → game) and `state.json` (game → page).

## Two modes, one page
- **REPLAY** — `docs/ncm.html` on its own (e.g. GitHub Pages). A shareable, read-only portal
  into a baked deterministic season. No game required.
- **LIVE** — run the relay with `--mod` pointed at a running game. The page lights up and the
  buttons command the race.

## Install (LIVE)
1. Copy `loom_bridge.lua` into the mod's modules folder:
   `…/cyber_engine_tweaks/mods/MT_Ecosystem/modules/loom_bridge.lua`
2. Add three lines to `MT_Ecosystem/init.lua`:
   - with the other `require`s: `local LoomBridge = require("modules/loom_bridge")`
   - in the `MTE = { … }` table: `LoomBridge = LoomBridge,`
   - at the end of the `onInit` callback: `LoomBridge.init(MTE)`
   - inside the `onUpdate` callback: `if LoomBridge and LoomBridge.update then LoomBridge.update(dt) end`
3. Run the relay (stdlib Python, no deps):
   ```sh
   python integrations/ncm/ncm_bridge.py --mod "C:/…/cyber_engine_tweaks/mods/MT_Ecosystem"
   ```
   (or set `NCM_MOD_DIR` instead of `--mod`)
4. Open <http://localhost:8777>, launch Cyberpunk with the mod, and drive Race Control.

## The dispatch (what each button calls)
`open`→`Racing.openQuickRaceConfig()` · `stage`→`Racing.stageNow()` ·
`start`→`Racing.startRace()` · `quali`→`Racing.startQuali()` · `abort`→`Racing.abort()`.
All `pcall`-guarded; the snapshot reads `getState` / `getRaceEntries` / `getResults` /
`Championships.driverStandings`.

## Honest edges
- **Bridge dir path.** `loom_bridge.lua`'s `Bridge.dir` is relative to CET's working dir
  (the game's `bin/x64`). If your layout differs, set `Bridge.dir` before `init()`. The relay
  creates the folder; the Lua only reads/writes the two files in it.
- **Staging is physical.** `startRace()` still requires the player parked/staged and the AI
  grid formed — that's intrinsic to the game. The page mirrors the state machine and offers the
  next legal action; it doesn't teleport past the sim's own preconditions.
- **Verify on your machine.** The relay round-trip is tested; the in-game call path is
  `luac`-valid and uses the confirmed `MTE.Racing` API, but the live dispatch wants one run on
  your rig to confirm CET's io path + staging flow.
