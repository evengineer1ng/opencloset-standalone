<script lang="ts">
  import { nkMap, nkMapStart, nkPlayerLoc, nkTierValue, nkBusy, nkActions } from '../../lib/nkStore'
  import type { NKNode } from '../../lib/nkStore'

  const WIDTH = 600
  const HEIGHT = 320
  const NODE_R = 10
  const NODE_R_CURRENT = 16

  // BFS layout
  function computeLayout(nodes: Record<string, NKNode>, startId: string): Map<string, {x:number,y:number}> {
    const pos = new Map<string, {x:number,y:number}>()
    if (!startId || !nodes[startId]) return pos

    const visited = new Set<string>()
    const rings: string[][] = [[startId]]
    visited.add(startId)

    while (true) {
      const last = rings[rings.length - 1]
      const next: string[] = []
      for (const nid of last) {
        const nd = nodes[nid]
        if (!nd) continue
        for (const nb of nd.neighbors) {
          if (!visited.has(nb) && nodes[nb]) {
            visited.add(nb)
            next.push(nb)
          }
        }
      }
      if (!next.length) break
      rings.push(next)
    }

    const cx = WIDTH / 2
    const cy = HEIGHT / 2
    const ringGap = 60

    rings.forEach((ring, ri) => {
      if (ri === 0) {
        pos.set(ring[0], {x: cx, y: cy})
        return
      }
      const r = ri * ringGap
      ring.forEach((nid, i) => {
        const angle = (2 * Math.PI * i / ring.length) - Math.PI / 2
        // Deterministic jitter from node_id hash
        const hash = hashStr(nid)
        const jx = ((hash & 0xff) / 255 - 0.5) * 20
        const jy = (((hash >> 8) & 0xff) / 255 - 0.5) * 20
        const x = Math.max(NODE_R_CURRENT + 2, Math.min(WIDTH - NODE_R_CURRENT - 2,
          cx + Math.cos(angle) * r + jx))
        const y = Math.max(NODE_R_CURRENT + 2, Math.min(HEIGHT - NODE_R_CURRENT - 2,
          cy + Math.sin(angle) * r + jy))
        pos.set(nid, {x, y})
      })
    })

    // Include any unvisited nodes (disconnected) in a bottom row
    let extraX = 20
    for (const nid of Object.keys(nodes)) {
      if (!pos.has(nid)) {
        pos.set(nid, {x: extraX, y: HEIGHT - 20})
        extraX += 30
      }
    }

    return pos
  }

  function hashStr(s: string): number {
    let h = 0
    for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0
    return Math.abs(h)
  }

  // Color per node type
  function nodeColor(type: string): string {
    const map: Record<string, string> = {
      WILD_ZONE:    'var(--nk-node-wild, #4a7c59)',
      CITY:         'var(--nk-node-city, #5c7fa0)',
      FACILITY:     'var(--nk-node-fac, #7a6b9e)',
      DUNGEON:      'var(--nk-node-dung, #3d3d3d)',
      ANOMALY_ZONE: 'var(--nk-node-anom, #a06c3d)',
      LANDMARK:     'var(--nk-node-land, #7a9e5c)',
    }
    return map[type] ?? 'var(--nk-text-muted)'
  }

  // Pan state
  let panX = 0
  let panY = 0
  let panning = false
  let lastPanX = 0
  let lastPanY = 0

  function onPointerDown(e: PointerEvent) {
    panning = true; lastPanX = e.clientX; lastPanY = e.clientY
    ;(e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId)
  }
  function onPointerMove(e: PointerEvent) {
    if (!panning) return
    panX += e.clientX - lastPanX; panY += e.clientY - lastPanY
    lastPanX = e.clientX; lastPanY = e.clientY
  }
  function onPointerUp() { panning = false }

  $: layout = computeLayout($nkMap, $nkMapStart)
  $: currentNeighbors = new Set<string>($nkMap[$nkPlayerLoc]?.neighbors ?? [])

  function nodeLabel(node: NKNode, tier: number): string {
    if (tier >= 5) return `SITE ${node.node_id}`
    if (tier >= 3) return node.node_id
    return node.name.length > 10 ? node.name.slice(0, 10) + '…' : node.name
  }

  function canClick(nid: string): boolean {
    return currentNeighbors.has(nid) && !$nkBusy
  }

  function handleNodeClick(nid: string) {
    if (canClick(nid)) nkActions.move(nid)
  }
