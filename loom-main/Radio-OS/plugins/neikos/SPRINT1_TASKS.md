# Neikos Development Roadmap — Sprint 1
# All 10 tasks, sequential, complete each before starting the next.
# Write PROGRESS.md updates after each task.

## Context
- Game backend: plugins/neikos/__init__.py (FastAPI on port 7700)
- Frontend: web/src/tabs/Neikos.svelte, web/src/lib/nkStore.ts, web/src/lib/nkAudio.ts
- CSS tier system: web/src/styles/neikos.css (data-tier="1..5" on .nk-root)
- Build: cd web && npm run build (must pass before marking task done)

---

## TASK 1: Visual Node Map

**File:** `web/src/components/nk/NodeMap.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — replace neighbor list with NodeMap in explore tab

Build an SVG-based node map. Layout algorithm: place nodes using a simple force-directed or radial layout seeded by node_id (deterministic, no animation needed for layout itself).

Requirements:
- Fetch map from nkStore: `$nkMap`, `$nkMapStart`, `$nkPlayerLoc`
- Each node is an SVG circle, colored by node_type using --nk-node-* CSS vars
- Current player location: larger circle, accent border
- Edges: lines between neighbors (dimmed, --nk-border color)
- Clicking a neighbor node calls `nkActions.move(nodeId)` if it's a valid neighbor
- Non-adjacent nodes are visible but not clickable (opacity 0.4)
- Relay nodes get a pulsing glow animation (CSS keyframe)
- Map is scrollable/pannable on touch (use viewBox + pointer events)
- Tier awareness: at tier 3+ show node IDs as labels instead of names; at tier 5 all nodes labeled "SITE [id]"
- Size: fills available width, fixed height 320px

Node type colors (from neikos.css vars): WILD_ZONE=--nk-node-wild, CITY=--nk-node-city, FACILITY=--nk-node-fac, DUNGEON=--nk-node-dung, ANOMALY_ZONE=--nk-node-anom, LANDMARK=--nk-node-land

Layout: for each node, compute position as:
  - Start from start node at center
  - BFS out, each ring at radius += 60px
  - Nodes in same ring distributed evenly around the ring
  - Use node_id hash to add small x/y jitter (±10px) for organic feel
  - Clamp to viewBox bounds

---

## TASK 2: Battle Screen UI

**File:** `web/src/components/nk/BattleScreen.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — show BattleScreen when a battle is in progress (nkScreen === 'battle')

Requirements:
- Two sides: player (left) and opponent (right)
- Each side shows: creature name, type chip, level, HP bar (animated), status effect badge
- Center: "VS" divider, turn counter, field state
- Action buttons: Fight, Switch, Flee (greyed out if $nkBusy)
- After battle resolves, show result panel: WIN/LOSE, rating change, EXP gained
- Turn log: scrollable list of last 8 turns, monospace font
- Audio: call nkAudio.playBattleStart() on mount, nkAudio.playBattleWin() on win
- Tier awareness: at tier 3+ "HP" becomes "INTEGRITY", trainer name becomes "AGENT [id]"
- Battle is triggered by calling nkActions.battle(opponent_id)
- Poll nkEvents for battle_result event to know when battle ended
- Escape/back button returns to explore without fleeing

The backend battle is synchronous — call the command, wait for battle_result event in nkEvents.
Parse battle_result: { winner, player_remaining, opponent_remaining, turns, player_rating, opponent_name }

---

## TASK 3: Radio OS NPC Voice Pipeline

**File:** `plugins/neikos/voice.py`
**Update:** `plugins/neikos/__init__.py` — import and wire up voice pipeline
**Update:** `plugins/neikos/server.py` — add POST /api/speak endpoint

Build the NPC voice system. NPCs speak via Radio OS TTS.

