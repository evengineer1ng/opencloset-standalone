/**
 * nkStore.ts — Neikos reactive state + API client
 * Connects to the Neikos FastAPI server on port 7700
 */
import { writable, derived } from 'svelte/store'

const NK_BASE = 'http://localhost:7700/api'

// ── Types ──────────────────────────────────────────────────

export interface NKNode {
  node_id: string
  node_type: string
  region: string
  name: string
  biome: number[]          // [humidity, temp, instability, toxicity]
  neighbors: string[]
  is_relay_node: boolean
  gate: any | null
  faction_influence: Record<string, number>
}

export interface NKState {
  seed: number
  tick: number
  island_name: string
  climate: string
  node_count: number
  species_count: number
  player_location: string
  discovered_species: number
  ledger: Record<string, any>
  trajectory: Record<string, any>
  factions: Record<string, string>  // fid → name
}

export interface NKTier {
  base_tier: string
  current_tier: string
  tier_value: number
  description: string
  tick: number
}

export interface NKFragment {
  fragment_id: string
  type: string
  title: string
  body: string | null
  mountain_code: string
  discovered: boolean
  unlock_condition: Record<string, number>
}

export interface NKSpecies {
  species_id: string
  name: string
  primary_type: string
  secondary_type: string | null
  rarity: string
  stat_archetype: string
  base_stats: Record<string, number>
  evolution_line_id: string
  discovery_node_types: string[]
}

export interface NKKnower {
  name: string
  archetype: string
  location_node_id: string
  /** Raw fragments are NOT sent by /api/knower — use nkFetchKnowerFragment() to load on demand. */
  dialogue_fragments?: string[]
  fragment_count: number
  is_unlocked: boolean
  unlock_thresholds: Record<string, number>
}

export interface NKEvent {
  type: string
  data: Record<string, any>
  tick: number
  ts: number
}

// ── Stores ─────────────────────────────────────────────────

export const nkConnected   = writable(false)
export const nkState       = writable<NKState | null>(null)
export const nkMap         = writable<Record<string, NKNode>>({})
export const nkMapStart    = writable('')
export const nkTier        = writable<NKTier | null>(null)
export const nkFragments   = writable<NKFragment[]>([])
export const nkSpecies     = writable<NKSpecies[]>([])
export const nkKnower      = writable<NKKnower | null>(null)
export const nkEvents      = writable<NKEvent[]>([])
export const nkBusy        = writable(false)   // command in flight
export const nkScreen      = writable<'explore' | 'battle' | 'encounter' | 'fragments' | 'knower' | 'species'>('explore')
export const nkBattleTarget = writable<string | null>(null)
export const nkActiveFragment = writable<NKFragment | null>(null)
export const nkKnowerFrag   = writable(0)       // which knower fragment index

// Active encounter data (set before switching to 'encounter' screen)
export interface NKEncounterData {
  species: {
    species_id: string
    name: string
    primary_type: string
    secondary_type: string | null
    rarity: string
    bst: number
    stats: number[]
    archetype: string
    passive: string
    evo_stage: number
    evo_line: string
    evolves_to: string | null
    evolves_from: string | null
    evo_level: number | null
  }
  instance_id: string
  level: number
  temperament: number
}
export const nkEncounterData = writable<NKEncounterData | null>(null)

// Derived
export const nkTierValue    = derived(nkTier, $t => $t?.tier_value ?? 1)
export const nkPlayerLoc    = derived(nkState, $s => $s?.player_location ?? '')
export const nkIslandName   = derived(nkState, $s => $s?.island_name ?? 'Unknown Island')
export const nkDiscovered   = derived(nkFragments, $f => $f.filter(f => f.discovered))
export const nkTrajectory   = derived(nkState, $s => $s?.trajectory ?? {})

// ── API helpers ─────────────────────────────────────────────

async function nkGet(path: string) {
  const r = await fetch(`${NK_BASE}${path}`)
  if (!r.ok) throw new Error(`NK ${path}: ${r.status}`)
  return r.json()
}

