## Milestone 4 - Causal Visual Tape

### Goal

Make Q3 feel real without violating the repo's causality laws.

The visual system must follow the same split the engine already uses elsewhere:

- derivable coordinates come from `oradio_engine.index.Index`
- observed visual causes are preserved in an append-only tape
- rendered frames are projections of `seed + tape[0..t]`, not the source of truth

### Canon

- `docs/SIMULATION_ENGINE.md`: `world(t) = f(seed, tape[0..t])`
- `oradio_engine/index.py`: seed + address derive a point lazily
- `oradio_engine/observation.py`: reality's answer is stored, never re-derived

### Runtime shape

#### 1. Descriptor contract

Loom descriptors keep the existing `theme` field for compatibility, and add:

```yaml
visual:
  base:
    mode: builtin | media
    theme: ribbon
    path: C:/...
  tape:
    seed: my_loom:42
    accumulation: causal
    families: [color_drift, breath, particles, ripples]
  thumbnail:
    mode: sidecar_png
```

`theme` remains the compatibility seam the Club/runtime already understands.
`visual` is the richer Loom-native expression contract.

#### 2. Visual tape

New module: `oradio_engine/visual_tape.py`

- append-only `VisualTapeLog`
- `VisualTapeEvent` stores:
  - `tick`
  - `family`
  - `source`
  - `address`
  - `energy`
  - `hue_hint`
  - `lineage`
  - `payload`
- candidate -> tape entries is deterministic
- earlier entries continue to influence later snapshots

#### 3. Visual index

New module: `oradio_engine/visual_index.py`

- wraps `Index`
- derives overlay primitives from `seed + address`
- no frame cache is the source of truth
- addresses stay inspectable and lineage-friendly

#### 4. Renderer / thumbnail

New module: `oradio_engine/visual_thumbnail.py`

- resolves a base frame from:
  - builtin ribbon theme
  - image path
  - video poster/frame when OpenCV is available
- composites the causal tape over the base non-destructively
- writes `<descriptor>.thumbnail.png`

The same render path serves both:

- player stage image
- sidecar thumbnail generation

### Player changes

`loom_player_ui.py` becomes a tape-driven compositor:

- creates a `VisualTapeLog` from descriptor config
- records produced candidates into the tape
- renders the stage from `visual_thumbnail.render_visual_frame(...)`
- updates a visual-tape summary panel
- regenerates the sidecar thumbnail after initial load and after new beats

### Loom authoring changes

`loom/loom_studio.py` Q3 authors:

- builtin theme vs custom media path
- voice provider and assignments
- causal tape families

Export also generates the initial sidecar thumbnail so the artifact has visible proof before first open.

### First slice limits

- base media playback uses a still image / sampled video frame path in this milestone slice
- the tape is the main delivered runtime system
- full time-varying video decode can grow on top of the same base contract later

### Tests

Add `tests/test_visual_tape.py` for:

- deterministic snapshot derivation from same seed + same tape
- compounding behavior as tape grows
- sidecar thumbnail generation

Extend `tests/test_loom_studio.py` for:

- descriptor includes `visual.base`, `visual.tape`, `visual.thumbnail`
- custom media path authors `visual.base.mode = media`
