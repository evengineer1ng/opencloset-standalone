<script lang="ts">
  import { onMount } from 'svelte'
  import { nkProfile, fetchNGPProfile, nkTierValue } from '../../lib/nkStore'

  onMount(() => { fetchNGPProfile() })

  $: tier = $nkTierValue
  $: profile = $nkProfile
  $: runCount = profile?.run_count ?? 0
  $: axis = profile?.dominant_axis ?? ''
  $: priorScores = profile?.prior_run_scores ?? {}
  $: echoSeeds = profile?.echo_nodes ?? []
  $: isRestricted = tier >= 5

  const axisIcons: Record<string, string> = {
    COMPETITIVE:    '⚔️',
    CURIOUS:        '🔍',
    RESEARCHER:     '🔬',
    BREEDER:        '🧬',
    ANOMALY_SEEKER: '⚠️',
    BALANCED:       '⚖️',
  }

  const scoreKeys = [
    ['competitive_focus', 'Competitive'],
    ['exploration_depth', 'Exploration'],
    ['research_investment', 'Research'],
    ['breeding_intensity', 'Breeding'],
    ['anomaly_exposure', 'Anomaly'],
  ] as const
</script>

<div class="nk-ngp-history" class:restricted={isRestricted}>
  {#if isRestricted}
    <div class="nk-ngp-restricted-label">
      SUBJECT BEHAVIORAL DOSSIER — RESTRICTED
    </div>
  {/if}

  {#if !profile}
    <div class="nk-ngp-empty">
      <p>No prior expedition data. This is your first island.</p>
    </div>
  {:else}
    <div class="nk-ngp-run-header">
      <span class="nk-ngp-run-count">
        {tier >= 3 ? `EXPEDITION RECORD #${runCount}` : `This is run #${runCount}`}
      </span>
      {#if axis}
        <span class="nk-ngp-axis-badge" title={axis}>
          {axisIcons[axis] ?? '⚖️'} {tier >= 3 ? axis : axis.replace(/_/g, ' ').toLowerCase()}
        </span>
      {/if}
    </div>

    {#if Object.keys(priorScores).length > 0}
      <div class="nk-ngp-section-title">
        {tier >= 3 ? 'PRIOR RUN TRAJECTORY SCORES' : 'Last run profile'}
      </div>
      <div class="nk-ngp-scores">
        {#each scoreKeys as [key, label]}
          {@const val = priorScores[key] ?? 0}
          <div class="nk-ngp-score-row">
            <span class="nk-ngp-score-label">{tier >= 4 ? key.toUpperCase() : label}</span>
            <div class="nk-ngp-score-bar">
              <div class="nk-ngp-score-fill" style="width:{Math.min(100, val)}%"></div>
            </div>
            <span class="nk-ngp-score-val">{Math.round(val)}</span>
          </div>
        {/each}
      </div>
    {/if}

    {#if echoSeeds.length > 0}
      <div class="nk-ngp-section-title">
        {tier >= 3 ? 'MEMORY ECHO NODES' : 'Memory traces'}
      </div>
      <div class="nk-ngp-echoes">
        {echoSeeds.join(', ')}
      </div>
    {/if}

    {#if runCount >= 3}
      <div class="nk-ngp-system-msg">
        {tier >= 3
          ? 'BEHAVIORAL PROFILE ESTABLISHED. PATTERN RECOGNITION ACTIVE.'
          : 'Behavioral profile established. Pattern recognition active.'}
      </div>
    {/if}

    {#if runCount >= 5 || isRestricted}
      <div class="nk-ngp-dossier">
        <pre>{JSON.stringify(profile, null, 2)}</pre>
      </div>
    {/if}
  {/if}
</div>

<style>
  .nk-ngp-history {
    display: flex; flex-direction: column; gap: 10px;
    font-size: 12px;
  }
  .nk-ngp-history.restricted {
    background: var(--nk-frag-bg, var(--nk-bg-card));
    border: 1px solid var(--nk-danger, #f44);
    border-radius: var(--radius);
    padding: 12px;
  }
  .nk-ngp-restricted-label {
    font-size: 10px; font-weight: 700; letter-spacing: 2px;
    color: var(--nk-danger, #f44); text-transform: uppercase;
    border-bottom: 1px solid var(--nk-danger, #f44);
    padding-bottom: 6px; margin-bottom: 4px;
  }
  .nk-ngp-empty {
    text-align: center; padding: 24px;
    color: var(--nk-text-muted);
  }
  .nk-ngp-run-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 8px;
  }
  .nk-ngp-run-count { font-size: 14px; font-weight: 700; color: var(--nk-text); }
  .nk-ngp-axis-badge {
    font-size: 12px; padding: 3px 8px;
    background: var(--nk-bg-node); border: 1px solid var(--nk-border);
    border-radius: 999px; color: var(--nk-accent);
  }
  .nk-ngp-section-title {
    font-size: 10px; font-weight: 700; letter-spacing: 1px;
    text-transform: uppercase; color: var(--nk-text-muted);
    border-bottom: 1px solid var(--nk-border); padding-bottom: 4px;
  }
  .nk-ngp-scores { display: flex; flex-direction: column; gap: 6px; }
  .nk-ngp-score-row { display: flex; align-items: center; gap: 8px; }
  .nk-ngp-score-label { font-size: 10px; color: var(--nk-text-muted); width: 90px; flex-shrink: 0; }
  .nk-ngp-score-bar {
    flex: 1; height: 4px; background: var(--nk-border);
    border-radius: 2px; overflow: hidden;
  }
  .nk-ngp-score-fill { height: 100%; background: var(--nk-accent); border-radius: 2px; }
  .nk-ngp-score-val { font-size: 10px; color: var(--nk-text-muted); width: 28px; text-align: right; }
  .nk-ngp-echoes {
    font-family: var(--font-mono, monospace); font-size: 11px;
    color: var(--nk-accent); padding: 6px 8px;
    background: var(--nk-bg-card); border: 1px solid var(--nk-border);
    border-radius: var(--radius-sm);
    word-break: break-all;
  }
  .nk-ngp-system-msg {
    font-size: 11px; font-family: var(--font-mono, monospace);
    color: var(--nk-text-muted); padding: 6px 8px;
    border-left: 2px solid var(--nk-accent); letter-spacing: 0.3px;
  }
  .nk-ngp-dossier {
    background: var(--nk-bg-card);
    border: 1px solid var(--nk-border);
    border-radius: var(--radius);
    padding: 8px;
    overflow: auto;
    max-height: 200px;
  }
  .nk-ngp-dossier pre {
    font-family: var(--font-mono, monospace);
    font-size: 10px; color: var(--nk-text-muted);
    margin: 0; white-space: pre-wrap;
  }
</style>
