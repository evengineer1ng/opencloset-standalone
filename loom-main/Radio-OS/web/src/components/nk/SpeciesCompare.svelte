<script lang="ts">
  import { nkSpecies, nkTierValue } from '../../lib/nkStore'
  import type { NKSpecies } from '../../lib/nkStore'

  $: tier = $nkTierValue

  let slotA: NKSpecies | null = null
  let slotB: NKSpecies | null = null
  let search = ''
  let showPicker: 'A' | 'B' | null = null

  $: filtered = search
    ? $nkSpecies.filter(s => s.name.toLowerCase().includes(search.toLowerCase()))
    : $nkSpecies

  function pick(slot: 'A' | 'B', sp: NKSpecies) {
    if (slot === 'A') slotA = sp
    else slotB = sp
    showPicker = null
    search = ''
  }

  const STAT_KEYS = ['HP', 'ATK', 'DEF', 'SPD', 'SPC']

  function statVal(sp: NKSpecies | null, key: string): number {
    if (!sp) return 0
    const stats = sp.base_stats ?? {}
    for (const [k, v] of Object.entries(stats)) {
      if (k.toUpperCase().includes(key)) return Number(v)
    }
    return 0
  }

  function rarityColor(r: string): string {
    const map: Record<string, string> = {
      COMMON: 'var(--nk-text-muted)',
      UNCOMMON: '#5c9e5c',
      RARE: '#4a7ccc',
      ULTRA_RARE: '#9a5cc6',
      LEGENDARY: '#c6a040',
    }
    return map[r] ?? 'var(--nk-text-muted)'
  }

  function compareAdvantage(a: number, b: number): 'A' | 'B' | 'tie' {
    if (a > b) return 'A'
    if (b > a) return 'B'
    return 'tie'
  }

  $: nameLabel = tier >= 3 ? 'SPECIES ID' : 'Name'
</script>

