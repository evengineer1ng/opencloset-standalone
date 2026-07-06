/**
 * nkAudio.ts — Neikos ambient audio engine
 *
 * Tier-adaptive soundscapes using Web Audio API oscillators and noise.
 * No external audio files required — all synthetic.
 * At Tier V, the nature sounds reveal themselves as recordings.
 */

let _ctx: AudioContext | null = null
let _masterGain: GainNode | null = null
let _started = false

// Active sound nodes
const _active: Map<string, { nodes: AudioNode[], gain: GainNode }> = new Map()

// Relay pulse tracking
let _relayPulseTimer: ReturnType<typeof setInterval> | null = null
let _tierValue = 1

function ctx(): AudioContext {
  if (!_ctx) {
    _ctx = new AudioContext()
    _masterGain = _ctx.createGain()
    _masterGain.gain.value = 0.25
    _masterGain.connect(_ctx.destination)
  }
  return _ctx
}

function master(): GainNode {
  ctx()
  return _masterGain!
}

// ── Noise generator ────────────────────────────────────────

function createNoise(type: 'white' | 'pink' | 'brown' = 'brown'): AudioBufferSourceNode {
  const c = ctx()
  const bufSize = c.sampleRate * 2
  const buf = c.createBuffer(1, bufSize, c.sampleRate)
  const data = buf.getChannelData(0)

  if (type === 'white') {
    for (let i = 0; i < bufSize; i++) data[i] = Math.random() * 2 - 1
  } else if (type === 'pink') {
    let b0=0, b1=0, b2=0, b3=0, b4=0, b5=0, b6=0
    for (let i = 0; i < bufSize; i++) {
      const w = Math.random() * 2 - 1
      b0=0.99886*b0+w*0.0555179; b1=0.99332*b1+w*0.0750759
      b2=0.96900*b2+w*0.1538520; b3=0.86650*b3+w*0.3104856
      b4=0.55000*b4+w*0.5329522; b5=-0.7616*b5-w*0.0168980
      data[i] = (b0+b1+b2+b3+b4+b5+b6+w*0.5362)*0.11
      b6 = w * 0.115926
    }
  } else {  // brown
    let last = 0
    for (let i = 0; i < bufSize; i++) {
      const w = Math.random() * 2 - 1
      last = (last + 0.02 * w) / 1.02
      data[i] = last * 3.5
    }
  }

  const src = c.createBufferSource()
  src.buffer = buf
  src.loop = true
  return src
}

function createFilter(freq: number, type: BiquadFilterType = 'bandpass', Q = 1): BiquadFilterNode {
  const f = ctx().createBiquadFilter()
  f.type = type
  f.frequency.value = freq
  f.Q.value = Q
  return f
}

function createOsc(freq: number, type: OscillatorType = 'sine'): OscillatorNode {
  const c = ctx()
  const o = c.createOscillator()
  o.type = type
  o.frequency.value = freq
  return o
}

// ── Soundscape builders per node type ──────────────────────

type SoundLayer = { nodes: AudioNode[], gain: GainNode }

function buildWildZone(tier: number): SoundLayer {
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())

  const nodes: AudioNode[] = [gain]

  // Wind — brown noise through low-pass
  const wind = createNoise('brown')
  const windFilt = createFilter(300, 'lowpass', 0.5)
  const windGain = c.createGain()
  windGain.gain.value = 0.4
  wind.connect(windFilt)
  windFilt.connect(windGain)
  windGain.connect(gain)
  wind.start()
  nodes.push(wind, windFilt, windGain)

  // Birdsong simulation — pink noise bursts through high bandpass
  // At Tier V this reveals itself as the "synthetic" layer
  const birds = createNoise('pink')
  const birdFilt = createFilter(2400 + tier * 100, 'bandpass', 8)
  const birdGain = c.createGain()
  birdGain.gain.value = tier >= 5 ? 0.08 : 0.18  // quieter at tier V — revealing artificiality
  birds.connect(birdFilt)
  birdFilt.connect(birdGain)
  birdGain.connect(gain)
  birds.start()
  nodes.push(birds, birdFilt, birdGain)

  // Tier II+: subtle low undertone begins
  if (tier >= 2) {
    const undertone = createOsc(47, 'sine')
    const underGain = c.createGain()
    underGain.gain.value = 0.02 + (tier - 2) * 0.01
    undertone.connect(underGain)
    underGain.connect(gain)
    undertone.start()
    nodes.push(undertone, underGain)
  }

  return { nodes, gain }
}