voice.py requirements:
```python
from __future__ import annotations
import threading, queue
from typing import Optional, Dict, Any

# NPC archetype → voice profile mapping
# Maps KnowerArchetype and trainer types to voice_provider identifiers
NPC_VOICE_MAP = {
    "ELDER":       {"speed": 0.85, "pitch": -2,  "style": "wise"},
    "SCIENTIST":   {"speed": 1.0,  "pitch": 0,   "style": "clinical"},
    "REBEL":       {"speed": 1.1,  "pitch": 1,   "style": "terse"},
    "CARTOGRAPHER":{"speed": 0.9,  "pitch": -1,  "style": "precise"},
    "GHOST":       {"speed": 0.75, "pitch": -4,  "style": "haunted"},
    "TRAINER":     {"speed": 1.0,  "pitch": 0,   "style": "neutral"},
    "DEFAULT":     {"speed": 1.0,  "pitch": 0,   "style": "neutral"},
}

class NPCVoiceQueue:
    """Thread-safe queue for NPC dialogue TTS requests."""
    def __init__(self, runtime_stub: Dict[str, Any]):
        self._q: queue.Queue = queue.Queue()
        self._runtime = runtime_stub
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def speak(self, text: str, archetype: str = "DEFAULT", npc_name: str = ""):
        """Queue a TTS request for an NPC line."""
        profile = NPC_VOICE_MAP.get(archetype, NPC_VOICE_MAP["DEFAULT"])
        self._q.put({"text": text, "profile": profile, "npc_name": npc_name})

    def _worker(self):
        while True:
            item = self._q.get()
            try:
                self._synthesize(item)
            except Exception as e:
                print(f"[NK Voice] Error: {e}")
            self._q.task_done()

    def _synthesize(self, item: dict):
        """Synthesize speech via Radio OS voice_provider if available."""
        vp = self._runtime.get("voice_provider")
        if vp is None:
            print(f"[NK Voice] {item['npc_name']}: {item['text']}")
            return
        try:
            # Use voice_provider.synthesize if available
            if hasattr(vp, "synthesize"):
                audio = vp.synthesize(
                    text=item["text"],
                    speed=item["profile"]["speed"],
                    pitch=item["profile"]["pitch"],
                )
                # Play via audio queue if available
                aq = self._runtime.get("audio_queue")
                if aq:
                    aq.put({"type": "tts", "audio": audio, "npc": item["npc_name"]})
            else:
                print(f"[NK Voice] {item['npc_name']}: {item['text']}")
        except Exception as e:
            print(f"[NK Voice] Synthesis failed: {e}")

_voice_queue: Optional[NPCVoiceQueue] = None

def init_voice(runtime_stub: Dict[str, Any]) -> NPCVoiceQueue:
    global _voice_queue
    _voice_queue = NPCVoiceQueue(runtime_stub)
    return _voice_queue

def speak_npc(text: str, archetype: str = "DEFAULT", npc_name: str = ""):
    if _voice_queue:
        _voice_queue.speak(text, archetype, npc_name)
    else:
        print(f"[NK Voice uninit] {npc_name}: {text}")
```

Add to server.py a new endpoint:
```
POST /api/speak
body: { "text": str, "archetype": str, "npc_name": str }
→ queues TTS, returns { "status": "queued" }
```

Add to nkStore.ts:
```typescript
export async function nkSpeak(text: string, archetype = 'DEFAULT', npc_name = '') {
  await fetch(`${NK_BASE}/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, archetype, npc_name }),
  })
}
```

Wire speak calls into KnowerDialogue (task 9 spec) and trainer battle intro text.

---

## TASK 4: ESP32 Puck Protocol

**File:** `plugins/neikos/spatial/esp32.py`
**Update:** `plugins/neikos/server.py` — add WebSocket /ws/puck endpoint
**Update:** `plugins/neikos/__init__.py` — start puck manager on register_widgets

Build the ESP32 puck bridge. Pucks connect over WebSocket. Each puck identifies itself with a node_id.

esp32.py:
```python
from __future__ import annotations
import json, threading, time, logging
from typing import Dict, Set, Optional, Any

log = logging.getLogger("nk.puck")

