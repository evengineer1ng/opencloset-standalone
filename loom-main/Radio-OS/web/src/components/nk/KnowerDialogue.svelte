<script lang="ts">
  import { onMount, onDestroy, createEventDispatcher } from 'svelte'
  import { nkTierValue, nkSpeak, nkKnowerDialogue, nkFetchKnowerFragment } from '../../lib/nkStore'
  import * as nkAudio from '../../lib/nkAudio'
  import type { NKKnower } from '../../lib/nkStore'

  export let knower: NKKnower

  const dispatch = createEventDispatcher()

  $: tier = $nkTierValue
  $: archetype = knower.archetype

  const archetypeIcons: Record<string, string> = {
    ELDER: '🧙', SCIENTIST: '🔬', REBEL: '🗡️',
    CARTOGRAPHER: '🗺️', GHOST: '👁️',
    RETIRED_ARCHIVIST: '📜', ANONYMOUS_SIGNAL: '📡',
    ISOLATED_RESEARCHER: '🔬', ELDERLY_HERMIT: '🧙',
    REGIONAL_GYM_LEADER: '🏆',
  }
  const archetypeCodes: Record<string, string> = {
    ELDER: 'ELD-7', SCIENTIST: 'SCI-2', REBEL: 'RBL-4',
    CARTOGRAPHER: 'CART-1', GHOST: 'GHO-9',
    RETIRED_ARCHIVIST: 'ARC-3', ANONYMOUS_SIGNAL: 'SIG-0',
    ISOLATED_RESEARCHER: 'RES-5', ELDERLY_HERMIT: 'HRM-2',
    REGIONAL_GYM_LEADER: 'GYM-1',
  }

  let fragIdx = 0
  // totalFrags: use fragment_count from the knower prop as initial value.
  // The server populates fragment_count on /api/knower.
  let totalFrags: number = knower.fragment_count ?? 1
  let displayText = ''
  let currentFrag = ''
  let isTyping = false
  let isLoading = false
  let loadError = false
  let typeInterval: ReturnType<typeof setInterval> | null = null
  let hasPlayedUnlock = false

  function startTyping(text: string) {
    displayText = ''
    isTyping = true
    let i = 0
    if (typeInterval) clearInterval(typeInterval)
    typeInterval = setInterval(() => {
      if (i < text.length) {
        displayText += text[i++]
      } else {
        isTyping = false
        if (typeInterval) { clearInterval(typeInterval); typeInterval = null }
      }
    }, 30)
  }

  async function showFrag(idx: number) {
    fragIdx = idx
    isLoading = true
    loadError = false
    const result = await nkFetchKnowerFragment(idx)
    isLoading = false
    if (!result) {
      loadError = true
      currentFrag = ''
      displayText = '[Dialogue unavailable]'
      return
    }
    currentFrag = result.fragment
    totalFrags = result.total_fragments
    if (idx === 0 && !hasPlayedUnlock) {
      nkAudio.playKnowerUnlock()
      hasPlayedUnlock = true
    }
    startTyping(result.fragment)
    nkKnowerDialogue.set({ active: true, fragmentIndex: idx, currentText: result.fragment, isTyping })
  }

  function skipTyping() {
    if (typeInterval) { clearInterval(typeInterval); typeInterval = null }
    displayText = currentFrag
    isTyping = false
  }

  async function nextFrag() {
    if (fragIdx + 1 < totalFrags) {
      await showFrag(fragIdx + 1)
    } else {
      dispatch('close')
    }
  }

  function speakVoice() {
    nkSpeak(currentFrag, archetype, knower.name)
  }

  onMount(() => showFrag(0))

  onDestroy(() => {
    if (typeInterval) clearInterval(typeInterval)
    nkKnowerDialogue.set(null)
  })
</script>

