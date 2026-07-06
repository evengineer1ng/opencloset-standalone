<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import {
    nkConnected, nkState, nkMap, nkMapStart, nkTier,
    nkFragments, nkSpecies, nkKnower, nkEvents, nkBusy,
    nkScreen, nkBattleTarget, nkActiveFragment, nkKnowerFrag,
    nkTierValue, nkPlayerLoc, nkIslandName, nkDiscovered,
    nkTrajectory, startNKPolling, stopNKPolling,
    nkRefreshFragments, nkRefreshSpecies, nkRefreshKnower,
    nkActions, nkPushEvent, nkRollEncounter, nkEncounterData,
    nkTrainers, fetchTrainers,
    type NKNode, type NKFragment, type NKKnower, type NKEncounterData, type NKTrainer,
  } from '../lib/nkStore'
  import * as nkAudio from '../lib/nkAudio'
  import NodeMap from '../components/nk/NodeMap.svelte'
  import BattleScreen from '../components/nk/BattleScreen.svelte'
  import EncounterScreen from '../components/nk/EncounterScreen.svelte'
  import FragmentReader from '../components/nk/FragmentReader.svelte'
  import IslandSelect from '../components/nk/IslandSelect.svelte'
  import NGPHistory from '../components/nk/NGPHistory.svelte'
  import TierEscalation from '../components/nk/TierEscalation.svelte'
  import KnowerDialogue from '../components/nk/KnowerDialogue.svelte'
  import SpeciesCompare from '../components/nk/SpeciesCompare.svelte'

  // Tabs within Neikos
  const innerTabs = [
    { id: 'explore',   label: '🗺️', name: 'Explore' },
    { id: 'species',   label: '🐾', name: 'Species' },
    { id: 'fragments', label: '📄', name: 'Fragments' },
    { id: 'knower',    label: '👁', name: 'Knower' },
  ]

  let activeInnerTab: 'explore' | 'species' | 'fragments' | 'knower' = 'explore'
  let audioUnlocked = false

  // ── Tier escalation ───────────────────────────────────────
  let showEscalation = false
  let escalationTier = 1
  let prevTier = 0

  $: {
    if (tier > prevTier && prevTier > 0) {
      escalationTier = tier
      showEscalation = true
      nkAudio.playTierEscalation(tier)
      setTimeout(() => { showEscalation = false }, 3600)
    }
    prevTier = tier
  }

  // ── Tier helpers ─────────────────────────────────────────
  $: tier = $nkTierValue
  $: tierLabel = ['', 'TIER I', 'TIER II', 'TIER III', 'TIER IV', 'TIER V'][tier] || 'TIER I'

  // Knower dialogue
  let showKnowerDialogue = false

  // Tier-dependent labels (the UI becomes "the system" at higher tiers)
  $: playerLabel     = tier >= 4 ? 'SUBJECT' : 'Explorer'
  $: islandLabel     = tier >= 5 ? `SITE [${$nkState?.seed ?? '?'}]` : $nkIslandName
  $: leagueLabel     = tier >= 5 ? 'BEHAVIORAL COMPLIANCE INDEX' : 'League Standings'
  $: trajLabel       = tier >= 4 ? 'SUBJECT PROFILE' : 'Profile'
  $: fragReaderStyle = tier >= 3 ? 'document' : 'journal'
  $: knowerLabel     = $nkKnower ? (tier >= 5 ? 'OBSERVER — AUTHORIZED' : $nkKnower.name) : '???'

  // ── Audio ────────────────────────────────────────────────
  function unlockAudio() {
    if (audioUnlocked) return
    nkAudio.ensureStarted()
    audioUnlocked = true
    applyNodeAmbient()
  }

  function applyNodeAmbient() {
    if (!audioUnlocked) return
    const node = $nkMap[$nkPlayerLoc]
    if (node) {
      nkAudio.setTier(tier)
      nkAudio.setNodeAmbient(node.node_type, node.is_relay_node, tier)
    }
  }

  $: if ($nkPlayerLoc && audioUnlocked) applyNodeAmbient()
  $: nkAudio.setTier(tier)

  // ── Node actions ─────────────────────────────────────────
  async function moveTo(nodeId: string) {
    unlockAudio()
    await nkActions.move(nodeId)
  }

  async function doExplore() {
    unlockAudio()
    await nkActions.explore()
    await nkRefreshFragments()
  }

  async function doEncounter() {
    unlockAudio()
    nkBusy.set(true)
    try {
      const enc = await nkRollEncounter()
      if (enc) {
        nkEncounterData.set(enc)
        nkScreen.set('encounter')
      } else {
        // No creature appeared — push a UI event so the log shows it
        nkPushEvent('no_encounter', { message: 'Nothing appeared.' })
      }
    } finally {
      nkBusy.set(false)
    }
  }

  function closeEncounter() {
    nkEncounterData.set(null)
    nkScreen.set('explore')
  }

  async function doBattle(trainerId: string) {
    unlockAudio()
    nkBattleTarget.set(trainerId)
    nkScreen.set('battle')
  }

  async function talkKnower(idx: number) {
    unlockAudio()
    if ($nkKnower && !$nkKnower.is_unlocked) {
      nkPushEvent('knower_locked', {})
      return
    }
    nkAudio.playKnowerUnlock()
    await nkActions.talkKnower(idx)
  }

  // ── Map helpers ──────────────────────────────────────────
  $: currentNode = $nkMap[$nkPlayerLoc] as NKNode | undefined
  $: neighborNodes = (currentNode?.neighbors ?? [])
    .map(id => $nkMap[id])
    .filter(Boolean) as NKNode[]

  function nodeTypeIcon(type: string): string {
    const icons: Record<string, string> = {
      WILD_ZONE: '🌿', CITY: '🏙️', FACILITY: '🏭',
      DUNGEON: '⚫', ANOMALY_ZONE: '⚠️', LANDMARK: '🗿',
      RELAY_NODE: '📡',
    }
    return icons[type] ?? '·'
  }

  function regionLabel(region: string): string {
    return region.replace(/_/g, ' ').toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase())
  }

  // ── Fragment helpers ─────────────────────────────────────
  $: undiscoveredCount = $nkFragments.filter(f => !f.discovered).length

  function fragmentTypeIcon(type: string): string {
    const icons: Record<string, string> = {
      REDACTED_LOG: '🗂️', RESEARCH_NOTE: '📝',
      AUDIO_ARTIFACT: '🔊', SPECIES_REGISTRY_GLITCH: '🧬',
      STATISTICAL_SUMMARY: '📊',
    }
    return icons[type] ?? '📄'
  }

  function openFragment(f: NKFragment) {
    nkAudio.playFragmentDiscover()
    nkActiveFragment.set(f)
  }

  function renderBody(body: string): string {
    // Replace [REDACTED] tokens with styled spans
    return body.replace(/\[REDACTED\]/g,
      '<span class="nk-redacted">[REDACTED]</span>')
  }

  // ── Events feed ─────────────────────────────────────────
  $: recentEvents = $nkEvents.slice(-8).reverse()

  function eventIcon(type: string): string {
    const icons: Record<string, string> = {
      moved: '👣', encounter: '⚡', battle_result: '⚔️',
      breed_result: '🧬', fragment_discovered: '📄',
      anomaly_event: '⚠️', research_discovery: '🔬',
      explored: '🔍', knower_dialogue: '👁', memory_echo: '🌀',
      gate_blocked: '🔒', error: '❗', captured: '✅',
      capture_failed: '❌', released: '🌿', claimed: '🤝',
      rested: '💤', level_up: '⬆️', tier_escalated: '🔺',
      island_initialized: '🏝️',
    }
    return icons[type] ?? '·'
  }

  function eventSummary(evt: { type: string; data: Record<string, any>; tick: number }): string {
    const d = evt.data ?? {}
    switch (evt.type) {
      case 'moved':           return `→ ${d.name ?? d.node_id ?? '?'}`
      case 'explored':        return `Explored ${d.node_name ?? d.node_id ?? '?'}`
      case 'encounter':       return `Wild ${d.species_name ?? '?'} Lv.${d.level ?? '?'}`
      case 'captured':        return `Caught ${d.species_name ?? '?'}!`
      case 'capture_failed':  return `${d.species_name ?? '?'} escaped`
      case 'released':        return `Released ${d.species_name ?? '?'}`
      case 'claimed':         return d.ok !== false ? `${d.species_name ?? '?'} joined!` : `Claim failed`
      case 'rested':          return `Team rested`
      case 'level_up':        return `${d.species_name ?? '?'} → Lv.${d.new_level ?? '?'}`
      case 'battle_result':   return `${d.winner === 'player' ? 'Won' : 'Lost'} vs ${d.opponent_name ?? '?'} (${d.turns ?? '?'}t)`
      case 'breed_result':    return `Offspring: ${d.species_name ?? '?'}`
      case 'fragment_discovered': return `Fragment: ${d.title ?? d.fragment_id ?? '?'}`
      case 'research_discovery':  return `Research insight`
      case 'anomaly_event':   return `Anomaly detected`
      case 'gate_blocked':    return `Gate locked (${d.gate_type ?? '?'})`
      case 'knower_dialogue': return `Knower spoke`
      case 'memory_echo':     return `Echo: ${d.hint ?? '…'}`
      case 'tier_escalated':  return `Tier → ${d.new_tier ?? '?'}`
      case 'island_initialized': return `Island: ${d.island_name ?? '?'}`
      default:                return evt.type.replace(/_/g, ' ')
    }
  }

  // ── Lifecycle ────────────────────────────────────────────
  onMount(async () => {
    startNKPolling()
    await nkRefreshFragments()
    await nkRefreshSpecies()
    await nkRefreshKnower()
    await fetchTrainers()
  })

  onDestroy(() => {
    stopNKPolling()
    nkAudio.stopAll()
  })