class PuckManager:
    """
    Manages ESP32 puck connections. Each puck registers with:
      { "type": "register", "node_id": "N001", "puck_id": "puck-a3f2" }
    
    When player moves to a node, the puck at that node gets:
      { "type": "activate", "node_type": "WILD_ZONE", "tier": 1, "is_relay": false }
    
    Other pucks get:
      { "type": "ambient", "node_type": "...", "tier": 1 }
    
    Button press from puck:
      { "type": "interact", "node_id": "N001", "puck_id": "puck-a3f2" }
    → triggers explore action at that node if player is there
    """
    
    def __init__(self, controller):
        self._controller = controller
        self._pucks: Dict[str, Any] = {}      # puck_id → websocket
        self._puck_nodes: Dict[str, str] = {} # puck_id → node_id
        self._node_pucks: Dict[str, str] = {} # node_id → puck_id
        self._lock = threading.Lock()
    
    def register(self, puck_id: str, node_id: str, ws):
        with self._lock:
            self._pucks[puck_id] = ws
            self._puck_nodes[puck_id] = node_id
            self._node_pucks[node_id] = puck_id
        log.info(f"Puck {puck_id} registered at node {node_id}")
        self._send(puck_id, {"type": "registered", "node_id": node_id})
    
    def unregister(self, puck_id: str):
        with self._lock:
            node = self._puck_nodes.pop(puck_id, None)
            if node:
                self._node_pucks.pop(node, None)
            self._pucks.pop(puck_id, None)
        log.info(f"Puck {puck_id} disconnected")
    
    def on_player_move(self, new_node_id: str, node_type: str, tier: int, is_relay: bool):
        """Called when player moves. Activate the target puck, ambient all others."""
        with self._lock:
            for pid, nid in self._puck_nodes.items():
                if nid == new_node_id:
                    self._send(pid, {
                        "type": "activate",
                        "node_type": node_type,
                        "tier": tier,
                        "is_relay": is_relay,
                    })
                else:
                    self._send(pid, {
                        "type": "ambient",
                        "node_type": node_type,
                        "tier": tier,
                    })
    
    def on_interact(self, puck_id: str):
        """Button press on a puck — trigger explore at that node if player is there."""
        node_id = self._puck_nodes.get(puck_id)
        if not node_id:
            return
        st = self._controller._state
        if st and st.player_location == node_id:
            self._controller._cmd_q.put({"action": "explore"})
    
    def broadcast(self, msg: dict):
        with self._lock:
            for pid in list(self._pucks.keys()):
                self._send(pid, msg)
    
    def connected_count(self) -> int:
        return len(self._pucks)
    
    def _send(self, puck_id: str, msg: dict):
        ws = self._pucks.get(puck_id)
        if ws:
            try:
                # WebSocket send — called from FastAPI ws handler
                ws._pending = ws._pending if hasattr(ws, '_pending') else []
                ws._pending.append(json.dumps(msg))
            except Exception as e:
                log.warning(f"Puck send failed {puck_id}: {e}")

_puck_manager: Optional[PuckManager] = None

def get_puck_manager() -> Optional[PuckManager]:
    return _puck_manager

def init_puck_manager(controller) -> PuckManager:
    global _puck_manager
    _puck_manager = PuckManager(controller)
    return _puck_manager
```

Add to server.py:
```python
from fastapi import WebSocket, WebSocketDisconnect
from ..spatial.esp32 import get_puck_manager

@app.websocket("/ws/puck")
async def puck_ws(websocket: WebSocket):
    await websocket.accept()
    puck_id = None
    pm = get_puck_manager()
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "register":
                puck_id = data["puck_id"]
                node_id = data["node_id"]
                if pm:
                    pm.register(puck_id, node_id, websocket)
            elif data.get("type") == "interact":
                if pm and puck_id:
                    pm.on_interact(puck_id)
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        if pm and puck_id:
            pm.unregister(puck_id)

@app.get("/api/pucks")
def get_pucks():
    pm = get_puck_manager()
    if not pm:
        return {"connected": 0, "pucks": []}
    return {"connected": pm.connected_count()}