<div class="nk-knower-dialogue" class:cold={tier >= 3} class:scan={tier >= 5}>
  <!-- Portrait area -->
  <div class="nk-kd-portrait">
    <span class="nk-kd-icon">{archetypeIcons[archetype] ?? '👤'}</span>
    <div class="nk-kd-identity">
      <div class="nk-kd-name">
        {tier >= 3 ? `DESIGNATION: ${knower.name}` : knower.name}
      </div>
      <div class="nk-kd-archetype">
        {tier >= 3 ? (archetypeCodes[archetype] ?? archetype) : archetype}
      </div>
    </div>
    <button class="nk-btn-ghost nk-kd-voice-btn" on:click={speakVoice} title="Voice">🔊</button>
  </div>

  <!-- Dialogue bubble -->
  <div class="nk-kd-bubble">
    {#if isLoading}
      <p class="nk-kd-text nk-kd-loading">…</p>
    {:else if loadError}
      <p class="nk-kd-text nk-kd-error">[Signal lost. Try again.]</p>
    {:else}
      <p class="nk-kd-text">{displayText}</p>
      {#if isTyping}
        <button class="nk-btn-ghost nk-kd-skip" on:click={skipTyping}>Skip →</button>
      {/if}
    {/if}
  </div>

  <!-- Fragment dots: use totalFrags, not a local array -->
  <div class="nk-kd-dots">
    {#each Array(totalFrags) as _, i}
      <span class="nk-kd-dot" class:active={i === fragIdx} class:visited={i < fragIdx}></span>
    {/each}
  </div>

  <!-- Navigation -->
  <div class="nk-kd-actions">
    {#if !isTyping}
      {#if fragIdx + 1 < totalFrags}
        <button class="nk-btn" on:click={nextFrag}>Continue →</button>
      {:else}
        <button class="nk-btn" on:click={() => dispatch('close')}>End conversation</button>
      {/if}
    {/if}
    <button class="nk-btn-ghost" on:click={() => dispatch('close')}>← Back</button>
  </div>

  <!-- Locked fragments hint -->
  {#if totalFrags < 8}
    <div class="nk-kd-locked-hint">
      {8 - totalFrags} more conversation{8 - totalFrags !== 1 ? 's' : ''} require deeper exploration
    </div>
  {/if}
</div>

<style>
  .nk-knower-dialogue {
    display: flex; flex-direction: column; gap: 12px;
    padding: 4px 0;
    font-size: 13px;
    transition: all 4s ease;
  }
  .nk-kd-portrait {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
  }
  .nk-kd-icon { font-size: 28px; }
  .nk-kd-identity { flex: 1; display: flex; flex-direction: column; gap: 2px; }
  .nk-kd-name { font-size: 14px; font-weight: 700; color: var(--nk-text); }
  .nk-kd-archetype { font-size: 10px; color: var(--nk-text-muted); letter-spacing: 0.5px; }
  .nk-kd-voice-btn { font-size: 16px; }

  .nk-kd-bubble {
    padding: 14px;
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    min-height: 80px;
    position: relative;
  }
  .nk-kd-text {
    margin: 0;
    line-height: 1.7;
    color: var(--nk-text);
    white-space: pre-wrap;
  }
  .nk-kd-loading {
    opacity: 0.5;
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100%{opacity:0.5} 50%{opacity:0.15} }
  .nk-kd-error { color: var(--nk-accent); opacity: 0.7; font-size: 11px; }
  .nk-kd-skip {
    position: absolute; bottom: 6px; right: 8px;
    font-size: 11px; opacity: 0.7;
  }

  .nk-kd-dots {
    display: flex; gap: 6px; justify-content: center;
  }
  .nk-kd-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--nk-border);
    transition: background 0.3s;
  }
  .nk-kd-dot.active  { background: var(--nk-accent); }
  .nk-kd-dot.visited { background: var(--nk-accent-dim); }

  .nk-kd-actions {
    display: flex; gap: 8px; align-items: center;
  }

  .nk-kd-locked-hint {
    font-size: 10px; color: var(--nk-text-muted);
    text-align: center; padding: 6px;
    border: 1px dashed var(--nk-border);
    border-radius: var(--radius-sm);
    opacity: 0.7;
  }

  /* Cold tier styling */
  .cold .nk-kd-name { font-family: var(--font-mono, monospace); letter-spacing: 0.5px; }
  .cold .nk-kd-text { font-family: var(--font-mono, monospace); font-size: 12px; }

  /* Tier 5 scan lines */
  .scan .nk-kd-portrait {
    position: relative;
    overflow: hidden;
  }
  .scan .nk-kd-portrait::after {
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: repeating-linear-gradient(
      0deg, transparent, transparent 3px,
      rgba(0,255,200,0.03) 3px, rgba(0,255,200,0.03) 4px
    );
    animation: scan-anim 8s linear infinite;
  }
  @keyframes scan-anim {
    0%   { background-position: 0 0; }
    100% { background-position: 0 40px; }
  }
</style>