function buildCity(tier: number): SoundLayer {
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())

  const nodes: AudioNode[] = [gain]

  // Crowd hum — mid-band noise
  const crowd = createNoise('pink')
  const crowdFilt = createFilter(400, 'bandpass', 0.8)
  const crowdGain = c.createGain()
  crowdGain.gain.value = 0.3
  crowd.connect(crowdFilt)
  crowdFilt.connect(crowdGain)
  crowdGain.connect(gain)
  crowd.start()
  nodes.push(crowd, crowdFilt, crowdGain)

  // Mechanical hum
  const hum = createOsc(120, 'sawtooth')
  const humFilt = createFilter(200, 'lowpass')
  const humGain = c.createGain()
  humGain.gain.value = 0.04 + (tier - 1) * 0.015
  hum.connect(humFilt)
  humFilt.connect(humGain)
  humGain.connect(gain)
  hum.start()
  nodes.push(hum, humFilt, humGain)

  return { nodes, gain }
}

function buildFacility(tier: number): SoundLayer {
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())

  const nodes: AudioNode[] = [gain]

  // Fluorescent buzz
  const buzz = createOsc(120, 'square')
  const buzzFilt = createFilter(300, 'lowpass')
  const buzzGain = c.createGain()
  buzzGain.gain.value = 0.03 + tier * 0.01
  buzz.connect(buzzFilt)
  buzzFilt.connect(buzzGain)
  buzzGain.connect(gain)
  buzz.start()
  nodes.push(buzz, buzzFilt, buzzGain)

  // HVAC white noise
  const hvac = createNoise('white')
  const hvacFilt = createFilter(800, 'bandpass', 2)
  const hvacGain = c.createGain()
  hvacGain.gain.value = 0.08
  hvac.connect(hvacFilt)
  hvacFilt.connect(hvacGain)
  hvacGain.connect(gain)
  hvac.start()
  nodes.push(hvac, hvacFilt, hvacGain)

  // Tier III+: telemetry-style beep (faint)
  if (tier >= 3) {
    const ping = createOsc(880, 'sine')
    const pingGain = c.createGain()
    pingGain.gain.value = 0
    ping.connect(pingGain)
    pingGain.connect(gain)
    ping.start()
    // Pulse every 8 seconds
    const pingInterval = setInterval(() => {
      const now = c.currentTime
      pingGain.gain.setValueAtTime(0.04, now)
      pingGain.gain.exponentialRampToValueAtTime(0.001, now + 0.3)
    }, 8000)
    nodes.push(ping, pingGain)
    ;(gain as any)._pingInterval = pingInterval
  }

  return { nodes, gain }
}

function buildDungeon(tier: number): SoundLayer {
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())

  const nodes: AudioNode[] = [gain]

  // Low drone
  const drone = createOsc(55 + tier * 5, 'sine')
  const droneGain = c.createGain()
  droneGain.gain.value = 0.12
  drone.connect(droneGain)
  droneGain.connect(gain)
  drone.start()
  nodes.push(drone, droneGain)

  // Echo effect via delay
  const delay = c.createDelay(2.0)
  delay.delayTime.value = 0.4
  const delayGain = c.createGain()
  delayGain.gain.value = 0.35
  const noise = createNoise('brown')
  const noiseFilt = createFilter(150, 'lowpass')
  const noiseGain = c.createGain()
  noiseGain.gain.value = 0.15
  noise.connect(noiseFilt)
  noiseFilt.connect(noiseGain)
  noiseGain.connect(gain)
  noiseGain.connect(delay)
  delay.connect(delayGain)
  delayGain.connect(gain)
  noise.start()
  nodes.push(noise, noiseFilt, noiseGain, delay, delayGain)

  return { nodes, gain }
}