```

Also add to nkStore.ts a puck status indicator store:
```typescript
export const nkPuckCount = writable(0)
// poll /api/pucks every 10s
```

---

## TASK 5: Fragment Audio Treatment

**Update:** `web/src/components/nk/FragmentReader.svelte` (extract from Neikos.svelte)
**Update:** `web/src/lib/nkAudio.ts` — add playFragmentAudio(type, body)

Extract the fragment reader into its own component. Add audio treatment per fragment type.

nkAudio.ts additions:
```typescript
export function playFragmentAudio(fragmentType: string, bodyText: string) {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime

  if (fragmentType === 'AUDIO_ARTIFACT') {
    // Degraded "recording" effect: static burst, then low-pass filtered tone
    const noise = createNoise('white')
    const noiseFilt = createFilter(400, 'lowpass', 2)
    const noiseGain = c.createGain()
    noiseGain.gain.setValueAtTime(0.15, now)
    noiseGain.gain.exponentialRampToValueAtTime(0.001, now + 0.8)
    noise.connect(noiseFilt); noiseFilt.connect(noiseGain); noiseGain.connect(master())
    noise.start(now); noise.stop(now + 0.8)
    // Low tone (like a damaged tape)
    const tone = createOsc(180, 'sawtooth')
    const toneFilt = createFilter(250, 'lowpass')
    const toneGain = c.createGain()
    toneGain.gain.setValueAtTime(0.04, now + 0.3)
    toneGain.gain.exponentialRampToValueAtTime(0.001, now + 1.5)
    tone.connect(toneFilt); toneFilt.connect(toneGain); toneGain.connect(master())
    tone.start(now + 0.3); tone.stop(now + 1.5)
  } else if (fragmentType === 'REDACTED_LOG') {
    // Bureaucratic stamp sound — short low thud
    const thud = createOsc(80, 'sine')
    const thudGain = c.createGain()
    thudGain.gain.setValueAtTime(0.12, now)
    thudGain.gain.exponentialRampToValueAtTime(0.001, now + 0.25)
    thud.connect(thudGain); thudGain.connect(master())
    thud.start(now); thud.stop(now + 0.25)
  } else if (fragmentType === 'RESEARCH_NOTE') {
    // Quiet page turn — short pink noise burst
    const page = createNoise('pink')
    const pageFilt = createFilter(3000, 'highpass')
    const pageGain = c.createGain()
    pageGain.gain.setValueAtTime(0.06, now)
    pageGain.gain.exponentialRampToValueAtTime(0.001, now + 0.15)
    page.connect(pageFilt); pageFilt.connect(pageGain); pageGain.connect(master())
    page.start(now); page.stop(now + 0.15)
  } else {
    // Default: soft chime (reuse playFragmentDiscover)
    playFragmentDiscover()
  }
}
```

FragmentReader.svelte: standalone component, accepts fragment prop, emits close event. Calls playFragmentAudio on open. Shows static/glitch overlay animation for AUDIO_ARTIFACT type.

---

## TASK 6: Island Selection Screen

**File:** `web/src/components/nk/IslandSelect.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — show IslandSelect when no island is loaded (nkState is null)
**Update:** `web/src/lib/nkStore.ts` — add nkSeedPreviews store and fetchIslandPreviews()

Show a grid of 100 islands before the game starts. Each island card shows seed-derived info.

For the preview, call GET /api/state — if null, show the selector.

Add to server.py:
```python
@app.get("/api/island_preview/{seed}")
def island_preview(seed: int):
    """Fast preview of island N without full init."""
    from ..world.topology import generate_island_topology  # or from __init__
    topo = generate_island_topology(seed)
    from ..progression.tiers import _seed_to_base_tier
    tier = _seed_to_base_tier(seed)
    return {
        "seed": seed,
        "name": topo.island_name,
        "climate": topo.climate.name,
        "node_count": topo.node_count,
        "base_tier": tier.name,
        "active_types": [t.name for t in topo.active_types[:4]],
    }

@app.get("/api/islands")
def get_islands():
    """Preview all 100 islands."""
    results = []
    for seed in range(1, 101):
        results.append(island_preview(seed))
    return results
```

