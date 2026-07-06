# Booth Antennas Mapping

This maps the `Radio-OS` antenna/feed model onto the one-file booth workflow without making the booth depend on a backend at runtime.

## Core Idea

`Radio-OS` antennas already define feeds as deterministic config:

- `feeds.{key}.plugin`
- plugin config like `urls`, `subreddits`, `poll_sec`, `limit`
- optional `mix.weights`
- optional `scheduler.source_quotas`

The booth should not run those live fetchers inside `booth-presets.html`.

Instead:

1. OpenClaw fetches a live snapshot from the feed spec.
2. OpenClaw bakes that snapshot into:
   - booth role-row tape events
   - schema hints
   - an inline JS snippet for `TAPES[...]`
3. The one-file booth consumes only the baked artifact.

That keeps the booth:

- one file
- deterministic against a frozen snapshot
- backend-free at playback time

## Determinism

Live feeds are still compatible with determinism if we use the same boundary as the rest of Loom:

- live fetch is the intake
- baked artifact is the immutable snapshot
- booth output is a pure function of:
  - baked tape
  - selected preset
  - controls

So the rule is:

`same baked snapshot + same preset + same controls = same booth output`

This is a `LIVE`-class deterministic path, not a fake-static one.

## Field Mapping

Radio-OS feed manifest to booth artifact:

- `feeds.{key}.plugin` -> fetch adapter (`rss`, `reddit`)
- `feeds.{key}.urls` -> RSS source list
- `feeds.{key}.subreddits` -> Reddit source list
- `mix.weights` -> retained in artifact manifest for authoring context
- `scheduler.source_quotas` -> retained in artifact manifest for authoring context

Baked artifact outputs:

- `items[]` -> normalized snapshot rows
- `tape.events[]` -> booth role rows
- `schema_hints` -> aliases, fields, actors, query notes
- `inline_js` -> pasteable `TAPES[...] = [...]`
- `booth_lines[]` -> plain fallback lines if you want textarea mode

## OpenClaw Path

The new CLI path lives in `oc booth ...`.

Single spec bake:

```powershell
python opencloset/oc.py booth bake-feeds `
  --spec opencloset/loom-main/booth_feed_queue/sample_rss_reddit.json `
  --out opencloset/loom-main/booth_feed_queue/artifacts/live_feed_digest.artifact.json `
  --inline-js-out opencloset/loom-main/booth_feed_queue/artifacts/live_feed_digest.inline.js
```

Queue processing:

```powershell
python opencloset/oc.py booth bake-queue `
  --inbox opencloset/loom-main/booth_feed_queue `
  --out-dir opencloset/loom-main/booth_feed_queue/artifacts `
  --archive-dir opencloset/loom-main/booth_feed_queue/archive
```

Agent payload generation:

```powershell
python opencloset/oc.py booth agent-payload `
  --artifact opencloset/loom-main/booth_feed_queue/artifacts/live_feed_digest.artifact.json `
  --out opencloset/loom-main/booth_feed_queue/artifacts/live_feed_digest.agent_event.json
```

## Background Agent Layer

Keep the background agent upstream of the booth, not inside it.

Recommended division:

- deterministic baker:
  - fetches feeds
  - emits normalized tape
  - emits base schema hints
- OpenClaw agent channel:
  - reviews baked artifacts
  - proposes:
    - richer aliases
    - domain-specific query phrases
    - stronger event-role mappings
    - candidate high-end tapes worth inlining
  - never becomes a booth runtime dependency

Example runtime setup:

```powershell
python opencloset/run_api.py
python opencloset/oc.py agent start boothbaker --mode ambient --domain general --objective "Review baked booth feed artifacts and propose schema improvements"
python opencloset/oc.py agent schedule boothbaker --cooldown-seconds 900
python opencloset/oc.py agent event boothbaker --payload-file opencloset/loom-main/booth_feed_queue/artifacts/live_feed_digest.agent_event.json --type booth.feed_baked --text "New booth feed artifact ready for schema review"
```

## What This Gives Us

- RSS and Reddit can become dynamic booth tapes right now.
- The booth stays one-file.
- The agent can work a queue of baked artifacts and help populate schemas over time.
- If the agent fails or is offline, the baked deterministic tape still works.