</script>

<div class="nk-root" data-tier={tier}>
  {#if showEscalation}
    <TierEscalation tier={escalationTier} on:done={() => showEscalation = false} />
  {/if}

  <!-- Header -->
  <header class="nk-header">
    <div class="nk-header-left">
      <span class="nk-island-name">{islandLabel}</span>
      {#if $nkState}
        <span class="nk-island-meta">
          {$nkState.climate} · {$nkState.node_count} nodes · {$nkState.species_count} species
        </span>
      {/if}
    </div>
    <div class="nk-header-right">
      <span class="nk-tier-pill" data-tier={tier}>{tierLabel}</span>
      {#if !$nkConnected}
        <span class="nk-offline">OFFLINE</span>
      {/if}
    </div>
  </header>

  <!-- Inner tab bar -->
  <div class="nk-tab-bar">
    {#each innerTabs as tab}
      <button
        class="nk-tab-btn"
        class:active={activeInnerTab === tab.id}
        on:click={() => activeInnerTab = tab.id as any}
      >
        {tab.label} {tab.name}
        {#if tab.id === 'fragments' && $nkDiscovered.length > 0}
          <span class="nk-badge">{$nkDiscovered.length}</span>
        {/if}
      </button>
    {/each}
  </div>

  <!-- Main content -->
  <div class="nk-body" on:click={unlockAudio} on:keydown={unlockAudio} role="region">

    {#if $nkScreen === 'battle'}
      {@const battleTrainer = $nkTrainers.find(t => t.trainer_id === $nkBattleTarget)}
      <BattleScreen
        opponentId={$nkBattleTarget ?? ''}
        opponentName={battleTrainer?.name ?? $nkBattleTarget ?? 'Trainer'}
      />
    {:else if $nkScreen === 'encounter' && $nkEncounterData}
      <EncounterScreen encounterData={$nkEncounterData} on:close={closeEncounter} />
    {:else if !$nkState}
      <IslandSelect />
    {:else}

    <!-- ── EXPLORE TAB ── -->
    {#if activeInnerTab === 'explore'}
      <div class="nk-explore">

        <!-- Current location -->
        {#if currentNode}
          <div class="nk-card nk-location-card">
            <div class="nk-location-header">
              <span class="nk-node-chip {currentNode.node_type}">
                {nodeTypeIcon(currentNode.node_type)} {currentNode.node_type.replace(/_/g, ' ')}
              </span>
              {#if currentNode.is_relay_node}
                <span class="nk-node-chip ANOMALY_ZONE nk-relay-glow">📡 RELAY NODE</span>
              {/if}
              <span class="nk-region">{regionLabel(currentNode.region)}</span>
            </div>
            <h2 class="nk-node-name">{currentNode.name}</h2>
            <div class="nk-biome-row">
              {#each ['Humid', 'Temp', 'Instab', 'Tox'] as label, i}
                <div class="nk-biome-stat">
                  <span class="nk-biome-label">{label}</span>
                  <div class="nk-biome-bar">
                    <div class="nk-biome-fill" style="width:{(currentNode.biome[i] ?? 0) * 100}%"></div>
                  </div>
                </div>
              {/each}
            </div>
            <div class="nk-location-actions">
              <button class="nk-btn" on:click={doExplore} disabled={$nkBusy}>
                🔍 Explore
              </button>
              <button class="nk-btn" on:click={() => doEncounter()} disabled={$nkBusy}>
                ⚡ Encounter
              </button>
            </div>
          </div>
        {:else}
          <div class="nk-card nk-empty">
            <p>Loading island…</p>
          </div>
        {/if}

        <!-- Node Map -->
        <NodeMap />

        <!-- Neighbors -->
        {#if neighborNodes.length > 0}
          <div class="nk-section-title">
            {tier >= 3 ? 'ADJACENT NODES' : 'Where to go'}
          </div>
          <div class="nk-neighbors">
            {#each neighborNodes as node}
              <button
                class="nk-neighbor-card nk-card"
                class:nk-relay-glow={node.is_relay_node}
                on:click={() => moveTo(node.node_id)}
                disabled={$nkBusy}
              >
                <span class="nk-neighbor-icon">{nodeTypeIcon(node.is_relay_node ? 'RELAY_NODE' : node.node_type)}</span>
                <div class="nk-neighbor-info">
                  <span class="nk-neighbor-name">{node.name}</span>
                  <span class="nk-neighbor-type">{node.node_type.replace(/_/g, ' ')}</span>
                </div>
                {#if node.gate}
                  <span class="nk-gate-lock">🔒</span>
                {/if}
              </button>
            {/each}
          </div>
        {/if}

        <!-- Player profile -->
        {#if $nkState}
          <div class="nk-section-title">{trajLabel}</div>
          <div class="nk-card nk-profile">
            <div class="nk-profile-grid">
              {#each [
                ['Competitive', $nkState.trajectory?.competitive_focus ?? 0],
                ['Exploration', $nkState.trajectory?.exploration_depth ?? 0],
                ['Research',    $nkState.trajectory?.research_investment ?? 0],
                ['Breeding',    $nkState.trajectory?.breeding_intensity ?? 0],
                ['Anomaly',     $nkState.trajectory?.anomaly_exposure ?? 0],
              ] as [string, number]}
                <div class="nk-traj-row">
                  <span class="nk-traj-label">{tier >= 4 ? $0.toUpperCase() : $0}</span>
                  <div class="nk-traj-bar">
                    <div class="nk-traj-fill" style="width:{Math.min(100, $1)}%"></div>
                  </div>
                  <span class="nk-traj-val">{Math.round($1)}</span>
                </div>
              {/each}
            </div>
            <div class="nk-profile-footer">
              <span>{tier >= 4 ? 'AXIS:' : 'Style:'} {$nkState.trajectory?.dominant_archetype ?? '—'}</span>
              <span>Battles: {$nkState.trajectory?.battles_won ?? 0}W / {$nkState.trajectory?.battles_lost ?? 0}L</span>
            </div>
          </div>
        {/if}

        <!-- NGP History -->
        <NGPHistory />

        <!-- Trainers (League) -->
        {#if $nkTrainers.length > 0}
          <div class="nk-section-title">
            {tier >= 3 ? 'REGISTERED COMBATANTS' : 'League Trainers'}
          </div>
          <div class="nk-trainer-list">
            {#each $nkTrainers.slice(0, 8) as tr (tr.trainer_id)}
              <button
                class="nk-card nk-trainer-card"
                on:click={() => doBattle(tr.trainer_id)}
                disabled={$nkBusy || ($nkState?.player_team_size ?? 0) === 0}
              >
                <div class="nk-trainer-info">
                  <span class="nk-trainer-name">{tier >= 3 ? `[${tr.trainer_id.slice(-4).toUpperCase()}]` : tr.name}</span>
                  <span class="nk-trainer-meta">{tr.tier} · {tr.team_size} team · {tr.wins}W/{tr.losses}L</span>
                </div>
                <span class="nk-trainer-rating">{Math.round(tr.rating)}</span>
                <span class="nk-trainer-challenge">⚔️</span>
              </button>
            {/each}
          </div>
          {#if ($nkState?.player_team_size ?? 0) === 0}
            <p class="nk-trainer-hint">Capture a creature first to challenge trainers</p>
          {/if}
        {/if}

        <!-- Event log -->
        {#if recentEvents.length > 0}          <div class="nk-section-title">
            {tier >= 3 ? 'ACTIVITY LOG' : 'Recent Events'}
          </div>
          <div class="nk-event-log">
            {#each recentEvents as evt}
              <div class="nk-event-row">
                <span class="nk-event-icon">{eventIcon(evt.type)}</span>
                <span class="nk-event-type">{eventSummary(evt)}</span>
                <span class="nk-event-tick">t{evt.tick}</span>
              </div>
            {/each}
          </div>
        {/if}

      </div>

    <!-- ── SPECIES TAB ── -->
    {:else if activeInnerTab === 'species'}
      <div class="nk-species-tab">
        <div class="nk-section-title">
          Discovered: {$nkState?.discovered_species ?? 0} / {$nkState?.species_count ?? '?'}
        </div>
        {#if $nkSpecies.length >= 2}
          <SpeciesCompare />
        {/if}
        {#if $nkSpecies.length === 0}
          <div class="nk-card nk-empty">
            <p>Explore nodes to discover species</p>
          </div>
        {:else}
          <div class="nk-species-list">
            {#each $nkSpecies as sp}
              <div class="nk-card nk-species-card">
                <div class="nk-species-header">
                  <span class="nk-species-name">{sp.name}</span>
                  <span class="nk-node-chip {sp.rarity}">{sp.rarity}</span>
                </div>
                <div class="nk-species-types">
                  <span class="nk-type-chip">{sp.primary_type}</span>
                  {#if sp.secondary_type}
                    <span class="nk-type-chip secondary">{sp.secondary_type}</span>
                  {/if}
                </div>
                <div class="nk-species-stats">
                  {#each Object.entries(sp.base_stats ?? {}) as [k, v]}
                    <div class="nk-stat-row">
                      <span class="nk-stat-key">{k}</span>
                      <span class="nk-stat-val">{v}</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </div>

    <!-- ── FRAGMENTS TAB ── -->
    {:else if activeInnerTab === 'fragments'}
      <div class="nk-fragments-tab">
        {#if $nkActiveFragment}
          <!-- Fragment reader -->
          <FragmentReader fragment={$nkActiveFragment} on:close={() => nkActiveFragment.set(null)} />
        {:else}
          <!-- Fragment list -->
          <div class="nk-section-title">
            {$nkDiscovered.length} / {$nkFragments.length} fragments recovered
          </div>
          {#if $nkFragments.length === 0}
            <div class="nk-card nk-empty">
              <p>Explore facilities, landmarks and dungeons to find fragments</p>
            </div>
          {:else}
            <div class="nk-fragment-list">
              {#each $nkFragments as frag}
                <button
                  class="nk-card nk-fragment-item"
                  class:discovered={frag.discovered}
                  class:locked={!frag.discovered}
                  on:click={() => openFragment(frag)}
                >
                  <span class="nk-frag-icon">{fragmentTypeIcon(frag.type)}</span>
                  <div class="nk-frag-info">
                    <span class="nk-frag-title">
                      {frag.discovered ? frag.title : '[ ENCRYPTED ]'}
                    </span>
                    <span class="nk-frag-meta">{frag.mountain_code} · {frag.type.replace(/_/g, ' ')}</span>
                  </div>
                  {#if frag.discovered}
                    <span class="nk-frag-read">→</span>
                  {:else}
                    <span class="nk-frag-locked-icon">🔒</span>
                  {/if}
                </button>
              {/each}
            </div>
          {/if}
        {/if}
      </div>

    <!-- ── KNOWER TAB ── -->
    {:else if activeInnerTab === 'knower'}
      <div class="nk-knower-tab">
        {#if !$nkKnower}
          <div class="nk-card nk-empty">
            <p>{tier >= 3 ? 'OBSERVER NOT LOCATED' : 'No trace of the one who knows'}</p>
            <p class="nk-frag-hint">Explore deeper to find them</p>
          </div>
        {:else}
          <div class="nk-card nk-knower-card" class:nk-relay-glow={$nkKnower.is_unlocked}>
            <div class="nk-knower-header">
              <span class="nk-knower-name">{knowerLabel}</span>
              <span class="nk-node-chip {$nkKnower.archetype}">{$nkKnower.archetype}</span>
            </div>
            <div class="nk-knower-location">
              Location: {tier >= 5 ? $nkKnower.location_node_id : ($nkKnower.is_unlocked ? $nkKnower.location_node_id : '???')}
            </div>

            {#if $nkKnower.is_unlocked}
              <!-- Dialogue fragments -->
              {#if showKnowerDialogue}
                <KnowerDialogue knower={$nkKnower} on:close={() => showKnowerDialogue = false} />
              {:else}
                <button class="nk-btn" on:click={() => { unlockAudio(); showKnowerDialogue = true }}>
                  Begin Dialogue
                </button>
              {/if}
            {:else}
              <div class="nk-knower-locked">
                <p>{tier >= 3 ? 'SUBJECT INSUFFICIENT CLEARANCE' : 'They are not ready to speak with you'}</p>
                <div class="nk-unlock-reqs">
                  {#each Object.entries($nkKnower.unlock_thresholds) as [k, v]}
                    <div class="nk-unlock-row">
                      <span>{k.replace(/_/g, ' ')}</span>
                      <span>≥ {v}</span>
                      <span class:met={($nkState?.trajectory?.[k] ?? 0) >= v}>
                        {Math.round($nkState?.trajectory?.[k] ?? 0)}
                      </span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

    {/if}

  </div>
</div>

<style>
  @import '../styles/neikos.css';

  .nk-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--nk-border);
    flex-shrink: 0;
    background: var(--nk-bg-card);
    transition: background 4s ease, border-color 4s ease;
    position: relative;
    z-index: 1;
  }
  .nk-island-name {
    font-size: 14px;
    font-weight: 700;
    color: var(--nk-text);
    transition: color 4s ease;
  }
  .nk-island-meta {
    font-size: 10px;
    color: var(--nk-text-muted);
    margin-left: 8px;
    transition: color 4s ease;
  }
  .nk-header-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .nk-offline {
    font-size: 10px;
    font-weight: 700;
    color: var(--nk-danger);
    letter-spacing: 1px;
  }

  .nk-tab-bar {
    display: flex;
    overflow-x: auto;
    scrollbar-width: none;
    border-bottom: 1px solid var(--nk-border);
    background: var(--nk-bg);
    flex-shrink: 0;
    position: relative;
    z-index: 1;
  }
  .nk-tab-bar::-webkit-scrollbar { display: none; }
  .nk-tab-btn {
    flex: 0 0 auto;
    padding: 9px 14px;
    font-size: 12px;
    font-weight: 500;
    color: var(--nk-text-muted);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    white-space: nowrap;
    transition: color 0.15s, border-color 0.15s;
    font-family: var(--font);
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .nk-tab-btn.active {
    color: var(--nk-accent);
    border-bottom-color: var(--nk-accent);
  }
  .nk-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    font-size: 9px;
    font-weight: 700;
    border-radius: 8px;
    background: var(--nk-accent);
    color: #000;
  }

  .nk-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    position: relative;
    z-index: 1;
  }

  /* ── Explore ── */
  .nk-explore { display: flex; flex-direction: column; gap: 10px; }

  .nk-location-card {}
  .nk-location-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .nk-region {
    font-size: 10px;
    color: var(--nk-text-muted);
    margin-left: auto;
  }
  .nk-node-name {
    font-size: 18px;
    font-weight: 700;
    color: var(--nk-text);
    margin-bottom: 10px;
    transition: color 4s ease;
  }

  .nk-biome-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 14px;
    margin-bottom: 12px;
  }
  .nk-biome-stat { display: flex; align-items: center; gap: 6px; }
  .nk-biome-label {
    font-size: 10px;
    color: var(--nk-text-muted);
    width: 42px;
    flex-shrink: 0;
  }
  .nk-biome-bar {
    flex: 1;
    height: 4px;
    background: var(--nk-border);
    border-radius: 2px;
    overflow: hidden;
  }
  .nk-biome-fill {
    height: 100%;
    background: var(--nk-accent);
    border-radius: 2px;
    transition: width 0.3s ease, background 4s ease;
  }
  .nk-location-actions {
    display: flex;
    gap: 8px;
    margin-top: 4px;
  }

  .nk-neighbors {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .nk-neighbor-card {
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    border: 1px solid var(--nk-border);
    background: var(--nk-bg-card);
    border-radius: var(--radius);
    padding: 10px 12px;
    text-align: left;
    width: 100%;
    transition: border-color 0.15s, background 0.15s;
  }
  .nk-neighbor-card:hover:not(:disabled) {
    border-color: var(--nk-accent);
    background: var(--nk-bg-node);
  }
  .nk-neighbor-card:disabled { opacity: 0.5; cursor: not-allowed; }
  .nk-neighbor-icon { font-size: 20px; }
  .nk-neighbor-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
  .nk-neighbor-name { font-size: 13px; font-weight: 600; color: var(--nk-text); }
  .nk-neighbor-type { font-size: 10px; color: var(--nk-text-muted); }
  .nk-gate-lock { font-size: 14px; }

  .nk-profile {}
  .nk-profile-grid { display: flex; flex-direction: column; gap: 7px; }
  .nk-traj-row { display: flex; align-items: center; gap: 8px; }
  .nk-traj-label { font-size: 11px; color: var(--nk-text-muted); width: 80px; flex-shrink: 0; }
  .nk-traj-bar {
    flex: 1; height: 4px; background: var(--nk-border);
    border-radius: 2px; overflow: hidden;
  }
  .nk-traj-fill {
    height: 100%;
    background: var(--nk-accent);
    transition: width 0.5s ease, background 4s ease;
  }
  .nk-traj-val { font-size: 11px; color: var(--nk-text-muted); width: 28px; text-align: right; }
  .nk-profile-footer {
    display: flex;
    justify-content: space-between;
    margin-top: 10px;
    font-size: 11px;
    color: var(--nk-text-muted);
    border-top: 1px solid var(--nk-border);
    padding-top: 8px;
  }

  .nk-event-log { display: flex; flex-direction: column; gap: 4px; }
  .nk-event-row {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 8px;
    background: var(--nk-bg-card);
    border-radius: var(--radius-sm);
    font-size: 11px;
    border: 1px solid var(--nk-border);
  }
  .nk-event-icon { font-size: 12px; }
  .nk-event-type { flex: 1; color: var(--nk-text-muted); }
  .nk-event-tick { font-family: var(--font-mono); color: var(--nk-text-muted); font-size: 10px; }

  /* ── Trainers ── */
  .nk-trainer-list { display: flex; flex-direction: column; gap: 6px; }
  .nk-trainer-card {
    display: flex; align-items: center; gap: 10px;
    cursor: pointer; text-align: left; width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--nk-border);
    background: var(--nk-bg-card);
    border-radius: var(--radius);
    transition: border-color 0.15s, background 0.15s;
  }
  .nk-trainer-card:hover:not(:disabled) {
    border-color: var(--nk-accent);
    background: var(--nk-bg-node);
  }
  .nk-trainer-card:disabled { opacity: 0.45; cursor: not-allowed; }
  .nk-trainer-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
  .nk-trainer-name { font-size: 13px; font-weight: 600; color: var(--nk-text); }
  .nk-trainer-meta { font-size: 10px; color: var(--nk-text-muted); }
  .nk-trainer-rating {
    font-family: var(--font-mono); font-size: 12px;
    color: var(--nk-accent); min-width: 36px; text-align: right;
  }
  .nk-trainer-challenge { font-size: 16px; }
  .nk-trainer-hint {
    font-size: 11px; color: var(--nk-text-muted);
    text-align: center; padding: 4px 0;
  }

  .nk-empty {
    text-align: center;
    padding: 32px;
    color: var(--nk-text-muted);
  }

  /* ── Species ── */
  .nk-species-tab { display: flex; flex-direction: column; gap: 10px; }
  .nk-species-list { display: flex; flex-direction: column; gap: 8px; }
  .nk-species-card {}
  .nk-species-header {
    display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 6px;
  }
  .nk-species-name { font-size: 14px; font-weight: 600; color: var(--nk-text); }
  .nk-species-types { display: flex; gap: 5px; margin-bottom: 8px; }
  .nk-type-chip {
    padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 700;
    background: var(--nk-bg-node); color: var(--nk-accent);
    border: 1px solid var(--nk-accent-dim);
  }
  .nk-type-chip.secondary { color: var(--nk-text-muted); border-color: var(--nk-border); }
  .nk-species-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; }
  .nk-stat-row { display: flex; justify-content: space-between; font-size: 11px; }
  .nk-stat-key { color: var(--nk-text-muted); }
  .nk-stat-val { font-family: var(--font-mono); color: var(--nk-text); }

  /* ── Fragments ── */
  .nk-fragments-tab { display: flex; flex-direction: column; gap: 10px; }
  .nk-fragment-list { display: flex; flex-direction: column; gap: 6px; }
  .nk-fragment-item {
    display: flex; align-items: center; gap: 10px;
    cursor: pointer; text-align: left; width: 100%;
    transition: border-color 0.15s;
  }
  .nk-fragment-item.discovered { border-color: var(--nk-accent-dim); }
  .nk-fragment-item.locked { opacity: 0.6; }
  .nk-fragment-item:hover { border-color: var(--nk-accent); }
  .nk-frag-icon { font-size: 18px; }
  .nk-frag-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
  .nk-frag-title { font-size: 13px; font-weight: 600; color: var(--nk-text); }
  .nk-frag-meta { font-size: 10px; color: var(--nk-text-muted); }
  .nk-frag-read { color: var(--nk-accent); }
  .nk-frag-locked-icon { font-size: 12px; }

  .nk-fragment-reader { display: flex; flex-direction: column; gap: 12px; }
  .nk-fragment-reader.document .nk-fragment-title {
    font-family: 'Courier New', monospace;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .nk-back-btn { align-self: flex-start; }
  .nk-fragment-type-row {
    display: flex; align-items: center; gap: 8px;
    font-size: 11px; color: var(--nk-text-muted);
  }
  .nk-fragment-type-label { text-transform: uppercase; letter-spacing: 0.5px; }
  .nk-mountain-code {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 2px 6px;
    border: 1px solid var(--nk-border);
    border-radius: 3px;
  }
  .nk-fragment-title { font-size: 16px; font-weight: 700; color: var(--nk-text); }
  .nk-fragment-locked {
    text-align: center; padding: 24px;
    color: var(--nk-text-muted);
    border: 1px dashed var(--nk-border);
    border-radius: var(--radius);
  }
  .nk-frag-hint { font-size: 11px; margin-top: 8px; }

  /* ── Knower ── */
  .nk-knower-tab { display: flex; flex-direction: column; gap: 10px; }
  .nk-knower-card {}
  .nk-knower-header {
    display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 6px;
  }
  .nk-knower-name { font-size: 15px; font-weight: 700; color: var(--nk-text); }
  .nk-knower-location {
    font-size: 11px; color: var(--nk-text-muted); margin-bottom: 12px;
    font-family: var(--font-mono);
  }
  .nk-knower-frags { display: flex; flex-direction: column; gap: 8px; }
  .nk-knower-frag-btn {
    display: flex; align-items: flex-start; gap: 10px;
    cursor: pointer; text-align: left; width: 100%;
    transition: border-color 0.15s;
  }
  .nk-knower-frag-btn:hover { border-color: var(--nk-accent); }
  .nk-knower-frag-idx { font-family: var(--font-mono); font-size: 10px; color: var(--nk-text-muted); flex-shrink: 0; }
  .nk-knower-frag-preview { font-size: 12px; color: var(--nk-text); line-height: 1.5; }
  .nk-knower-locked { text-align: center; padding: 16px; }
  .nk-knower-locked p { color: var(--nk-text-muted); font-size: 13px; }
  .nk-unlock-reqs { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
  .nk-unlock-row {
    display: flex; justify-content: space-between;
    font-size: 11px; color: var(--nk-text-muted);
    padding: 5px 8px;
    border: 1px solid var(--nk-border);
    border-radius: var(--radius-sm);
  }
  .nk-unlock-row span.met { color: var(--nk-accent); font-weight: 700; }
</style>