IslandSelect.svelte:
- Grid of 100 cards (10x10), each showing name, climate, tier pill, 4 type chips
- Tier coloring: tier I = green tint, tier III = blue tint, tier V = red tint
- Hover: expand card slightly, show node count
- Click: call nkActions.reset(seed) which reinits the island
- "Random" button at top
- Loading state while fetching (the 100 previews take a moment since they generate 100 topologies)
- Cache previews in store so it only loads once per session

---

## TASK 7: NGP+ Run History UI

**File:** `web/src/components/nk/NGPHistory.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — add as a sub-panel accessible from the profile section
**Update:** `web/src/lib/nkStore.ts` — add nkProfile store and fetchNGPProfile()

Add to nkStore.ts:
```typescript
export const nkProfile = writable<any>(null)
export async function fetchNGPProfile() {
  try {
    const data = await nkGet('/profile')
    if (!data.error) nkProfile.set(data)
  } catch {}
}
```

NGPHistory.svelte displays:
- Run count ("This is run #N")
- Behavioral axis with icon (COMPETITIVE=⚔️, CURIOUS=🔍, RESEARCHER=🔬, BREEDER=🧬, ANOMALY_SEEKER=⚠️, BALANCED=⚖️)
- Trajectory scores from prior run as horizontal bars
- Echo seeds list (node IDs that will echo on next island) — shown as "Memory traces: N001, N045…"
- At run 3+: "Behavioral profile established. Pattern recognition active." — styled as a system message
- At run 5+: show the full profile as a monospace data block, styled like a Cartographer dossier
- Tier V visual: the whole panel is styled with nk-frag-bg and labeled "SUBJECT BEHAVIORAL DOSSIER — RESTRICTED"
- Empty state (run 1): "No prior expedition data. This is your first island."

---

## TASK 8: Tier Escalation Moment

**File:** `web/src/components/nk/TierEscalation.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — watch for tier changes and show overlay

TierEscalation.svelte:
- Full-screen overlay, absolute positioned, z-index 1000
- Background: solid --nk-bg (matches current tier)
- Center: single line of text in monospace, large (24px), letter-spacing 3px
- Text per tier transition:
  - → Tier II: "ANOMALOUS BEHAVIOR FLAGGED"
  - → Tier III: "ADMINISTRATIVE REVIEW INITIATED"  
  - → Tier IV: "CONTAINMENT PROTOCOLS ACTIVE"
  - → Tier V: "FULL DISCLOSURE MODE ENGAGED"
- Below text: thin horizontal line, then tier name in small caps
- Animation: fade in (0.3s), hold 2.5s, fade out (0.8s), then destroy component
- After overlay fades: the CSS tier shift happens (so you see it in the now-colder UI)
- Play a sound on escalation: low descending tones (use nkAudio — add playTierEscalation())

nkAudio.ts addition:
```typescript
export function playTierEscalation(newTier: number) {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Deep ominous pulse
  const freqs = [newTier === 5 ? 30 : 40, 60, 80]
  freqs.forEach((f, i) => {
    const o = createOsc(f, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.3)
    g.gain.linearRampToValueAtTime(0.15 - i * 0.03, now + i * 0.3 + 0.4)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.3 + 2.5)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.3); o.stop(now + i * 0.3 + 2.5)
  })
}
```

In Neikos.svelte:
```svelte
let showEscalation = false
let escalationTier = 1
let prevTier = 1

$: {
  if (tier > prevTier && prevTier > 0) {
    escalationTier = tier
    showEscalation = true
    nkAudio.playTierEscalation(tier)
    setTimeout(() => { showEscalation = false }, 3600)
  }
  prevTier = tier
}
```

---

## TASK 9: Knower Dialogue Flow

**File:** `web/src/components/nk/KnowerDialogue.svelte`
**Update:** `web/src/tabs/Neikos.svelte` — use KnowerDialogue in the Knower tab instead of inline code
**Update:** `web/src/lib/nkStore.ts` — add nkKnowerDialogue store for active dialogue state

Full conversation UI replacing the simple fragment buttons.