function buildAnomalyZone(tier: number): SoundLayer {
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())

  const nodes: AudioNode[] = [gain]

  // Unstable harmonic cluster
  const freqs = [47, 94, 141, 188]
  freqs.forEach((f, i) => {
    const osc = createOsc(f + tier * 3, i % 2 === 0 ? 'sine' : 'triangle')
    const oscGain = c.createGain()
    oscGain.gain.value = 0.03 + tier * 0.005
    osc.connect(oscGain)
    oscGain.connect(gain)
    osc.start()
    // Slow LFO on frequency
    const lfo = createOsc(0.1 + i * 0.07, 'sine')
    const lfoGain = c.createGain()
    lfoGain.gain.value = 5 + tier * 2
    lfo.connect(lfoGain)
    lfoGain.connect(osc.frequency)
    lfo.start()
    nodes.push(osc, oscGain, lfo, lfoGain)
  })

  // Distorted noise
  const dist = createNoise('white')
  const distFilt = createFilter(200 + tier * 50, 'bandpass', 3)
  const distGain = c.createGain()
  distGain.gain.value = 0.05 + tier * 0.02
  dist.connect(distFilt)
  distFilt.connect(distGain)
  distGain.connect(gain)
  dist.start()
  nodes.push(dist, distFilt, distGain)

  return { nodes, gain }
}

function buildLandmark(tier: number): SoundLayer {
  // Calm, ancient — wind through stone
  const c = ctx()
  const gain = c.createGain()
  gain.gain.value = 0
  gain.connect(master())
  const nodes: AudioNode[] = [gain]

  const wind = createNoise('brown')
  const windFilt = createFilter(180, 'lowpass', 0.7)
  const windGain = c.createGain()
  windGain.gain.value = 0.25
  wind.connect(windFilt)
  windFilt.connect(windGain)
  windGain.connect(gain)
  wind.start()
  nodes.push(wind, windFilt, windGain)

  // Resonant tone
  const tone = createOsc(220, 'sine')
  const toneGain = c.createGain()
  toneGain.gain.value = 0.03
  tone.connect(toneGain)
  toneGain.connect(gain)
  tone.start()
  nodes.push(tone, toneGain)

  return { nodes, gain }
}

// ── Relay node pulse (Tier IV+) ────────────────────────────
// Fires every 47 seconds — matching the lore

function startRelayPulse() {
  if (_relayPulseTimer) return
  const c = ctx()
  _relayPulseTimer = setInterval(() => {
    if (_tierValue < 4) return
    const now = c.currentTime
    const osc = createOsc(47, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now)
    g.gain.linearRampToValueAtTime(0.06, now + 0.5)
    g.gain.exponentialRampToValueAtTime(0.001, now + 2.5)
    osc.connect(g)
    g.connect(master())
    osc.start(now)
    osc.stop(now + 2.5)
  }, 47000)
}

function stopRelayPulse() {
  if (_relayPulseTimer) { clearInterval(_relayPulseTimer); _relayPulseTimer = null }
}

// ── Public API ─────────────────────────────────────────────

function soundscapeFor(nodeType: string, tier: number): SoundLayer {
  switch (nodeType) {
    case 'WILD_ZONE':    return buildWildZone(tier)
    case 'CITY':         return buildCity(tier)
    case 'FACILITY':     return buildFacility(tier)
    case 'DUNGEON':      return buildDungeon(tier)
    case 'ANOMALY_ZONE': return buildAnomalyZone(tier)
    case 'LANDMARK':     return buildLandmark(tier)
    default:             return buildWildZone(tier)  // fallback
  }
}

let _currentNodeType = ''
let _crossfadeMs = 2000

