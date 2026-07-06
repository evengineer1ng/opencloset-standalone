# SPRINT 1 COMPLETE — Neikos: Hundred Islands

All 10 tasks implemented, build passing, Python import verified.

---

## What Was Built

### New Svelte Components (`web/src/components/nk/`)
| Component | Purpose |
|---|---|
| `NodeMap.svelte` | SVG BFS radial map with pan, relay pulse, clickable neighbors, tier labels |
| `BattleScreen.svelte` | Two-sided HP bars, turn log, result panel with rating/exp, audio hooks |
| `FragmentReader.svelte` | Fragment viewer with per-type audio, AUDIO_ARTIFACT static overlay |
| `IslandSelect.svelte` | 100-island grid, tier-colored cards, hover details, random picker |
| `NGPHistory.svelte` | Run history with trajectory scores, axis icon, echo nodes, dossier at run 5+ |
| `TierEscalation.svelte` | Full-screen overlay on tier shift with fade animation + audio sting |
| `KnowerDialogue.svelte` | Typewriter dialogue flow, voice button, fragment dots, tier V scan-line portrait |
| `SpeciesCompare.svelte` | Side-by-side stat comparison matrix, searchable picker, winner highlighting |

### New Python Files
| File | Purpose |
|---|---|
| `plugins/neikos/voice.py` | `NPCVoiceQueue` — threaded TTS dispatch with archetype voice profiles |
| `plugins/neikos/spatial/esp32.py` | `PuckManager` — ESP32 puck registration, activation, button press handler |

### Backend Additions (`plugins/neikos/__init__.py`)
- `GET /api/state` — added `climate` and `discovered_species` fields
- `GET /api/map` — fixed to return `{nodes, start}` instead of raw dict
- `POST /api/speak` — NPC voice queue endpoint
- `GET /api/pucks` — puck connection count
- `WS /ws/puck` — WebSocket for ESP32 pucks
- `GET /api/islands` — all 100 island previews (seed, name, climate, tier, types)
- `GET /api/island_preview/{seed}` — single island fast preview

### Store Additions (`web/src/lib/nkStore.ts`)
- `nkSpeak()` — POST to /api/speak
- `nkPuckCount` — puck connection count store
- `startPuckPolling()` / `stopPuckPolling()`
- `NKIslandPreview` type + `nkSeedPreviews` store + `fetchIslandPreviews()`
- `nkProfile` + `fetchNGPProfile()`
- `nkKnowerDialogue` — active dialogue state

### Audio Engine Additions (`web/src/lib/nkAudio.ts`)
- `playFragmentAudio(type, body)` — per-type sounds (tape static, paper, thud)
- `playTierEscalation(tier)` — low rumble sting on tier shift

### UI Wiring in Neikos.svelte
- Battle screen overlay on `$nkScreen === 'battle'`
- Island Select shown when `!$nkState`
- Tier escalation reactive watcher with `prevTier` tracking
- NGPHistory in explore tab
- KnowerDialogue replacing raw fragment buttons in Knower tab
- SpeciesCompare at top of Species tab when ≥2 species known
- FragmentReader component replaces inline reader

---

## Final Build Stats
- 192 modules transformed
- JS: ~284 kB (88.65 kB gzip)
- CSS: ~94 kB (15.20 kB gzip)
- Python import: OK
- Build time: ~1.45s