KnowerDialogue.svelte:
- NPC portrait area: archetype icon (large emoji or SVG), name, archetype label
  - ELDER: 🧙 | SCIENTIST: 🔬 | REBEL: 🗡️ | CARTOGRAPHER: 🗺️ | GHOST: 👁️
- Dialogue bubble: renders current fragment text with typewriter effect (character by character, 30ms interval)
- Skip button: reveals full text immediately
- Below bubble: "Continue" button (if more fragments) or "End conversation" 
- Fragment index indicator: dots showing progress through 5-8 fragments
- "Voice" button: calls nkSpeak() from task 3 (gracefully no-ops if voice not available)
- Unlock requirements shown as greyed-out fragments at the bottom ("3 more conversations require deeper exploration")
- Tier awareness:
  - Tier 1-2: warm, human layout
  - Tier 3+: colder, more clinical — name becomes "DESIGNATION: [name]", archetype label becomes a code
  - Tier 5: the NPC portrait gets a faint scan-line effect (CSS animation)
- On first unlock (fragment 0): play nkAudio.playKnowerUnlock()
- Emit 'close' event when conversation ends

nkStore.ts addition:
```typescript
export const nkKnowerDialogue = writable<{
  active: boolean
  fragmentIndex: number
  currentText: string
  isTyping: boolean
} | null>(null)
```

---

## TASK 10: Species Encounter & Capture Flow

**File:** `web/src/components/nk/EncounterScreen.svelte`
**File:** `web/src/components/nk/TeamManager.svelte`  
**Update:** `web/src/tabs/Neikos.svelte` — show EncounterScreen when encounter event fires, add team panel
**Update:** `web/src/lib/nkStore.ts` — add nkTeam store, nkEncounter store

This makes the game actually playable end-to-end.

nkStore.ts additions:
```typescript
export interface NKCreature {
  instance_id: string
  species_id: string
  level: number
  fatigue: number
  genes: { stat_genes: number[], trait_genes: string[] }
}

export const nkTeam    = writable<NKCreature[]>([])
export const nkCreatures = writable<Record<string, NKCreature>>({})
export const nkEncounterActive = writable(false)
export const nkEncounterSpecies = writable<any>(null)
export const nkEncounterInstance = writable<string | null>(null)
```

EncounterScreen.svelte:
- Shows when nkEncounterActive is true (triggered by 'encounter' event in nkEvents)
- Wild creature display: name, type chips, level, animated entrance (slide in from right)
- Action buttons: Battle | Capture | Flee
- Capture: calls nkCommand({ action: 'capture', instance_id }) — NOTE: add this to backend too
- Battle: transitions to BattleScreen (task 2) with this creature as opponent
- Flee: dismisses encounter
- Capture result: success animation (creature card flies into team), fail (creature fled)
- After capture: if team < 3, auto-add; if team full, show swap screen

Add capture endpoint to server.py:
```python
@app.post("/api/capture")
async def capture_creature(data: dict):
    instance_id = data.get("instance_id", "")
    if controller and controller._state:
        st = controller._state
        inst = st.creatures.get(instance_id)
        if not inst:
            return {"error": "creature not found"}
        # Add to team if space available
        if len(st.player_team) < 6:
            st.player_team.append((instance_id, inst.species_id))
            return {"success": True, "team_size": len(st.player_team)}
        return {"error": "team full", "team_size": len(st.player_team)}
    return {"error": "not initialized"}
```

TeamManager.svelte:
- Shows player's current team (up to 6 creatures)
- Each creature card: name, species, level, fatigue bar, type chips
- Swap/release buttons
- Accessible from explore tab as a panel below the profile section
- Empty slots shown as dashed cards ("Empty slot")

---

## COMPLETION

After all 10 tasks:
1. Run: `cd web && npm run build` — must succeed with 0 errors
2. Run: `python -c "import plugins.neikos; print('Backend OK')"` from Radio-OS root
3. Write a summary to `plugins/neikos/SPRINT1_COMPLETE.md` listing what was built
4. Telegram the user (K West) with a summary — use the message tool if available, otherwise write to a notification file
