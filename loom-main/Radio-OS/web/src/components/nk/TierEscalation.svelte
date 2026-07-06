<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte'
  export let tier: number = 2

  const dispatch = createEventDispatcher()

  const tierMessages: Record<number, string> = {
    2: 'ANOMALOUS BEHAVIOR FLAGGED',
    3: 'ADMINISTRATIVE REVIEW INITIATED',
    4: 'CONTAINMENT PROTOCOLS ACTIVE',
    5: 'FULL DISCLOSURE MODE ENGAGED',
  }

  const tierNames: Record<number, string> = {
    2: 'TIER II', 3: 'TIER III', 4: 'TIER IV', 5: 'TIER V',
  }

  let phase: 'in' | 'hold' | 'out' = 'in'

  onMount(() => {
    setTimeout(() => { phase = 'hold' }, 300)
    setTimeout(() => { phase = 'out' }, 2800)
    setTimeout(() => { dispatch('done') }, 3600)
  })
</script>

<div class="nk-tier-escalation" class:phase-in={phase === 'in'} class:phase-hold={phase === 'hold'} class:phase-out={phase === 'out'}>
  <div class="nk-tier-esc-content">
    <div class="nk-tier-esc-msg">{tierMessages[tier] ?? 'TIER SHIFT DETECTED'}</div>
    <div class="nk-tier-esc-line"></div>
    <div class="nk-tier-esc-tier">{tierNames[tier] ?? `TIER ${tier}`}</div>
  </div>
</div>

<style>
  .nk-tier-escalation {
    position: absolute;
    inset: 0;
    z-index: 1000;
    background: var(--nk-bg, #0a0a0a);
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.3s ease;
  }
  .nk-tier-escalation.phase-in  { opacity: 0; animation: esc-fadein 0.3s forwards; }
  .nk-tier-escalation.phase-hold { opacity: 1; }
  .nk-tier-escalation.phase-out { animation: esc-fadeout 0.8s forwards; }

  @keyframes esc-fadein {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  @keyframes esc-fadeout {
    from { opacity: 1; }
    to   { opacity: 0; }
  }

  .nk-tier-esc-content {
    display: flex; flex-direction: column; align-items: center; gap: 12px;
    padding: 32px;
    text-align: center;
  }
  .nk-tier-esc-msg {
    font-size: 20px;
    font-family: var(--font-mono, 'Courier New', monospace);
    font-weight: 700;
    letter-spacing: 3px;
    color: var(--nk-text, #eee);
    text-transform: uppercase;
  }
  .nk-tier-esc-line {
    width: 200px;
    height: 1px;
    background: var(--nk-border, #444);
  }
  .nk-tier-esc-tier {
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    font-variant: small-caps;
    letter-spacing: 4px;
    color: var(--nk-text-muted, #888);
    text-transform: uppercase;
  }
</style>