export async function nkCommand(cmd: Record<string, any>): Promise<boolean> {
  nkBusy.set(true)
  try {
    const r = await fetch(`${NK_BASE}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cmd),
    })
    if (!r.ok) return false
    return true
  } catch {
    return false
  } finally {
    nkBusy.set(false)
    // Refresh state after any command
    setTimeout(nkRefreshAll, 300)
  }
}

export function nkPushEvent(type: string, data: Record<string, any> = {}, tick = 0) {
  nkEvents.update(evts => {
    const next = [...evts, { type, data, tick, ts: Date.now() }]
    return next.slice(-60)  // keep last 60 events
  })
}

// ── Refresh functions ──────────────────────────────────────

export async function nkRefreshAll() {
  try {
    const [state, mapData, tierData] = await Promise.all([
      nkGet('/state'),
      nkGet('/map'),
      nkGet('/tier'),
    ])
    if (!state.error) {
      nkState.set(state)
      // Merge recent_events from /api/state into the nkEvents store.
      // Backend events lack a client-side `ts`; use tick+type as a dedup key
      // to avoid re-inserting events we already have from direct action calls.
      const incoming: Array<{ type: string; data: Record<string, any>; tick: number }> =
        state.recent_events ?? []
      if (incoming.length > 0) {
        nkEvents.update(existing => {
          const seen = new Set(existing.map(e => `${e.tick}:${e.type}`))
          const fresh = incoming
            .filter(e => !seen.has(`${e.tick}:${e.type}`))
            .map(e => ({ ...e, ts: 0 }))  // ts=0 signals a server-sourced event
          if (fresh.length === 0) return existing
          const merged = [...existing, ...fresh]
            .sort((a, b) => a.tick - b.tick)
          return merged.slice(-60)
        })
      }
    }
    if (!mapData.error) { nkMap.set(mapData.nodes ?? {}); nkMapStart.set(mapData.start ?? '') }
    if (!tierData.error) nkTier.set(tierData)
    nkConnected.set(true)
  } catch {
    nkConnected.set(false)
  }
}

export async function nkRefreshFragments() {
  try {
    const data = await nkGet('/fragments')
    if (!data.error) nkFragments.set(data.fragments ?? [])
  } catch {}
}

export async function nkRefreshSpecies() {
  try {
    const data = await nkGet('/species')
    if (Array.isArray(data)) nkSpecies.set(data)
  } catch {}
}

export async function nkRefreshKnower() {
  try {
    const data = await nkGet('/knower')
    nkKnower.set(data.error ? null : data)
  } catch { nkKnower.set(null) }
}

// ── Polling ────────────────────────────────────────────────

let _poll: ReturnType<typeof setInterval> | null = null

export function startNKPolling() {
  if (_poll) return
  nkRefreshAll()
  _poll = setInterval(nkRefreshAll, 3000)
}

export function stopNKPolling() {
  if (_poll) { clearInterval(_poll); _poll = null }
}

// ── Actions (convenience wrappers) ───────────────────────

export const nkActions = {
  move:       (target_node: string) => nkCommand({ action: 'move', target_node }),
  encounter:  ()                    => nkCommand({ action: 'encounter' }),
  explore:    ()                    => nkCommand({ action: 'explore' }),
  battle:     (opponent_id: string) => nkCommand({ action: 'battle', opponent_id }),
  breed:      (a: string, b: string)=> nkCommand({ action: 'breed', parent_a_id: a, parent_b_id: b }),
  dialogue:   (delta: Record<string, number>) => nkCommand({ action: 'dialogue', ...delta }),
  talkKnower: (fragment_index = 0)  => nkCommand({ action: 'talk_knower', fragment_index }),
  getState:   ()                    => nkCommand({ action: 'get_state' }),
  newExpedition: (seed = 0)         => nkCommand({ action: 'new_expedition', seed }),
  reset:      (seed = 42)           => nkCommand({ action: 'reset_simulation', seed }),
}

/** Roll a wild encounter — returns encounter data or null if nothing appeared. */
export async function nkRollEncounter(): Promise<NKEncounterData | null> {
  try {
    const r = await fetch(`${NK_BASE}/encounter`, { method: 'POST' })
    if (!r.ok) return null
    const data = await r.json()
    if (data.encounter) {
      // Normalise stats: backend may return a space-separated string in some shapes
      const sp = data.encounter.species
      if (typeof sp.stats === 'string') {
        sp.stats = (sp.stats as string).split(' ').map(Number)
      }
      return data.encounter as NKEncounterData
    }
    return null
  } catch {
    return null
  } finally {
    setTimeout(nkRefreshAll, 300)
  }
}

export async function nkSpeak(text: string, archetype = 'DEFAULT', npc_name = '') {
  try {
    await fetch(`${NK_BASE}/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, archetype, npc_name }),
    })
  } catch {}
}

// ── Trainers ───────────────────────────────────────────────
export interface NKTrainer {
  trainer_id: string
  name: string
  rating: number
  tier: string
  team_size: number
  wins: number
  losses: number
}

