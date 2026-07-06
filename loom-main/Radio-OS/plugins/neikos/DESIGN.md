# Neikos: Hundred Islands — Design Document
*Last updated: 2026-03-19 | Maintained by the AI*

---

## What This Is

Neikos: Hundred Islands is a single-player creature adventure game with a dark conspiracy underneath.

On the surface: you explore an island, discover and battle Neiko creatures, breed them, align with factions, climb the league. Standard creature-game loop.

Underneath: every island is one of 100 contained ecological experiments. The league is administration. The Knower knows. The Founder may have built the cage. PROJECT HUNDRED is real, and you are the subject.

Every run is one island — seed 1 through 100 (or any seed). You never see two islands in a run. The island is the whole world. Your behavioral signature persists into the next run via NGP+.

---

## Platform Targets (Equal Priority)

### 1. Web Browser
A room-traversal experience with strong spatial audio. The player navigates between "rooms" (map nodes) via a visual interface. Each node has sublocations (puck slots). Moving between nodes triggers ambient audio transitions. The browser is the primary screen.

Sound design is a first-class feature — not background music, but environmental audio that changes based on node type, biome, containment tier, and events.

### 2. ESP32 Puck Room
Physical speaker/mic units (ESP32-based) placed around a real room. Each puck represents a map sublocation. The player physically walks to a puck to interact with that sublocation. The R-Unit concept (a central console) is retained conceptually but not tied to specific hardware — any ESP32 with a screen or indicator can serve the function.

The web browser and the puck room are two views into the same game engine. They share state. Ideally a player can switch between them mid-session.

### 3. Flutter App (Deprioritized)
Legacy R-Unit hardware companion. Not a focus. Keep it functional but don't build toward it.

---

## Radio OS Integration

Radio OS is not a game narrator or ambient music system. Radio OS is the **voice of every NPC in the game**.

When you walk up to a character and initiate dialogue — a trainer, a faction liaison, the Hidden Knower — Radio OS voices that character. Each NPC has a persona that maps to a Radio OS station or voice profile. The dialogue content is generated (from the Cold Layer simulation data) and delivered as speech.

This means:
- NPC dialogue is always live/generated, never pre-recorded
- Each character can sound different (different TTS voices/stations)
- The Hidden Knower should feel distinctly different from a league trainer
- Radio OS handles the audio routing; the game engine handles the content

The Cold Layer principle still holds: Radio OS is presentation only. The simulation computes what an NPC knows and says. Radio OS gives it voice.

---

## Core Game Loop

```
Arrive on Island (seed N)
    ↓
Explore nodes → encounter Neikos → build team
    ↓
Battle trainers → climb league standings
    ↓
Align with factions → shift ledger axes
    ↓
Research / anomaly exposure → unlock fragments
    ↓
Find the Hidden Knower → piece together the conspiracy
    ↓
Reach an outcome band (1–100) based on your behavioral signature
    ↓
NGP+ profile saved → next island is shaped by how you played this one
```

---

## The Cold Layer Principle

The simulation is pure deterministic computation. Given a seed, everything is derivable:
- Island topology
- Species roster
- Faction placement
- Fragment selection
- Knower identity and location
- Containment tier
- All encounter tables

The LLM (Radio OS) only touches the presentation layer: NPC dialogue, fragment narration, ambient commentary. It never generates world state.

This means the game is:
- Fully reproducible (same seed = same island)
- Cheat-proof at the simulation level
- Fast to initialize (no LLM calls during world gen)
- Safe to run headless or in background

---

## Spatial Audio Design

### Web
- Each node type has an ambient soundscape (settlement hum, wild zone insects, facility hum, dungeon echo)
- Moving between nodes triggers a crossfade transition
- Anomaly zones have distinct audio artifacts (distortion, sub-harmonic pulses at 47s intervals — matching the relay node lore)
- Battle audio is positional (opponent left, player right, field events center)
- Fragment discovery has a dedicated audio signature by type (REDACTED_LOG = static/bureaucratic, AUDIO_ARTIFACT = degraded recording, RESEARCH_NOTE = quiet/intimate)

### ESP32 Pucks
- Each puck plays the ambient audio for its sublocation when the player is near
- The puck at the R-Unit position plays the node-level ambient
- Pucks can emit directional hints (audio cues pointing toward next node)
- Battle: pucks represent the two sides; creature sounds come from the appropriate puck
- Fragment discovery: the puck where the fragment was found plays the audio

---

## NGP+ (New Game Plus) System

Completing a run saves a `BehavioralProfileSignature`:
- Your behavioral axis (COMPETITIVE / CURIOUS / RESEARCHER / BREEDER / ANOMALY_SEEKER / BALANCED)
- Your trajectory scores
- Your final containment tier
- Echo seeds (node IDs from this run that will echo on future runs)
- Run count

On the next island:
- Some nodes carry "memory echoes" of your prior decisions
- The island's faction balance nudges toward your historical patterns
- Higher run count = deeper fragment unlock conditions = harder truths
- Tier escalation happens faster if you keep triggering it

The NGP+ system is how the game gets darker over time. Run 1 is a creature game. Run 5 starts to feel like you're being watched. Run 10 you know what you are.

---

## Containment Tier System

Five tiers (I through V). Each island starts at a seed-determined base tier. Your actions can escalate it — anomaly exposure, aggressive competitive play, research into restricted topics.

Higher tiers:
- More aggressive faction behavior
- Faster ecological degradation
- More dangerous encounters
- Fragments unlock earlier (the system wants you to know)
- The Knower is less hidden

Tier never de-escalates mid-island. It's a ratchet.