<div class="nk-compare">
  <div class="nk-compare-header">
    <span class="nk-compare-title">
      {tier >= 3 ? 'SPECIES COMPARISON MATRIX' : 'Compare Species'}
    </span>
  </div>

  <div class="nk-compare-slots">
    <!-- Slot A -->
    <div class="nk-compare-slot">
      <div class="nk-slot-label">
        {tier >= 3 ? 'SPECIMEN A' : 'Species A'}
      </div>
      {#if slotA}
        <div class="nk-slot-card">
          <div class="nk-slot-name" style="color:{rarityColor(slotA.rarity)}">{slotA.name}</div>
          <div class="nk-slot-types">
            <span class="nk-type-chip">{slotA.primary_type}</span>
            {#if slotA.secondary_type}<span class="nk-type-chip secondary">{slotA.secondary_type}</span>{/if}
          </div>
          <div class="nk-slot-rarity" style="color:{rarityColor(slotA.rarity)}">{slotA.rarity}</div>
          <button class="nk-btn-ghost nk-slot-change" on:click={() => showPicker = 'A'}>Change</button>
        </div>
      {:else}
        <button class="nk-slot-empty" on:click={() => showPicker = 'A'}>+ Select</button>
      {/if}
    </div>

    <div class="nk-compare-vs">VS</div>

    <!-- Slot B -->
    <div class="nk-compare-slot">
      <div class="nk-slot-label">
        {tier >= 3 ? 'SPECIMEN B' : 'Species B'}
      </div>
      {#if slotB}
        <div class="nk-slot-card">
          <div class="nk-slot-name" style="color:{rarityColor(slotB.rarity)}">{slotB.name}</div>
          <div class="nk-slot-types">
            <span class="nk-type-chip">{slotB.primary_type}</span>
            {#if slotB.secondary_type}<span class="nk-type-chip secondary">{slotB.secondary_type}</span>{/if}
          </div>
          <div class="nk-slot-rarity" style="color:{rarityColor(slotB.rarity)}">{slotB.rarity}</div>
          <button class="nk-btn-ghost nk-slot-change" on:click={() => showPicker = 'B'}>Change</button>
        </div>
      {:else}
        <button class="nk-slot-empty" on:click={() => showPicker = 'B'}>+ Select</button>
      {/if}
    </div>
  </div>

  <!-- Stat comparison -->
  {#if slotA || slotB}
    <div class="nk-compare-stats">
      {#each STAT_KEYS as sk}
        {@const va = statVal(slotA, sk)}
        {@const vb = statVal(slotB, sk)}
        {@const adv = compareAdvantage(va, vb)}
        <div class="nk-cmp-row">
          <span class="nk-cmp-val-a" class:winner={adv === 'A'}>{slotA ? va : '—'}</span>
          <span class="nk-cmp-key">{sk}</span>
          <span class="nk-cmp-val-b" class:winner={adv === 'B'}>{slotB ? vb : '—'}</span>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Species picker -->
  {#if showPicker}
    <div class="nk-picker-overlay">
      <div class="nk-picker-header">
        <span>{showPicker === 'A' ? 'Select Specimen A' : 'Select Specimen B'}</span>
        <button class="nk-btn-ghost" on:click={() => showPicker = null}>✕</button>
      </div>
      <input
        class="nk-picker-search"
        type="text"
        placeholder="Search species…"
        bind:value={search}
      />
      <div class="nk-picker-list">
        {#each filtered as sp}
          <!-- svelte-ignore a11y-click-events-have-key-events -->
          <div
            class="nk-picker-item"
            on:click={() => pick(showPicker!, sp)}
            role="button"
            tabindex="0"
            on:keydown={(e) => e.key === 'Enter' && pick(showPicker!, sp)}
          >
            <span class="nk-picker-name" style="color:{rarityColor(sp.rarity)}">{sp.name}</span>
            <span class="nk-picker-rarity">{sp.rarity}</span>
            <span class="nk-picker-type">{sp.primary_type}</span>
          </div>
        {/each}
        {#if filtered.length === 0}
          <div class="nk-picker-empty">No species found</div>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .nk-compare { display: flex; flex-direction: column; gap: 12px; }
  .nk-compare-header { display: flex; align-items: center; justify-content: space-between; }
  .nk-compare-title { font-size: 13px; font-weight: 700; color: var(--nk-text); }

  .nk-compare-slots {
    display: grid; grid-template-columns: 1fr auto 1fr; gap: 8px; align-items: start;
  }
  .nk-compare-slot { display: flex; flex-direction: column; gap: 6px; }
  .nk-slot-label { font-size: 10px; font-weight: 700; letter-spacing: 0.5px; color: var(--nk-text-muted); text-transform: uppercase; }
  .nk-slot-card {
    padding: 10px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    display: flex; flex-direction: column; gap: 4px;
  }
  .nk-slot-name { font-size: 13px; font-weight: 700; }
  .nk-slot-types { display: flex; gap: 4px; flex-wrap: wrap; }
  .nk-slot-rarity { font-size: 10px; }
  .nk-slot-change { font-size: 11px; align-self: flex-start; }
  .nk-slot-empty {
    height: 80px; display: flex; align-items: center; justify-content: center;
    border: 1px dashed var(--nk-border); border-radius: var(--radius);
    background: none; cursor: pointer; color: var(--nk-text-muted);
    font-size: 12px; transition: border-color 0.15s;
  }
  .nk-slot-empty:hover { border-color: var(--nk-accent); color: var(--nk-accent); }
  .nk-compare-vs {
    font-size: 14px; font-weight: 900; color: var(--nk-accent);
    display: flex; align-items: center; padding-top: 22px;
  }

  .nk-compare-stats {
    display: flex; flex-direction: column; gap: 6px;
    padding: 10px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
  }
  .nk-cmp-row {
    display: grid; grid-template-columns: 1fr auto 1fr;
    align-items: center; gap: 8px;
  }
  .nk-cmp-val-a, .nk-cmp-val-b {
    font-family: var(--font-mono, monospace); font-size: 13px;
    color: var(--nk-text-muted);
  }
  .nk-cmp-val-a { text-align: right; }
  .nk-cmp-val-b { text-align: left; }
  .nk-cmp-val-a.winner, .nk-cmp-val-b.winner { color: var(--nk-accent); font-weight: 700; }
  .nk-cmp-key {
    font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
    text-align: center; color: var(--nk-text-muted);
    min-width: 35px;
  }

  .nk-picker-overlay {
    display: flex; flex-direction: column; gap: 8px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    padding: 10px;
  }
  .nk-picker-header {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; font-weight: 700; color: var(--nk-text);
  }
  .nk-picker-search {
    background: var(--nk-bg);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius-sm);
    color: var(--nk-text); font-size: 12px;
    padding: 6px 8px; width: 100%; box-sizing: border-box;
    font-family: var(--font);
  }
  .nk-picker-search:focus { outline: none; border-color: var(--nk-accent); }
  .nk-picker-list {
    max-height: 200px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 4px;
  }
  .nk-picker-item {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px; border-radius: var(--radius-sm);
    cursor: pointer; border: 1px solid transparent;
    transition: background 0.12s;
  }
  .nk-picker-item:hover { background: var(--nk-bg-node); border-color: var(--nk-accent); }
  .nk-picker-name { flex: 1; font-size: 12px; font-weight: 600; }
  .nk-picker-rarity { font-size: 10px; color: var(--nk-text-muted); }
  .nk-picker-type { font-size: 10px; color: var(--nk-accent-dim); }
  .nk-picker-empty { font-size: 12px; color: var(--nk-text-muted); text-align: center; padding: 16px; }

  .nk-type-chip {
    font-size: 10px; font-weight: 700;
    padding: 1px 6px; border-radius: 999px;
    background: var(--nk-bg-node); color: var(--nk-accent);
    border: 1px solid var(--nk-accent-dim);
  }
  .nk-type-chip.secondary { color: var(--nk-text-muted); border-color: var(--nk-border); }
</style>