</script>

<div class="nk-nodemap-wrap">
  <svg
    class="nk-nodemap-svg"
    viewBox="{-panX} {-panY} {WIDTH} {HEIGHT}"
    width="100%"
    height="320"
    on:pointerdown={onPointerDown}
    on:pointermove={onPointerMove}
    on:pointerup={onPointerUp}
    on:pointercancel={onPointerUp}
    style="touch-action: none; cursor: {panning ? 'grabbing' : 'grab'}"
  >
    <!-- Edges -->
    {#each Object.entries($nkMap) as [nid, node]}
      {#each node.neighbors as nb}
        {#if layout.has(nid) && layout.has(nb) && nid < nb}
          <line
            x1={layout.get(nid)!.x}
            y1={layout.get(nid)!.y}
            x2={layout.get(nb)!.x}
            y2={layout.get(nb)!.y}
            stroke="var(--nk-border, #444)"
            stroke-width="1"
            opacity="0.5"
          />
        {/if}
      {/each}
    {/each}

    <!-- Nodes -->
    {#each Object.entries($nkMap) as [nid, node]}
      {#if layout.has(nid)}
        {@const p = layout.get(nid)!}
        {@const isCurrent = nid === $nkPlayerLoc}
        {@const isNeighbor = currentNeighbors.has(nid)}
        {@const r = isCurrent ? NODE_R_CURRENT : NODE_R}
        {@const opacity = (isCurrent || isNeighbor) ? 1 : 0.4}
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <g
          opacity={opacity}
          class:relay={node.is_relay_node}
          on:click={() => handleNodeClick(nid)}
          style="cursor: {canClick(nid) ? 'pointer' : 'default'}"
        >
          {#if node.is_relay_node}
            <circle
              cx={p.x} cy={p.y} r={r + 4}
              fill="none"
              stroke="var(--nk-node-anom, #f0a)"
              stroke-width="1.5"
              class="relay-pulse"
            />
          {/if}
          <circle
            cx={p.x}
            cy={p.y}
            r={r}
            fill={nodeColor(node.node_type)}
            stroke={isCurrent ? 'var(--nk-accent, #0cf)' : 'var(--nk-border, #444)'}
            stroke-width={isCurrent ? 2.5 : 1}
          />
          {#if $nkTierValue >= 3}
            <text
              x={p.x}
              y={p.y + r + 10}
              text-anchor="middle"
              font-size="8"
              fill="var(--nk-text-muted, #888)"
            >{nodeLabel(node, $nkTierValue)}</text>
          {:else if isCurrent}
            <text
              x={p.x}
              y={p.y + r + 10}
              text-anchor="middle"
              font-size="8"
              fill="var(--nk-accent, #0cf)"
            >{nodeLabel(node, $nkTierValue)}</text>
          {/if}
        </g>
      {/if}
    {/each}
  </svg>
</div>

<style>
  .nk-nodemap-wrap {
    width: 100%;
    overflow: hidden;
    border-radius: var(--radius, 6px);
    border: 1px solid var(--nk-border, #333);
    background: var(--nk-bg, #111);
  }
  .nk-nodemap-svg { display: block; }

  @keyframes relay-pulse {
    0%, 100% { opacity: 0.4; r: 14; }
    50%       { opacity: 0.9; r: 18; }
  }
  .relay-pulse {
    animation: relay-pulse 2s ease-in-out infinite;
  }
</style>