export function ensureStarted() {
  if (_ctx && _ctx.state === 'suspended') _ctx.resume()
  _started = true
  startRelayPulse()
}

export function isStarted() { return _started }

export function setTier(tier: number) {
  _tierValue = tier
  // Tier V: fade master slightly (sounds more clinical/quiet)
  if (_masterGain) {
    _masterGain.gain.setTargetAtTime(
      tier >= 5 ? 0.15 : 0.25,
      ctx().currentTime,
      2.0
    )
  }
}

export function setNodeAmbient(nodeType: string, isRelayNode = false, tier = 1) {
  if (!_started) return
  const c = ctx()
  const effectiveType = isRelayNode ? 'ANOMALY_ZONE' : nodeType
  if (effectiveType === _currentNodeType) return
  _currentNodeType = effectiveType

  const now = c.currentTime
  const fadeDur = _crossfadeMs / 1000

  // Fade out existing
  _active.forEach((layer, key) => {
    layer.gain.gain.setTargetAtTime(0, now, fadeDur * 0.3)
    setTimeout(() => stopLayer(key), _crossfadeMs + 500)
  })

  // Build and fade in new
  const key = `${effectiveType}_${tier}`
  if (!_active.has(key)) {
    const layer = soundscapeFor(effectiveType, tier)
    _active.set(key, layer)
    layer.gain.gain.setValueAtTime(0, now)
    layer.gain.gain.setTargetAtTime(0.8, now + fadeDur * 0.5, fadeDur * 0.4)
  }
}

function stopLayer(key: string) {
  const layer = _active.get(key)
  if (!layer) return
  layer.nodes.forEach(n => {
    try { (n as any).stop?.() } catch {}
    try { n.disconnect() } catch {}
  })
  if ((layer.gain as any)._pingInterval) clearInterval((layer.gain as any)._pingInterval)
  _active.delete(key)
}

export function stopAll() {
  _active.forEach((_, key) => stopLayer(key))
  _currentNodeType = ''
  stopRelayPulse()
  _started = false
  if (_ctx) { _ctx.close(); _ctx = null; _masterGain = null }
}

export function playBattleStart() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Rising tritone — tense
  ;[220, 311, 392].forEach((freq, i) => {
    const o = createOsc(freq, 'triangle')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.08)
    g.gain.linearRampToValueAtTime(0.08, now + i * 0.08 + 0.1)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.08 + 0.8)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.08)
    o.stop(now + i * 0.08 + 0.8)
  })
}

export function playBattleWin() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Ascending major arpeggio
  ;[261, 329, 392, 523].forEach((freq, i) => {
    const o = createOsc(freq, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.12)
    g.gain.linearRampToValueAtTime(0.1, now + i * 0.12 + 0.08)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.12 + 0.6)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.12)
    o.stop(now + i * 0.12 + 0.6)
  })
}

export function playFragmentDiscover() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Soft chime
  const o = createOsc(880, 'sine')
  const g = c.createGain()
  g.gain.setValueAtTime(0.1, now)
  g.gain.exponentialRampToValueAtTime(0.001, now + 1.5)
  o.connect(g); g.connect(master())
  o.start(now); o.stop(now + 1.5)
}

export function playKnowerUnlock() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Low, slow descending tones
  ;[330, 247, 185].forEach((freq, i) => {
    const o = createOsc(freq, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.4)
    g.gain.linearRampToValueAtTime(0.06, now + i * 0.4 + 0.2)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.4 + 1.2)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.4); o.stop(now + i * 0.4 + 1.2)
  })
}

