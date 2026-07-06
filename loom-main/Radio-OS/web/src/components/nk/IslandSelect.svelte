<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { nkSeedPreviews, fetchIslandPreviews, nkActions } from '../../lib/nkStore'
  import type { NKIslandPreview } from '../../lib/nkStore'

  let loading = true
  let cacheReady = false
  let hoveredSeed: number | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null

  onMount(async () => {
    await fetchIslandPreviews()
    loading = false
    // If not all 100 seeds are in the store yet, keep polling until the
    // background cache finishes building.
    if ($nkSeedPreviews.length < 100) {
      pollTimer = setInterval(async () => {
        await fetchIslandPreviews()
        if ($nkSeedPreviews.length >= 100) {
          cacheReady = true
          if (pollTimer) clearInterval(pollTimer)
        }
      }, 1200)
    } else {
      cacheReady = true
    }
  })

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
  })

  function tierClass(tier: string): string {
    if (tier === 'TIER_V' || tier === 'TIER_IV') return 'tier-high'
    if (tier === 'TIER_III') return 'tier-mid'
    return 'tier-low'
  }

  function tierLabel(tier: string): string {
    return tier.replace('_', ' ')
  }

  async function selectIsland(seed: number) {
    await nkActions.reset(seed)
  }

  async function pickRandom() {
    const seed = Math.floor(Math.random() * 100) + 1
    await nkActions.reset(seed)
  }

  function typeColor(t: string): string {
    const m: Record<string, string> = {
      WILD_ZONE: '#4a7c59', CITY: '#5c7fa0', FACILITY: '#7a6b9e',
      DUNGEON: '#5a5a5a', ANOMALY_ZONE: '#a06c3d', LANDMARK: '#7a9e5c',
    }
    return m[t] ?? '#666'
  }
</script>

<div class="nk-island-select">
  <div class="nk-island-select-header">
    <h2>Choose Your Island</h2>
    <button class="nk-btn" on:click={pickRandom}>🎲 Random</button>
  </div>

  {#if loading}
    <div class="nk-island-loading">
      <div class="nk-spinner">Loading islands…</div>
    </div>
  {:else}
    {#if !cacheReady}
      <div class="nk-cache-progress">
        <div class="nk-cache-bar" style="width:{($nkSeedPreviews.length / 100) * 100}%"></div>
        <span class="nk-cache-label">Building island database… {$nkSeedPreviews.length}/100</span>
      </div>
    {/if}
    <div class="nk-island-grid">
      {#each $nkSeedPreviews as island}
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <div
          class="nk-island-card {tierClass(island.base_tier)}"
          class:hovered={hoveredSeed === island.seed}
          on:mouseenter={() => hoveredSeed = island.seed}
          on:mouseleave={() => hoveredSeed = null}
          on:click={() => selectIsland(island.seed)}
          role="button"
          tabindex="0"
          on:keydown={(e) => e.key === 'Enter' && selectIsland(island.seed)}
        >
          <div class="nk-island-seed">#{island.seed}</div>
          <div class="nk-island-name">{island.name}</div>
          <div class="nk-island-climate">{island.climate}</div>
          <div class="nk-island-tier-pill {tierClass(island.base_tier)}">{tierLabel(island.base_tier)}</div>
          {#if hoveredSeed === island.seed}
            <div class="nk-island-detail">
              <span>{island.node_count} nodes</span>
              <div class="nk-island-types">
                {#each island.active_types as t}
                  <span class="nk-type-dot" style="background:{typeColor(t)}" title={t}></span>
                {/each}
              </div>
            </div>
          {:else}
            <div class="nk-island-types">
              {#each island.active_types as t}
                <span class="nk-type-dot" style="background:{typeColor(t)}" title={t}></span>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .nk-island-select {
    display: flex; flex-direction: column; gap: 12px;
    padding: 4px 0;
    min-height: 400px;
  }
  .nk-island-select-header {
    display: flex; align-items: center; justify-content: space-between;
  }
  .nk-island-select-header h2 {
    font-size: 16px; font-weight: 700; color: var(--nk-text); margin: 0;
  }
  .nk-island-loading {
    display: flex; align-items: center; justify-content: center;
    flex: 1; padding: 40px;
    color: var(--nk-text-muted); font-size: 13px;
  }
  .nk-spinner { animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  .nk-cache-progress {
    position: relative;
    height: 18px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    margin-bottom: 4px;
  }
  .nk-cache-bar {
    position: absolute; top: 0; left: 0; height: 100%;
    background: var(--nk-accent-dim);
    transition: width 0.6s ease;
  }
  .nk-cache-label {
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    font-size: 9px; color: var(--nk-text-muted); font-family: var(--font-mono);
    white-space: nowrap;
  }

  .nk-island-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
    gap: 6px;
    overflow-y: auto;
    max-height: 520px;
  }
  .nk-island-card {
    display: flex; flex-direction: column; gap: 3px;
    padding: 8px 6px;
    border-radius: var(--radius);
    border: 1px solid var(--nk-border);
    background: var(--nk-bg-card);
    cursor: pointer;
    transition: transform 0.12s, border-color 0.15s;
    min-height: 70px;
  }
  .nk-island-card:hover, .nk-island-card.hovered {
    transform: scale(1.04);
    z-index: 1;
  }
  .nk-island-card.tier-low  { border-color: #2a5c3a; }
  .nk-island-card.tier-mid  { border-color: #2a3f5c; }
  .nk-island-card.tier-high { border-color: #5c2a2a; }

  .nk-island-card:hover.tier-low  { border-color: #4a8c5a; }
  .nk-island-card:hover.tier-mid  { border-color: #4a6f9c; }
  .nk-island-card:hover.tier-high { border-color: #9c4a4a; }

  .nk-island-seed { font-size: 9px; color: var(--nk-text-muted); font-family: var(--font-mono); }
  .nk-island-name { font-size: 10px; font-weight: 700; color: var(--nk-text); line-height: 1.2; }
  .nk-island-climate { font-size: 9px; color: var(--nk-text-muted); }

  .nk-island-tier-pill {
    font-size: 8px; font-weight: 700; letter-spacing: 0.5px;
    padding: 1px 4px; border-radius: 3px; align-self: flex-start;
  }
  .nk-island-tier-pill.tier-low  { background: #1a3a22; color: #6c9e7c; }
  .nk-island-tier-pill.tier-mid  { background: #1a2a3a; color: #5c8ab0; }
  .nk-island-tier-pill.tier-high { background: #3a1a1a; color: #b05c5c; }

  .nk-island-detail { font-size: 9px; color: var(--nk-text-muted); }
  .nk-island-types { display: flex; gap: 3px; flex-wrap: wrap; margin-top: 2px; }
  .nk-type-dot {
    width: 7px; height: 7px; border-radius: 50%;
    opacity: 0.8;
  }
</style>
