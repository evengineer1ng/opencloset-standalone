<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { nkBusy, nkTierValue, nkEvents, nkScreen, nkChallengeBattle } from '../../lib/nkStore'
  import * as nkAudio from '../../lib/nkAudio'

  export let opponentId: string = ''
  export let opponentName: string = 'Trainer'

  interface BattleState {
    playerHP: number
    playerMaxHP: number
    playerName: string
    playerType: string
    playerLevel: number
    playerStatus: string
    opponentHP: number
    opponentMaxHP: number
    opponentName: string
    opponentType: string
    opponentLevel: number
    opponentStatus: string
    turn: number
    fieldState: string
    log: string[]
    result: null | { win: boolean; ratingChange: number; exp: number; turns: number }
  }

  let battle: BattleState = {
    playerHP: 100, playerMaxHP: 100, playerName: 'Your creature',
    playerType: 'NORMAL', playerLevel: 1, playerStatus: '',
    opponentHP: 100, opponentMaxHP: 100, opponentName: opponentName,
    opponentType: 'NORMAL', opponentLevel: 1, opponentStatus: '',
    turn: 0, fieldState: 'Open field', log: [], result: null,
  }

  // battleStarted prevents doFight from firing more than once per session
  let battleStarted = false

  $: hpLabel = $nkTierValue >= 3 ? 'INTEGRITY' : 'HP'
  $: trainerPrefix = $nkTierValue >= 3 ? 'AGENT' : 'Trainer'

  function hpPct(hp: number, max: number) {
    return Math.max(0, Math.min(100, (hp / max) * 100))
  }
  function hpColor(pct: number) {
    if (pct > 60) return 'var(--nk-accent, #0cf)'
    if (pct > 25) return '#f0a020'
    return 'var(--nk-danger, #f44)'
  }

  onMount(() => {
    nkAudio.playBattleStart()
  })

  async function doFight() {
    if ($nkBusy || battle.result || battleStarted) return
    battleStarted = true
    battle = { ...battle, log: [...battle.log, 'Challenging trainer…'] }

    const res = await nkChallengeBattle(opponentId)

    if (!res) {
      battle = { ...battle, log: [...battle.log, '⚠ Battle request failed — no team?'], result: null }
      battleStarted = false
      return
    }

    if (res.error) {
      battle = { ...battle, log: [...battle.log, `⚠ ${res.error}`], result: null }
      battleStarted = false
      return
    }

    const win = res.winner === 'player'
    const turns = res.turns ?? 0
    const ratingChange = res.player_rating ?? 0
    const opponentDisplayName = res.opponent_name ?? opponentName

    // Build a simple per-turn log from moves_used if available
    const movesLog: string[] = (res.moves_used ?? []).map(
      (m: string, i: number) => `Turn ${i + 1}: ${m}`
    )
    const finalLine = win ? '★ Victory!' : '✗ Defeated.'

    battle = {
      ...battle,
      turn: turns,
      opponentName: opponentDisplayName,
      opponentHP: win ? 0 : battle.opponentHP,
      playerHP: win ? battle.playerHP : 0,
      log: [...movesLog.slice(-6), finalLine],
      result: { win, ratingChange, exp: 0, turns },
    }

    if (win) nkAudio.playBattleWin()
  }

  async function doFlee() {
    if ($nkBusy) return
    nkScreen.set('explore')
  }

  function closeResult() {
    nkScreen.set('explore')
  }
</script>

