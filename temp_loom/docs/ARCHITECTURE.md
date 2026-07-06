# Oracle Radio — the end shape

Everything is a **tape**. A tape is a list of events. The whole system turns tapes into
language you can read or hear — deterministically, with no model at runtime.

```
              ┌─────────── the pipeline (deterministic, no ML) ───────────┐
  source ──►  events ──► detect ──► threads ──► inquiry ──► mix ──► speech ──► booth / .txt
 (a tape)     (rows)    (find       (pull a    (ask         (ride    (render
                         events)     thread)    questions)   faders)  in a voice)
```

## Five small declarations (+ one instrument)

| Thing | What it declares | Where |
|---|---|---|
| **`.loom`** | the wish — 2 fields (`universe` + `connections`) → one `.oradio` | `loom/dotloom.py`, `spec/examples/*.loom` |
| **`.oradio`** | how the world runs — worlds / sources / wiring | `oradio_engine/descriptor.py`, `spec/` |
| **grammar** | *how it speaks* — a voice (intern / town crier / PM), domain-agnostic | `data/grammars/*.json`, `oradio_engine/speech.py` |
| **inquiry** | *what it wonders* — expectation templates that birth questions | `data/inquiry/*.json`, `oradio_engine/inquiry.py` |
| **mix** | *how it's performed* — the faders (depth/flavour/curiosity/salience/continuity/voice) | `oradio_engine/mix.py` |
| **the booth** | the live DJ rig — ride the faders, hear the tape, keep what you want | `loom_booth.py` |

All tiny, all data, all compiled into deterministic behavior. The intelligence lives in
*authoring the declarations* (an LLM's job, at compile time) — never in the runtime.

## The narration stack (the distilled core, ~800 LOC)

- **`detect.py`** — continuous telemetry → discrete events (threshold/extremum/overtake/milestone).
- **`speech.py`** — `Grammar`: role rows → sentences. Domain words come from the tape + a shared
  English verb table; the grammar carries only *style*. Deterministic lexical choice, number-to-words,
  pronoun cohesion, carried-state continuity ("for the second time").
- **`thread.py`** — the loom: pull a salient seed N causal hops (`depth`), in a `flavour` direction
  (cause ← / consequence →). Typed causal edges (domain `reason` token) phrase through the grammar.
- **`inquiry.py`** — expectations → questions; `investigate()` pulls the thread to (try to) answer.
  Curiosity is the dial. The tape debugs its own detector this way.
- **`mix.py`** — the `Mixer` faders + `LiveNarrator`: streams one thread, riding the faders live.
- **`antenna.py`** — load many tapes, toggle each; the mix is **one thread pulled from all tapes**,
  cross-linked by the entity each event names (a race thread weaves in the news about that driver).

## Feeding it: bake → tape → replay
Heavy/raw ingestion is a one-time **bake** that writes a thin-wire JSON tape; the runtime replays it
with no heavy deps. One baker per feed type — they all feed the same antenna.
- `tools/bake_f1.py` (FastF1 lap data), `tools/bake_rss.py` (any RSS feed), `oradio_engine/shims/`
  (basketball PBP, simulated arrays, …). New feed type = new baker.

## The three invariants
1. **Decoder purity** — `import oradio_engine` is stdlib + PyYAML only (guarded by
   `tests/test_engine_purity.py`). Heavy deps (FastF1, numpy, audio, tkinter) live in endpoints/bakers.
   A heavy dep in the core forks the file format.
2. **Domain vs. grammar** — facts/salience live in the tape (domain); voice/phrasing live in the
   grammar (domain-agnostic). The same grammar speaks F1, heart rate, or markets.
3. **Everything is a tape** — computed (seed), recorded (live), or imported (a finished game). After
   intake it's an immutable, replayable tape. "Live vs. not" is only *where the tape came from*; a
   *mix* is a tape too (record the fader automation; keep the takes you want).

## Determinism
A run is `world(t) = f(seed, tape[0..t])`. Deterministic worlds replay byte-identical; live sources
record to an immutable intake tape so replay is byte-identical too. The whole narration pipeline is
deterministic — a transcript of a race renders in ~25 ms with no GPU.
