# loom

> **loom** *(verb)* — to pull a thread and trace it.

The center of this project. Read it before anything else here, because everything
else orbits it. (This is the dictionary. The thesaurus comes after — and it is *not*
the same thing.)

A coinage: a denominal verb from *loom*, the weaving machine. Standard-English "loom"
the verb means to emerge or impend — "loom large," "a storm loomed." This is a
different sense, derived from the **noun**: the instrument that pulls threads and
reveals a larger pattern.

---

## The definition

**to loom** — to pull a thread and trace it.

- **thread** *(n.)* — a perceived continuity within or between things. Not necessarily
  causal, not necessarily logical; just something that appears to continue. A melody
  through a song. A theme through a series. An idea through a life. A bug through a
  codebase. A pattern through markets.
- **pull** *(v.)* — to follow the thread beyond its immediately visible form. You
  notice it; instead of stopping there, you keep going.
- **trace** *(v.)* — to map where it leads, where it came from, and what it touches.
  Following — not necessarily predicting, not necessarily explaining.

Two invariants hold across every use:

- **It is active.** Dreaming, remembering, observing can be passive. Looming is not —
  you engage, turn, enter, return, follow.
- **It is object-agnostic.** You can loom a song, a movie, a memory, a dream, a
  project, a person, a trading strategy, an `.oradio`, or looming itself. Most verbs
  do not survive that test. *predict* doesn't. *remember* doesn't. *imagine* doesn't.

---

## Neighbors, not synonyms

*simulate · imagine · dream · reflect · model · study · remember · navigate · create*

These are **ways one looms** — vehicles, not the destination. You can loom *through*
simulation, *through* memory, *through* imagination. None of them is the definition;
defining loom by listing them dissolves its center. A thesaurus tells you who lives
nearby. A dictionary tells you who you are.

---

## Grammar (note: not "The Loom")

- **loom** *(verb)* — to pull the thread and trace it. *"I loomed it last night."*
- **Loom** *(proper noun)* — the software that industrializes the verb, the way a
  loom-the-machine industrializes weaving threads into cloth. *"I put it into Loom."*
- **a loom** *(noun)* — what a trace leaves behind. *"Send me your loom of that movie."*

To **loom an `.oradio`** is not a separate definition — it is the verb run through the
instrument on a substrate that can become anything: pull the thread (declare the seed)
→ trace it (run the tape, follow where it leads). It reduces to the center.

---

## It has teeth

The definition bottoms out in operations, not vibes:

- **thread** = a continuity in the bus/tape (candidates sharing source/tag/entity
  across ticks; the engine already emits `thread_opened` / `thread_resolved`).
- **pull** = follow it beyond the current tick — advance the tape along it, or derive
  points not yet materialized (the Index).
- **trace** = walk its lineage: backward (origin), forward (where it leads), sideways
  (what it touches, via bindings) — the provenance audit; evidence over identity,
  performed.

The dipole / metronome instrument is a *special case* of looming: the up/down polarity
is a thread, the meter **pulls** it, the zero-crossing log **traces** it. The
definition absorbs its own instruments — which is how you know it is the center and
not a neighbor.
