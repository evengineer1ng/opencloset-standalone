# Benchmark — deterministic narration vs LLM-at-runtime

**Scoped claim:** for narrating *structured events*, a deterministic renderer is ~5 orders of
magnitude faster, free, and faithful by construction, while a strong local LLM — given its **best
case** — confabulates facts about a third of the time. The LLM's only win is fluency.

Reproduce: `python -m tools.benchmark --n 50` (needs Ollama + `llama3.1:8b`).

## Method (and why it's fair to the LLM)
- **Same inputs.** Both systems get the *identical* structured event rows (driver / action / target
  / lap) from `data/f1_barcelona_2026.json` and produce one sentence each. This isolates the
  *rendering* step.
- **LLM steelmanned.** Local `llama3.1:8b` at **temperature 0** — its best case for both determinism
  and faithfulness. A clean, fast 8B (no chain-of-thought overhead).
- **Faithfulness is conservative.** "Invented a fact" fires only on an unsupported *specific* — a
  turn number, a position, a team, or a driver not in the event. It misses subtler invention
  (made-up crowds, "P5"-style mentions), so the true rate is **higher** than reported.
- **Scope is generous to the LLM.** We hand it one already-extracted event. The full pipeline
  (detect → select → thread → inquiry) is extra deterministic work the LLM would also have to do —
  and is *not* credited here.

## Results (N=50, one race)

| metric | deterministic | LLM-at-runtime (llama3.1:8b @ temp 0) |
|---|---|---|
| latency / event | **~0.01 ms** | ~2,953 ms |
| runtime token cost | **0** | 3,810 |
| identical on re-run | 100% | 100% *(temp 0 only; see caveats)* |
| names the right driver | 100% | 100% |
| **invented a fact** | **0%** | **34% (17/50)** — conservative floor |

## What this shows
For **faithful narration of structured data**, the deterministic path wins decisively on cost,
latency, and factual reliability. A third of the steelmanned LLM's one-liners contained a fabricated
specific (e.g. *"bringing his Racing Point car"*, *"Pérez moves into second place"* on a row that
only said *"Lawson pits"*). The deterministic renderer cannot do this — it renders given fields.

## What this does NOT show (honest caveats)
1. **Local 8B, not frontier.** A GPT-class model would confabulate less and read better — at higher
   cost, still non-deterministic at realistic temperature, still not faithful-by-construction. This
   number is specific to `llama3.1:8b`.
2. **Determinism is a tie here.** At temp 0 the LLM was reproducible — but temp-0 narration is
   robotic, which defeats the reason to use an LLM. At realistic temperature it is non-deterministic;
   the deterministic system is reproducible *by construction* regardless.
3. **Fluency goes to the LLM.** Its sentences are richer and more natural (when not hallucinating).
   The deterministic output is terse and templated. See `transcripts/benchmark_samples.txt`.
4. **One domain, N=50.** The *direction* is robust; a paper-grade rate needs more events, models,
   and domains.

## Context-budget test — what happens as you load up the input

Reproduce: `python -m tools.context_benchmark` (set `OPENAI_API_KEY` + `--openai-model` for a
frontier column). A **canary** is planted at lap 0 (*"Bottas stalled at lights out"*); each system
is asked *"what happened at the start?"* while the event log **doubles**. The LLM (`llama3.1:8b`,
`num_ctx=8192`) truncates oldest-first when the log overflows — so it forgets the start.

| events | det (ms / recalls start) | local 8B (ms / ctx tokens / recalls start) |
|---:|---|---|
| 8 | 0.00 · **yes** | 6,841 · 125 · yes |
| 64 | 0.00 · **yes** | 8,414 · 703 · yes |
| 256 | 0.00 · **yes** | 32,646 · 2,673 · yes |
| 512 | 0.00 · **yes** | 80,196 · 5,313 · yes |
| **1024** | 0.00 · **yes** | **246,138 · 8,192 · NO** ← crossover |
| 2048 | 0.00 · **yes** | 248,140 · 8,192 · NO |
| 4096 | 0.00 · **yes** | 248,530 · 8,192 · NO |

**Findings:** (1) at ~1024 events the context saturates (`ctx tokens` pins at 8,192) and the model
**silently forgets lap 0** — it answers confidently from a truncated race, no error raised. (2)
Latency detonates *before* the break: 6.8 s → 80 s → **246 s** (≈4 min for one wrong answer).
(3) Deterministic recalls the start in **O(1)** at every scale, 0 ms, no context limit — past the
crossover it is the *only* system still correct.

**Caveats:** `num_ctx=8192` is the budget set for this run; a bigger window (llama 128k, frontier
256k+) moves the crossover *later* but does not remove it — and the latency/cost growth with input,
plus documented "lost-in-the-middle" degradation *within* the window, remain. The deterministic
0 ms is O(1) canary recall; a full running summary is O(n) but still trivial and unbounded.

**Frontier (ChatGPT 5.x):** not run — requires an API key; no fabricated numbers. *Expected*
shape: crossover much later (large window) but the same latency/cost growth and the same eventual
truncation + in-window recall decay. Will be added as a measured column when a key is supplied.

## The takeaway (the project's thesis, measured)
Don't put the LLM in the runtime to *narrate*. Use it at **compile time** to *author* the
deterministic renderer (the grammar, the rules). Then narration is free, instant, reproducible, and
factually faithful — and you spend the model only where it's irreplaceable.
