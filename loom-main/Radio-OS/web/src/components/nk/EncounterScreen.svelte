<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte'
  import { nkBusy, nkTierValue, nkEvents, nkScreen, nkState, nkRefreshAll } from '../../lib/nkStore'
  import * as nkAudio from '../../lib/nkAudio'

  const dispatch = createEventDispatcher()

  // ── Encounter data passed in from parent ─────────────────
  export let encounterData: {
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
    }
    instance_id: string
    level: number
    temperament: number
  }

  // ── Local state ────────────────────────────────────────────
  type EncounterPhase = 'wild' | 'capturing' | 'captured' | 'failed' | 'fled'
  let phase: EncounterPhase = 'wild'
  let resultDetail: Record<string, any> | null = null
  let captureLog: string[] = []
  let unsubEvents: (() => void) | null = null

  // ── Tier-sensitive labels ──────────────────────────────────
  $: t = $nkTierValue
  $: creatureLabel   = t >= 4 ? 'SUBJECT'    : t >= 3 ? 'Entity'    : 'Creature'
  $: captureLabel    = t >= 4 ? 'CONTAIN'    : t >= 3 ? 'Secure'    : 'Capture'
  $: fleeLabel       = t >= 4 ? 'ABORT'      : t >= 3 ? 'Withdraw'  : 'Flee'
  $: typeLabel       = t >= 3 ? 'CLASS'       : 'Type'
  $: levelLabel      = t >= 3 ? 'STAGE'       : 'Lv.'
  $: temperamentDesc = temperamentLabel(encounterData.temperament)

  function temperamentLabel(val: number): string {
    if (val < 0.25) return t >= 3 ? 'HOSTILE'   : 'Aggressive'
    if (val < 0.5)  return t >= 3 ? 'CAUTIOUS'  : 'Wary'
    if (val < 0.75) return t >= 3 ? 'NEUTRAL'   : 'Calm'
    return t >= 3 ? 'RECEPTIVE' : 'Docile'
  }

  function rarityColor(rarity: string): string {
    const map: Record<string, string> = {
      COMMON:    'var(--nk-text-muted)',
      UNCOMMON:  '#4ade80',
      RARE:      '#60a5fa',
      EPIC:      '#c084fc',
      LEGENDARY: '#f59e0b',
    }
    return map[rarity] ?? 'var(--nk-text-muted)'
  }

  function statsArray(s: number[]): { label: string; val: number }[] {
    const labels = ['HP', 'ATK', 'DEF', 'SPA', 'SPD', 'SPE']
    return labels.map((label, i) => ({ label, val: s[i] ?? 0 }))
  }

  function catchChance(temperament: number, level: number): number {
    // Mirrors backend logic: base 0.3 + temperament * 0.5, penalised by level
    const base = 0.3 + temperament * 0.5
    const levelPenalty = Math.max(0, (level - 10) * 0.005)
    return Math.round(Math.max(0.05, Math.min(0.95, base - levelPenalty)) * 100)
  }

  onMount(() => {
    nkAudio.playEncounterStart()
    addLog(t >= 3 ? `[SIGNAL DETECTED] ${encounterData.species.name}` : `A wild ${encounterData.species.name} appeared!`)
    unsubEvents = nkEvents.subscribe(evts => {
      const latest = evts.slice().reverse()
      const capEv = latest.find(e => e.type === 'captured' || e.type === 'capture_failed')
      if (capEv && (phase === 'capturing')) {
        if (capEv.type === 'captured') {
          resultDetail = capEv.data
          phase = 'captured'
          addLog(t >= 3 ? `[CONTAINMENT SUCCESS] ${encounterData.species.name} secured.` : `${encounterData.species.name} was captured!`)
          nkAudio.playCapture()
        } else {
          resultDetail = capEv.data ?? {}
          phase = 'failed'
          addLog(t >= 3 ? `[CONTAINMENT FAILURE] Subject evaded.` : `${encounterData.species.name} broke free!`)
          nkAudio.playCaptureFail()
        }
      }
    })
    return () => { if (unsubEvents) unsubEvents() }
  })

  function addLog(line: string) {
    captureLog = [...captureLog.slice(-9), line]
  }

  async function doCapture() {
    if ($nkBusy || phase !== 'wild') return
    phase = 'capturing'
    addLog(t >= 3 ? `[INITIATING CONTAINMENT PROTOCOL...]` : `You throw a capture orb!`)
    nkAudio.ensureStarted()
    try {
      const r = await fetch('http://localhost:7700/api/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instance_id: encounterData.instance_id }),
      })
      if (!r.ok) {
        phase = 'wild'
        addLog('Error: capture request failed.')
        return
      }
      const data = await r.json()
      // Push any result events directly into nkEvents so the onMount subscription
      // fires immediately — don't wait for the 3-second polling cycle.
      if (data.events && Array.isArray(data.events)) {
        const tick = data.tick ?? 0
        nkEvents.update(evts => {
          const seen = new Set(evts.map(e => `${e.tick}:${e.type}`))
          const fresh = (data.events as Array<{ type: string; data: Record<string, any>; tick: number }>)
            .filter(e => !seen.has(`${e.tick ?? tick}:${e.type}`))
            .map(e => ({ ...e, tick: e.tick ?? tick, ts: Date.now() }))
          return [...evts, ...fresh].slice(-60)
        })
      }
      // Also schedule a full state refresh so nkState reflects the new team size
      setTimeout(nkRefreshAll, 250)
    } catch {
      phase = 'wild'
      addLog('Error: server unreachable.')
    }
  }

  function doFlee() {
    if ($nkBusy || phase === 'capturing') return
    addLog(t >= 3 ? `[ABORTING ENCOUNTER]` : `You fled from the encounter.`)
    phase = 'fled'
    nkAudio.playEncounterFlee()
    setTimeout(() => dispatch('close'), 600)
  }

  function doContinue() {
    dispatch('close')
    nkScreen.set('explore')
  }