---

## The Hidden Knower

One NPC per island. They know about PROJECT HUNDRED. They're not the only one who knows — the Cartographers know, the Founder knew — but they're the one who will tell *you*.

They're hidden: their location is not marked on the map. You find them by exploring deeply. They unlock based on your trajectory (you have to be the kind of player who would understand what they're saying).

They have 5–8 dialogue fragments. Each one costs something (exploration depth, research, anomaly exposure). The later ones are harder.

Radio OS voices them distinctly. They should sound like someone who has been on this island a long time and is very tired of it.

---

## The Fragment System

Lore pieces discovered through exploration. 40 in the global pool; up to 20 selected per island based on its narrative profile.

Fragment types:
- `REDACTED_LOG` — bureaucratic documents with things crossed out
- `RESEARCH_NOTE` — personal field notes from researchers
- `AUDIO_ARTIFACT` — degraded recordings, labeled and timestamped
- `SPECIES_REGISTRY_GLITCH` — database entries with integrity errors
- `STATISTICAL_SUMMARY` — data reports with editorial interference

They build the conspiracy through inference, not exposition. No fragment explains PROJECT HUNDRED directly. Taken together, across runs, the picture assembles.

---

## Species Design Principles

- 300 species per island, generated from seed
- 18 elemental types; each island has 8–12 active types
- Species are deterministic: same seed = same species at same nodes
- Evolution lines exist; breeding within a line is always compatible
- Rarity tiers: COMMON / UNCOMMON / RARE / APEX / ANOMALY
- ANOMALY species only appear at anomaly zones; their type signatures are unstable
- Species names are generated, not hand-authored

Breeding: offspring inherit a blend of parent genes with mutation variance scaled by anomaly instability. Higher tier = more interesting offspring. Also more dangerous ones.

---

## What Neikos Is Not

- Not a gacha game
- Not a multiplayer PvP game (open to it later, not the focus)
- Not a narrative-on-rails game (the LLM generates dialogue, the player chooses the world)
- Not a Pokemon clone (borrows the creature loop, not the tone or mechanics)
- Not a web novel or ARG (it's a playable game that happens to have lore depth)

---

## What Needs Building (Priority Order)

1. **Package restructure** — split neikos.py into the module tree; nothing should change functionally
2. **Web frontend** — room traversal UI, node map, sublocation pucks as clickable elements, battle screen
3. **Sound design system** — ambient audio per node type/biome, transition engine, fragment audio signatures
4. **Radio OS NPC voice pipeline** — character profile → voice selection → TTS → playback
5. **ESP32 puck protocol** — WebSocket or MQTT bridge; puck discovery, state sync, audio routing
6. **Battle UI** — visual battle screen with positional audio
7. **Fragment reader** — dedicated UI for discovered fragments with appropriate audio treatment
8. **NGP+ save/load UI** — show run history, behavioral axis, what carried over

---

## UI Aesthetic — Layered (Confirmed)

The browser UI is part of the experience. It starts warm and shifts cold.

### Tier I — Adventure Mode
- Soft earthy palette (greens, ambers, warm neutrals)
- Rounded nodes on the map, friendly iconography
- Fragment UI is a journal — handwritten-style font, warm paper texture
- NPC dialogue boxes look like a classic creature game
- Audio: birdsong, wind, natural ambience

### Tier II — First Cracks
- Color temperature cools slightly — blues start entering
- Map nodes become slightly more geometric
- A few UI elements are slightly misaligned or have faint artifacts
- Fragments start showing [REDACTED] blocks — rendered as actual blank rectangles
- Audio: subtle low-frequency undertone beneath the naturals

### Tier III — Institutional Creep
- Palette shifts toward cool grays and muted blues
- Map begins to look like a survey map — grid lines visible, nodes labeled with IDs
- The "league" UI elements (standings, trainer cards) start looking like bureaucratic forms
- Fragment reader now looks like a document viewer, not a journal
- Knower location marker appears on map but is labeled "???"
- Audio: facility hum present in all indoor nodes

### Tier IV — Containment
- Near-monochromatic with accent colors only for danger signals
- Navigation prompts show node IDs before names
- The word "ISLAND" in the header is sometimes replaced with "SITE [N]"
- Fragment titles appear in all-caps; redacted blocks are more frequent
- The player's behavioral stats are now visible in a sidebar labeled "SUBJECT PROFILE"
- Audio: relay node pulse (47s interval) audible as a sub-bass tick across all environments

### Tier V — Full Exposure
- The UI looks like a Cartographer monitoring terminal
- Player is labeled as SUBJECT throughout
- Map shows "CONTAINMENT BOUNDARY" markers at island edges
- The league standings table header reads "BEHAVIORAL COMPLIANCE INDEX"
- Knower is fully visible; their location is marked "OBSERVER — AUTHORIZED"
- All fragments are unlocked and the full body is visible (no conditions)
- Audio: the ambient tracks are now clearly synthetic/artificial — the "nature" sounds are revealed to be recordings

The transitions between tiers should be gradual, not sudden. CSS variables drive the aesthetic; a tier escalation event triggers a slow interpolation over several seconds.

---

## Open Questions (to resolve as we build)

- **Save format**: local JSON file vs. database for island state mid-run
- **Fragment audio**: should AUDIO_ARTIFACT fragments play a degraded TTS rendition vs. clean text?
- **Multiplayer hook**: if we add PvP, does it happen within one island or across the 100?
- **ESP32 puck count**: how many pucks in the room? This determines max page size in SubpageLayout
- **Island selection**: does the player choose which seed (island 1–100) or is it assigned?
