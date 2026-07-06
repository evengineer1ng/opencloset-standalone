# Make a tape

A **tape** is just a list of events — things that happened, in order. loom narrates (and sings) any
tape, deterministically, with no model. Two ways in.

## 1. The easy way — paste lines (works on the live page, zero setup)
On the [booth](https://evengineer1ng.github.io/loom/), paste anything into **connect your own tape**.
**Each line becomes a sung beat.** Your chess moves, your steps, your commits, your day:
```
e4 e5 Nf3 Nc6 Bb5 a6
woke at 7, 8000 steps, skipped lunch, ran 5k
deployed v2, tests green, prod on fire, rolled back
```
If it's lines, it sings. No format required.

## 2. The structured way — role rows (for *smart* narration: threads, voices, questions)
To get grammar, causal threads, and inquiry, each event is a row whose **roles** ride on `tags` as
`key:value`:
```json
{"tags": ["actor:Hamilton", "action:overtake", "object:Norris", "valence:hype", "lap:32"], "priority": 0.7}
```
- **actor** — who (a name)
- **action** — a plain present-tense verb (`overtake`, `pit`, `spike`); the engine conjugates it
- **object** — what (`Norris`, `a three`, `the lead`)
- **magnitude** + **unit** — a number (`118` `bpm`)
- **valence** — `hype` / `alarm` / `calm` (colours the phrasing)
- **lap** — when (any clock)

A tape is a JSON list of these rows. A *voice* (grammar) speaks them; the *thread-puller* links
cause→effect; the *inquiry* layer asks questions where reality surprises it. All deterministic.

## What kinds of tapes might people make?
Anything that's a stream of events:
- **sports** — play-by-play (F1, NBA, your rec league)
- **you** — heart rate, steps, sleep, a workout, a whole day
- **markets** — price moves, a portfolio's session
- **a game** — a chess match, a KSP launch, a speedrun split log
- **code** — a commit log, a deploy, an incident timeline
- **a story** — a beat sheet, a recipe, a road trip

## Baking a tape from raw data
A **baker** maps raw data → role rows. See [`tools/bake_f1.py`](../tools/bake_f1.py) (FastF1 lap data)
and [`tools/bake_rss.py`](../tools/bake_rss.py) (any RSS feed) — one small script per source.
Everything downstream (voices, threads, singing) is reused; you only write the mapping. The `.oradio`
/ `.loom` format is frozen and tiny — see [`spec/`](../spec).

Make one. Send it to loom. Hear what it means.