</script>

<div class="nk-encounter" data-phase={phase}>

  <!-- Header -->
  <div class="nk-enc-header">
    <span class="nk-enc-label">{t >= 3 ? '⚡ ENCOUNTER — SIGNAL DETECTED' : '⚡ Wild Encounter'}</span>
  </div>

  <!-- Species card -->
  <div class="nk-enc-card">
    <div class="nk-enc-name-row">
      <span class="nk-enc-name">{encounterData.species.name}</span>
      <span class="nk-enc-level">{levelLabel} {encounterData.level}</span>
      <span class="nk-enc-rarity" style="color:{rarityColor(encounterData.species.rarity)}">
        {encounterData.species.rarity}
      </span>
    </div>

    <div class="nk-enc-type-row">
      <span class="nk-type-chip">{encounterData.species.primary_type}</span>
      {#if encounterData.species.secondary_type}
        <span class="nk-type-chip secondary">{encounterData.species.secondary_type}</span>
      {/if}
      <span class="nk-enc-archetype">{encounterData.species.archetype}</span>
      <span class="nk-enc-passive" title="Passive ability">{encounterData.species.passive}</span>
    </div>

    <!-- Stats row -->
    <div class="nk-enc-stats">
      {#each statsArray(encounterData.species.stats) as stat}
        <div class="nk-enc-stat">
          <span class="nk-enc-stat-label">{stat.label}</span>
          <span class="nk-enc-stat-val">{stat.val}</span>
          <div class="nk-enc-stat-bar">
            <div class="nk-enc-stat-fill" style="width:{Math.min(100, (stat.val / 120) * 100)}%"></div>
          </div>
        </div>
      {/each}
    </div>

    <!-- Temperament + catch chance -->
    <div class="nk-enc-meta">
      <div class="nk-enc-meta-item">
        <span class="nk-enc-meta-label">Temperament</span>
        <span class="nk-enc-meta-val nk-enc-temp" data-temperament={temperamentDesc}>{temperamentDesc}</span>
      </div>
      <div class="nk-enc-meta-item">
        <span class="nk-enc-meta-label">{t >= 3 ? 'CONTAIN PROB' : 'Catch chance'}</span>
        <span class="nk-enc-meta-val">{catchChance(encounterData.temperament, encounterData.level)}%</span>
      </div>
      <div class="nk-enc-meta-item">
        <span class="nk-enc-meta-label">BST</span>
        <span class="nk-enc-meta-val">{encounterData.species.bst}</span>
      </div>
    </div>
  </div>

  <!-- Action buttons (only during 'wild' phase) -->
  {#if phase === 'wild'}
    <div class="nk-enc-actions">
      <button class="nk-btn nk-btn-capture" on:click={doCapture} disabled={$nkBusy}>
        🔮 {captureLabel}
      </button>
      <button class="nk-btn-ghost" on:click={doFlee} disabled={$nkBusy}>
        🚪 {fleeLabel}
      </button>
    </div>

  <!-- Capturing spinner -->
  {:else if phase === 'capturing'}
    <div class="nk-enc-pending">
      <span class="nk-enc-spinner">◌</span>
      <span>{t >= 3 ? 'Containment in progress…' : 'Capture attempt…'}</span>
    </div>

  <!-- Result: captured -->
  {:else if phase === 'captured' && resultDetail}
    <div class="nk-enc-result nk-enc-result--success">
      <div class="nk-enc-result-title">
        {t >= 4 ? '✓ CONTAINMENT COMPLETE' : t >= 3 ? '✓ SECURED' : '✓ Captured!'}
      </div>
      <div class="nk-enc-result-body">
        <span>{encounterData.species.name} joins your team.</span>
        <span>Team size: {resultDetail.team_size ?? '?'}</span>
        <span>Total captured: {resultDetail.creatures_captured ?? '?'}</span>
      </div>
      <button class="nk-btn" on:click={doContinue}>Continue →</button>
    </div>

  <!-- Result: capture failed -->
  {:else if phase === 'failed'}
    <div class="nk-enc-result nk-enc-result--fail">
      <div class="nk-enc-result-title">
        {t >= 3 ? '✗ CONTAINMENT FAILED' : '✗ Capture failed!'}
      </div>
      <div class="nk-enc-result-body">
        <span>{encounterData.species.name} escaped.</span>
      </div>
      <div class="nk-enc-actions">
        <button class="nk-btn nk-btn-capture" on:click={() => { phase = 'wild'; addLog('You ready another attempt…') }} >
          🔮 Try again
        </button>
        <button class="nk-btn-ghost" on:click={doFlee}>🚪 {fleeLabel}</button>
      </div>
    </div>
  {/if}

  <!-- Encounter log -->
  <div class="nk-enc-log">
    {#each captureLog as line}
      <div class="nk-enc-log-line">{line}</div>
    {/each}
  </div>

</div>

<style>
  .nk-encounter {
    display: flex; flex-direction: column; gap: 10px; padding: 4px 0;
  }

  .nk-enc-header {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: var(--nk-accent);
    text-transform: uppercase;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--nk-border);
  }

  .nk-enc-card {
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .nk-enc-name-row {
    display: flex; align-items: center; gap: 8px;
  }
  .nk-enc-name {
    font-size: 18px; font-weight: 700;
    color: var(--nk-text); flex: 1;
  }
  .nk-enc-level {
    font-size: 12px; color: var(--nk-text-muted);
    font-family: var(--font-mono);
  }
  .nk-enc-rarity {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.5px;
  }

  .nk-enc-type-row {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  }
  .nk-type-chip {
    padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 700;
    background: var(--nk-bg-node); color: var(--nk-accent);
    border: 1px solid var(--nk-accent-dim);
  }
  .nk-type-chip.secondary { color: var(--nk-text-muted); border-color: var(--nk-border); }
  .nk-enc-archetype {
    font-size: 10px; color: var(--nk-text-muted);
    padding: 2px 6px; border: 1px solid var(--nk-border);
    border-radius: 3px;
  }
  .nk-enc-passive {
    font-size: 10px; color: var(--nk-text-muted); font-style: italic;
    margin-left: auto;
  }

  .nk-enc-stats {
    display: grid; grid-template-columns: 1fr 1fr 1fr;
    gap: 6px 10px;
  }
  .nk-enc-stat {
    display: flex; flex-direction: column; gap: 2px;
  }
  .nk-enc-stat-label {
    font-size: 9px; color: var(--nk-text-muted);
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .nk-enc-stat-val {
    font-size: 12px; font-weight: 600;
    font-family: var(--font-mono); color: var(--nk-text);
  }
  .nk-enc-stat-bar {
    height: 3px; background: var(--nk-border);
    border-radius: 2px; overflow: hidden;
  }
  .nk-enc-stat-fill {
    height: 100%; background: var(--nk-accent);
    border-radius: 2px;
  }

  .nk-enc-meta {
    display: flex; gap: 12px; flex-wrap: wrap;
    padding-top: 8px;
    border-top: 1px solid var(--nk-border);
  }
  .nk-enc-meta-item { display: flex; flex-direction: column; gap: 2px; }
  .nk-enc-meta-label { font-size: 9px; color: var(--nk-text-muted); text-transform: uppercase; }
  .nk-enc-meta-val { font-size: 12px; font-weight: 600; color: var(--nk-text); }
  .nk-enc-temp[data-temperament="Aggressive"],
  .nk-enc-temp[data-temperament="HOSTILE"]    { color: var(--nk-danger, #f44); }
  .nk-enc-temp[data-temperament="Docile"],
  .nk-enc-temp[data-temperament="RECEPTIVE"]  { color: var(--nk-accent); }

  .nk-enc-actions {
    display: flex; gap: 8px;
  }
  .nk-btn-capture {
    background: #14532d; color: #bbf7d0;
    border: 1px solid #4ade80;
    padding: 8px 16px; border-radius: var(--radius);
    font-size: 13px; font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
  }
  .nk-btn-capture:hover:not(:disabled) { background: #166534; }
  .nk-btn-capture:disabled { opacity: 0.5; cursor: not-allowed; }

  .nk-enc-pending {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 12px;
    background: var(--nk-bg-card);
    border: 1px dashed var(--nk-border);
    border-radius: var(--radius);
    font-size: 13px; color: var(--nk-text-muted);
  }
  .nk-enc-spinner {
    font-size: 16px;
    animation: nk-spin 1.2s linear infinite;
    display: inline-block;
  }
  @keyframes nk-spin { to { transform: rotate(360deg); } }

  .nk-enc-result {
    display: flex; flex-direction: column; align-items: center; gap: 10px;
    padding: 16px;
    border: 2px solid var(--nk-border);
    border-radius: var(--radius);
    background: var(--nk-bg-card);
    text-align: center;
  }
  .nk-enc-result--success { border-color: var(--nk-accent); }
  .nk-enc-result--fail    { border-color: var(--nk-danger, #f44); }
  .nk-enc-result-title {
    font-size: 17px; font-weight: 900;
    letter-spacing: 2px; color: var(--nk-text);
  }
  .nk-enc-result-body {
    display: flex; flex-direction: column; gap: 4px;
    font-size: 12px; color: var(--nk-text-muted);
  }

  .nk-enc-log {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--nk-text-muted);
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    padding: 8px 10px;
    max-height: 90px;
    overflow-y: auto;
    display: flex; flex-direction: column; gap: 3px;
  }
  .nk-enc-log-line { line-height: 1.4; }
</style>