export const nkTrainers = writable<NKTrainer[]>([])

export async function fetchTrainers(): Promise<void> {
  try {
    const data = await nkGet('/trainers')
    if (Array.isArray(data?.trainers)) nkTrainers.set(data.trainers)
  } catch {}
}

/**
 * Challenge a trainer via /api/battle.
 * The endpoint queues the command, waits up to 600ms for simulate_battle,
 * then returns the result (winner, turns, player_rating, events, …) directly.
 * Merges returned events into nkEvents and schedules a state refresh.
 * Returns the response object, or null on network/server error.
 */
export async function nkChallengeBattle(
  trainer_id: string,
): Promise<Record<string, any> | null> {
  nkBusy.set(true)
  try {
    const r = await fetch(`${NK_BASE}/battle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trainer_id }),
    })
    if (!r.ok) return null
    const data = await r.json()
    // Merge any returned events into the nkEvents store
    const incoming: Array<{ type: string; data: Record<string, any>; tick: number }> =
      data.events ?? []
    if (incoming.length > 0) {
      nkEvents.update(existing => {
        const seen = new Set(existing.map((e: NKEvent) => `${e.tick}:${e.type}`))
        const fresh = incoming
          .filter(e => !seen.has(`${e.tick}:${e.type}`))
          .map(e => ({ ...e, ts: Date.now() }))
        if (fresh.length === 0) return existing
        return [...existing, ...fresh].slice(-60)
      })
    }
    setTimeout(nkRefreshAll, 300)
    return data
  } catch {
    return null
  } finally {
    nkBusy.set(false)
  }
}

// ── Puck status ────────────────────────────────────────────
export const nkPuckCount = writable(0)

async function _refreshPucks() {
  try {
    const data = await nkGet('/pucks')
    nkPuckCount.set(data.connected ?? 0)
  } catch {}
}

let _puckPoll: ReturnType<typeof setInterval> | null = null
export function startPuckPolling() {
  if (_puckPoll) return
  _refreshPucks()
  _puckPoll = setInterval(_refreshPucks, 10000)
}
export function stopPuckPolling() {
  if (_puckPoll) { clearInterval(_puckPoll); _puckPoll = null }
}

// ── Island previews ────────────────────────────────────────
export interface NKIslandPreview {
  seed: number
  name: string
  climate: string
  node_count: number
  base_tier: string
  active_types: string[]
}

export const nkSeedPreviews = writable<NKIslandPreview[]>([])
let _previewsLoaded = false

export async function fetchIslandPreviews(): Promise<void> {
  // Always re-fetch if cache wasn't fully ready last time
  if (_previewsLoaded) return
  try {
    const data = await nkGet('/islands')
    // New format: { islands: [...], cache_ready: bool, cached_count: number }
    // Legacy fallback: plain array
    const list = Array.isArray(data) ? data : (data?.islands ?? [])
    if (list.length > 0) {
      nkSeedPreviews.set(list)
      // Mark loaded only once the cache is fully built (all 100 seeds)
      if (Array.isArray(data) || data?.cache_ready) {
        _previewsLoaded = true
      }
    }
  } catch {}
}

// ── NGP Profile ────────────────────────────────────────────
export const nkProfile = writable<any>(null)

export async function fetchNGPProfile() {
  try {
    const data = await nkGet('/profile')
    if (!data.error) nkProfile.set(data)
  } catch {}
}

// ── Knower Dialogue ────────────────────────────────────────
/**
 * Fetch a single Knower dialogue fragment by index.
 * Calls POST /api/knower/talk and returns { fragment, total_fragments, name, archetype }.
 * Returns null on error or if the knower is locked.
 */
export async function nkFetchKnowerFragment(
  fragment_index: number,
): Promise<{ fragment: string; total_fragments: number; name: string; archetype: string } | null> {
  try {
    const r = await fetch(`${NK_BASE}/knower/talk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fragment_index }),
    })
    if (!r.ok) return null
    const data = await r.json()
    if (data.event_type === 'knower_locked' || data.error) return null
    if (typeof data.fragment !== 'string') return null
    return {
      fragment:        data.fragment        as string,
      total_fragments: data.total_fragments as number ?? 1,
      name:            data.name            as string ?? '',
      archetype:       data.archetype       as string ?? '',
    }
  } catch {
    return null
  }
}

export const nkKnowerDialogue = writable<{
  active: boolean
  fragmentIndex: number
  currentText: string
  isTyping: boolean
} | null>(null)
