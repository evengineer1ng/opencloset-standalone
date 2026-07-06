# assets/ — base media loops for the visual morph engine

A descriptor's `visual.base.path` points at a looping clip the morph engine paints **on top
of**. Drop your file here and the path in the `.oradio` resolves.

## basketball_loop.mp4

`spec/examples/basketball.oradio` declares:

```yaml
visual:
  base: { mode: media, theme: midnight, path: assets/basketball_loop.mp4 }
```

Drop a short, seamless basketball clip (a looping court / crowd / net shot) at
`assets/basketball_loop.mp4`. The play-by-play then drives morphs over it:

- a made basket → **ripples**
- a three / a run → **embers + bloom**
- a turnover or foul → **glitch**

If the file is absent, the player falls back to the builtin `theme` (`midnight`) — the
morphs still run; only the underlying loop is missing.