<div class="nk-battle">
  <!-- Combatants row -->
  <div class="nk-battle-row">
    <!-- Player side -->
    <div class="nk-combatant left">
      <div class="nk-combatant-name">{battle.playerName}</div>
      <div class="nk-type-chip">{battle.playerType}</div>
      <div class="nk-level">Lv.{battle.playerLevel}</div>
      {#if battle.playerStatus}
        <span class="nk-status-badge">{battle.playerStatus}</span>
      {/if}
      <div class="nk-hp-label">{hpLabel}: {battle.playerHP}/{battle.playerMaxHP}</div>
      <div class="nk-hp-bar">
        <div class="nk-hp-fill"
          style="width:{hpPct(battle.playerHP, battle.playerMaxHP)}%; background:{hpColor(hpPct(battle.playerHP, battle.playerMaxHP))}"
        ></div>
      </div>
    </div>

    <!-- Center VS -->
    <div class="nk-battle-center">
      <div class="nk-vs">VS</div>
      <div class="nk-turn-counter">Turn {battle.turn}</div>
      <div class="nk-field-state">{battle.fieldState}</div>
    </div>

    <!-- Opponent side -->
    <div class="nk-combatant right">
      <div class="nk-combatant-name">
        {$nkTierValue >= 3 ? `${trainerPrefix} [${opponentId.slice(0,5)}]` : battle.opponentName}
      </div>
      <div class="nk-type-chip">{battle.opponentType}</div>
      <div class="nk-level">Lv.{battle.opponentLevel}</div>
      {#if battle.opponentStatus}
        <span class="nk-status-badge">{battle.opponentStatus}</span>
      {/if}
      <div class="nk-hp-label">{hpLabel}: {battle.opponentHP}/{battle.opponentMaxHP}</div>
      <div class="nk-hp-bar">
        <div class="nk-hp-fill"
          style="width:{hpPct(battle.opponentHP, battle.opponentMaxHP)}%; background:{hpColor(hpPct(battle.opponentHP, battle.opponentMaxHP))}"
        ></div>
      </div>
    </div>
  </div>

  <!-- Actions -->
  {#if !battle.result}
    <div class="nk-battle-actions">
      <button class="nk-btn" on:click={doFight} disabled={$nkBusy}>⚔️ Fight</button>
      <button class="nk-btn" disabled={$nkBusy}>🔄 Switch</button>
      <button class="nk-btn-ghost" on:click={doFlee} disabled={$nkBusy}>🚪 Flee</button>
    </div>
  {:else}
    <!-- Result panel -->
    <div class="nk-battle-result" class:win={battle.result.win}>
      <div class="nk-result-title">{battle.result.win ? 'VICTORY' : 'DEFEATED'}</div>
      <div class="nk-result-stats">
        <span>Turns: {battle.result.turns}</span>
        <span>Rating: {battle.result.ratingChange >= 0 ? '+' : ''}{battle.result.ratingChange}</span>
        {#if battle.result.exp > 0}<span>EXP: +{battle.result.exp}</span>{/if}
      </div>
      <button class="nk-btn" on:click={closeResult}>Continue →</button>
    </div>
  {/if}

  <!-- Turn log -->
  <div class="nk-battle-log">
    {#each battle.log.slice(-8) as line}
      <div class="nk-log-line">{line}</div>
    {/each}
  </div>

  <!-- Back button (no flee) -->
  <button class="nk-btn-ghost nk-back-btn" on:click={() => nkScreen.set('explore')}>← Back</button>
</div>

<style>
  .nk-battle { display: flex; flex-direction: column; gap: 12px; padding: 4px 0; }

  .nk-battle-row {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 12px;
    align-items: start;
  }
  .nk-combatant {
    display: flex; flex-direction: column; gap: 5px;
    padding: 12px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
  }
  .nk-combatant.right { text-align: right; }
  .nk-combatant-name { font-size: 14px; font-weight: 700; color: var(--nk-text); }
  .nk-level { font-size: 10px; color: var(--nk-text-muted); }
  .nk-status-badge {
    font-size: 10px; font-weight: 700;
    padding: 2px 6px; border-radius: 4px;
    background: var(--nk-danger); color: #fff;
    align-self: flex-start;
  }
  .nk-combatant.right .nk-status-badge { align-self: flex-end; }
  .nk-hp-label { font-size: 10px; color: var(--nk-text-muted); }
  .nk-hp-bar {
    height: 6px; background: var(--nk-border);
    border-radius: 3px; overflow: hidden;
  }
  .nk-hp-fill { height: 100%; border-radius: 3px; transition: width 0.4s ease; }

  .nk-battle-center {
    display: flex; flex-direction: column; align-items: center;
    gap: 4px; padding: 12px 8px;
  }
  .nk-vs { font-size: 18px; font-weight: 900; color: var(--nk-accent); }
  .nk-turn-counter { font-size: 10px; color: var(--nk-text-muted); font-family: var(--font-mono); }
  .nk-field-state { font-size: 10px; color: var(--nk-text-muted); text-align: center; }

  .nk-battle-actions {
    display: flex; gap: 8px; justify-content: center;
  }

  .nk-battle-result {
    display: flex; flex-direction: column; align-items: center; gap: 8px;
    padding: 16px;
    border: 2px solid var(--nk-border);
    border-radius: var(--radius);
    background: var(--nk-bg-card);
    text-align: center;
  }
  .nk-battle-result.win { border-color: var(--nk-accent); }
  .nk-result-title { font-size: 20px; font-weight: 900; letter-spacing: 3px; color: var(--nk-text); }
  .nk-result-stats { display: flex; gap: 12px; font-size: 12px; color: var(--nk-text-muted); }

  .nk-battle-log {
    font-family: var(--font-mono, monospace);
    font-size: 11px;
    color: var(--nk-text-muted);
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    padding: 8px 10px;
    max-height: 100px;
    overflow-y: auto;
    display: flex; flex-direction: column; gap: 3px;
  }
  .nk-log-line { line-height: 1.4; }

  .nk-back-btn { align-self: flex-start; font-size: 12px; }
  .nk-type-chip {
    font-size: 10px; font-weight: 700;
    padding: 1px 6px; border-radius: 999px;
    background: var(--nk-bg-node); color: var(--nk-accent);
    border: 1px solid var(--nk-accent-dim);
    align-self: flex-start;
  }
  .nk-combatant.right .nk-type-chip { align-self: flex-end; }
</style>
