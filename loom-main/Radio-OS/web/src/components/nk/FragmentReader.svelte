<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte'
  import { nkTierValue } from '../../lib/nkStore'
  import * as nkAudio from '../../lib/nkAudio'
  import type { NKFragment } from '../../lib/nkStore'

  export let fragment: NKFragment

  const dispatch = createEventDispatcher()

  $: tier = $nkTierValue
  $: fragReaderStyle = tier >= 3 ? 'document' : 'journal'
  $: isAudioArtifact = fragment.type === 'AUDIO_ARTIFACT'

  onMount(() => {
    nkAudio.playFragmentAudio(fragment.type, fragment.body ?? '')
  })

  function renderBody(body: string): string {
    return body.replace(/\[REDACTED\]/g,
      '<span class="nk-redacted">[REDACTED]</span>')
  }

  function fragmentTypeIcon(type: string): string {
    const icons: Record<string, string> = {
      REDACTED_LOG: '🗂️', RESEARCH_NOTE: '📝',
      AUDIO_ARTIFACT: '🔊', SPECIES_REGISTRY_GLITCH: '🧬',
      STATISTICAL_SUMMARY: '📊',
    }
    return icons[type] ?? '📄'
  }
</script>

<div class="nk-fragment-reader" class:document={fragReaderStyle === 'document'} class:audio-artifact={isAudioArtifact}>
  {#if isAudioArtifact}
    <div class="nk-static-overlay" aria-hidden="true"></div>
  {/if}

  <button class="nk-btn-ghost nk-back-btn" on:click={() => dispatch('close')}>
    ← {tier >= 3 ? 'CLOSE FILE' : 'Back'}
  </button>

  <div class="nk-fragment-type-row">
    <span>{fragmentTypeIcon(fragment.type)}</span>
    <span class="nk-fragment-type-label">{fragment.type.replace(/_/g, ' ')}</span>
    <span class="nk-mountain-code">{fragment.mountain_code}</span>
  </div>

  <h3 class="nk-fragment-title">{fragment.title}</h3>

  {#if fragment.body}
    <div class="nk-fragment-body">
      {@html renderBody(fragment.body)}
    </div>
  {:else}
    <div class="nk-fragment-locked">
      <p>[ CONTENT LOCKED ]</p>
      <p class="nk-frag-hint">
        {#each Object.entries(fragment.unlock_condition) as [k, v]}
          Requires: {k.replace(/_/g, ' ')} ≥ {v}&nbsp;
        {/each}
      </p>
    </div>
  {/if}
</div>

<style>
  .nk-fragment-reader {
    display: flex; flex-direction: column; gap: 12px;
    position: relative;
  }
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

  /* Static glitch overlay for AUDIO_ARTIFACT */
  .nk-static-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 0;
    animation: static-glitch 0.15s steps(1) infinite;
    opacity: 0.04;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(255,255,255,0.3) 2px,
      rgba(255,255,255,0.3) 4px
    );
  }
  @keyframes static-glitch {
    0%   { transform: translateY(0); }
    25%  { transform: translateY(-2px); }
    50%  { transform: translateY(1px); }
    75%  { transform: translateY(-1px); }
    100% { transform: translateY(0); }
  }
  .audio-artifact .nk-fragment-body {
    font-family: var(--font-mono, monospace);
    filter: blur(0.2px);
  }
</style>