export function playFragmentAudio(fragmentType: string, _bodyText: string = '') {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime

  if (fragmentType === 'AUDIO_ARTIFACT') {
    const noise = createNoise('white')
    const noiseFilt = createFilter(400, 'lowpass', 2)
    const noiseGain = c.createGain()
    noiseGain.gain.setValueAtTime(0.15, now)
    noiseGain.gain.exponentialRampToValueAtTime(0.001, now + 0.8)
    noise.connect(noiseFilt); noiseFilt.connect(noiseGain); noiseGain.connect(master())
    noise.start(now); noise.stop(now + 0.8)
    const tone = createOsc(180, 'sawtooth')
    const toneFilt = createFilter(250, 'lowpass')
    const toneGain = c.createGain()
    toneGain.gain.setValueAtTime(0.04, now + 0.3)
    toneGain.gain.exponentialRampToValueAtTime(0.001, now + 1.5)
    tone.connect(toneFilt); toneFilt.connect(toneGain); toneGain.connect(master())
    tone.start(now + 0.3); tone.stop(now + 1.5)
  } else if (fragmentType === 'REDACTED_LOG') {
    const thud = createOsc(80, 'sine')
    const thudGain = c.createGain()
    thudGain.gain.setValueAtTime(0.12, now)
    thudGain.gain.exponentialRampToValueAtTime(0.001, now + 0.25)
    thud.connect(thudGain); thudGain.connect(master())
    thud.start(now); thud.stop(now + 0.25)
  } else if (fragmentType === 'RESEARCH_NOTE') {
    const page = createNoise('pink')
    const pageFilt = createFilter(3000, 'highpass')
    const pageGain = c.createGain()
    pageGain.gain.setValueAtTime(0.06, now)
    pageGain.gain.exponentialRampToValueAtTime(0.001, now + 0.15)
    page.connect(pageFilt); pageFilt.connect(pageGain); pageGain.connect(master())
    page.start(now); page.stop(now + 0.15)
  } else {
    playFragmentDiscover()
  }
}

// ── Encounter / Capture audio ──────────────────────────────

/** Soft "shimmer" sting when an encounter appears */
export function playEncounterStart() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Quick rising shimmer: 440 → 660 → 880
  ;[440, 660, 880].forEach((freq, i) => {
    const o = createOsc(freq, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.06)
    g.gain.linearRampToValueAtTime(0.07, now + i * 0.06 + 0.05)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.06 + 0.5)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.06)
    o.stop(now + i * 0.06 + 0.5)
  })
}

/** Success chime when a creature is captured */
export function playCapture() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Ascending major chord: C5 E5 G5 C6
  ;[523, 659, 784, 1047].forEach((freq, i) => {
    const o = createOsc(freq, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.07)
    g.gain.linearRampToValueAtTime(0.09, now + i * 0.07 + 0.05)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.07 + 1.2)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.07)
    o.stop(now + i * 0.07 + 1.2)
  })
}

/** Failure buzz when capture breaks */
export function playCaptureFail() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  // Descending minor groan
  ;[440, 370, 311].forEach((freq, i) => {
    const o = createOsc(freq, 'sawtooth')
    const g = c.createGain()
    g.gain.setValueAtTime(0.06, now + i * 0.1)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.1 + 0.4)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.1)
    o.stop(now + i * 0.1 + 0.4)
  })
}

/** Short blip when fleeing an encounter */
export function playEncounterFlee() {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  const o = createOsc(300, 'square')
  const g = c.createGain()
  g.gain.setValueAtTime(0.05, now)
  g.gain.exponentialRampToValueAtTime(0.001, now + 0.25)
  o.connect(g); g.connect(master())
  o.start(now); o.stop(now + 0.25)
}

export function playTierEscalation(newTier: number) {
  if (!_started) return
  const c = ctx()
  const now = c.currentTime
  const freqs = [newTier === 5 ? 30 : 40, 60, 80]
  freqs.forEach((f, i) => {
    const o = createOsc(f, 'sine')
    const g = c.createGain()
    g.gain.setValueAtTime(0, now + i * 0.3)
    g.gain.linearRampToValueAtTime(0.15 - i * 0.03, now + i * 0.3 + 0.4)
    g.gain.exponentialRampToValueAtTime(0.001, now + i * 0.3 + 2.5)
    o.connect(g); g.connect(master())
    o.start(now + i * 0.3); o.stop(now + i * 0.3 + 2.5)
  })
}
