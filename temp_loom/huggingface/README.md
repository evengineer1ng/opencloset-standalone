# Putting loom on HuggingFace

Two artifacts, one question. The **Space** lets them *hear* it; the **benchmark** makes them
*test* it.

## 1. The Static Space (zero backend — it just runs)
HuggingFace Spaces has a `static` SDK: your HTML *is* the app. No model, no server — which is the
whole point, and proof in itself.

1. `huggingface.co/new-space` → **SDK: Static**.
2. Put these at the Space repo root (copy from this repo's `docs/`):
   - `index.html`  (the artifact — the landing page)
   - `booth.html`  (the faders + lexical pitch)
   - `ncm.html`    (the Night City portal, REPLAY mode)
3. Give the Space's `README.md` this frontmatter:

```yaml
---
title: loom — a tape, sung
emoji: 🧵
colorFrom: indigo
colorTo: yellow
sdk: static
pinned: false
---
```

That's it — it serves `index.html`, runs in their browser, ~10KB.
(`ncm.html` runs in its read-only REPLAY mode on the Space; LIVE control needs the local relay.)

## 2. The benchmark (the thing that earns engagement)
Point them at [`bench/`](../bench) — a deterministic encoder that mints unlimited labeled
`(text → audio)` pairs, a scorer, and a non-ML baseline (~43% word accuracy) to beat. The task:
**decode the audio back to the text.** Near 100% proves a **lossless, model-free code**; whether
that code is a *language* is the open question you're posing, not a claim you're planting.

## How to post (in order of leverage)
- **HF Post** (the feed on hf.co) + a **Forum** thread (discuss.huggingface.co → *Research* or
  *Show and tell*). Draft:

> Built a deterministic codec that turns any event stream — a race, a heartbeat, a sentence — into
> sung language. No model, no server, ~10KB. Each word maps to a fixed musical motif, so it's
> **reversible**.
> It's reversible. I wrote a benchmark that emits unlimited (text→audio) pairs, and a dumb pitch-only
> baseline already recovers ~43% of words from sound alone — throwing the vowels away.
> So: how high can you push it? Near 100% means the meaning survives all the way into sound and
> back — a lossless code, no weights. And then the question I actually care about: **where's the line
> between a code you can decode and a language?** I don't know. I'd rather find out than guess.
> Beat 43%: [benchmark link]. Or just listen and tell me what you hear: [Space link].

**Framing note:** don't ask "is it AI?" — ask the decode challenge. It turns *"no model"* from a
shrug into a puzzle, and the absence of weights becomes the interesting part. Stay humble: it's
*a* proof and a concrete task, not a manifesto.
