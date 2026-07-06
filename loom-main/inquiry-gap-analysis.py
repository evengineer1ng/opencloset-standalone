#!/usr/bin/env python3
"""
Inquiry Gap Analysis for mixday1pt2Z.tape
==========================================
Answers each candidate question deterministically, then catalogs:
  - What math operations ARE available in the tape schema
  - What math operations are MISSING (need implementation)
  - What structural assumptions each question makes

Output: a structured report the coder can use to implement missing inquiry primitives.
"""

import json
import re
from datetime import datetime
from collections import Counter

# --- Load tape ---
with open(r"C:\Users\evana\.openclaw\media\inbound\mixday1pt2Z.tape---78002826-c2e5-435e-a7df-c09fe0d5fe6e.json") as f:
    tape = json.load(f)

N = len(tape)
print(f"Tape size: {N} beats (laps 1..{tape[-1]['lap']})")
print()

# ============================================================
# Q1: How many unique voices appear?
# ============================================================
print("=" * 60)
print("Q1: Unique voices")
print("=" * 60)
voices = [b["voice"] for b in tape]
unique_voices = set(voices)
voice_counts = Counter(voices)
print(f"  Unique voices: {len(unique_voices)}")
print(f"  List: {sorted(unique_voices)}")
print(f"  Frequencies: {dict(voice_counts.most_common())}")

# Math available: set(collection(field)), Counter(collection(field))
# Math needed: none missing — pure set/counter ops on structured field
print("  Math status: FULLY SUPPORTED (set, counter on field)")
print()

# ============================================================
# Q2: Which voice is used most often?
# ============================================================
print("=" * 60)
print("Q2: Most frequent voice")
print("=" * 60)
top_voice, top_count = voice_counts.most_common(1)[0]
print(f"  '{top_voice}' appears {top_count}/{N} times ({100*top_count/N:.1f}%)")

# Math available: argmax(counter(field))
print("  Math status: FULLY SUPPORTED (argmax on counter)")
print()

# ============================================================
# Q3: Unique source feeds (publisher domains)
# ============================================================
print("=" * 60)
print("Q3: Unique source feeds")
print("=" * 60)
# Extract domain patterns from object text
# Pattern: "<domain> published" or "<domain>" at start of narration
domain_pattern = re.compile(r'([A-Za-z][\w.-]*\.\w{2,})\s+published')
domains = []
for b in tape:
    m = domain_pattern.search(b["object"])
    if m:
        domains.append(m.group(1).lower())
    else:
        # Booth meta-beats (lap 30+) reference "booth played" — no domain
        pass
unique_domains = set(domains)
domain_counts = Counter(domains)
print(f"  Unique domains: {len(unique_domains)}")
print(f"  List: {sorted(unique_domains)}")
print(f"  Frequencies: {dict(domain_counts.most_common())}")

# Math available: regex extraction on text field, then set/counter
# Math missing: no structured "source" field — must regex-parse free text
print("  Math status: PARTIALLY SUPPORTED")
print("  GAP: No 'source' field in schema. Requires regex on free-text 'object'.")
print("  FIX: Add 'source' field to tape rows, OR add a deterministic extraction primitive.")
print()

# ============================================================
# Q4: Tape span (time range)
# ============================================================
print("=" * 60)
print("Q4: Time span")
print("=" * 60)
times = [datetime.fromisoformat(b["time"].replace("Z", "+00:00")) for b in tape]
t_start = min(times)
t_end = max(times)
span = t_end - t_start
print(f"  Start: {t_start.isoformat()}")
print(f"  End:   {t_end.isoformat()}")
print(f"  Span:  {span} ({span.total_seconds()/60:.1f} min)")

# Math available: min/max on datetime field, subtraction
print("  Math status: FULLY SUPPORTED (min, max, delta on 'time')")
print()

# ============================================================
# Q5: Longest inter-beat gap
# ============================================================
print("=" * 60)
print("Q5: Longest inter-beat gap")
print("=" * 60)
gaps = []
for i in range(1, len(tape)):
    dt = (times[i] - times[i-1]).total_seconds()
    gaps.append((i, dt))
max_gap_lap, max_gap_sec = max(gaps, key=lambda x: x[1])
print(f"  Max gap: {max_gap_sec:.1f}s between lap {max_gap_lap-1} and lap {max_gap_lap}")
print(f"  Lap {max_gap_lap-1}: {tape[max_gap_lap-2]['time']}")
print(f"  Lap {max_gap_lap}:   {tape[max_gap_lap-1]['time']}")

# Math available: pairwise delta on sequential field, argmax
print("  Math status: FULLY SUPPORTED (pairwise delta, argmax)")
print()

# ============================================================
# Q6: What changed at lap 30?
# ============================================================
print("=" * 60)
print("Q6: Configuration change at lap 30")
print("=" * 60)
b29 = tape[28]  # lap 29
b30 = tape[29]  # lap 30
changed = {}
for k in b30:
    if k in b29 and b29[k] != b30[k]:
        changed[k] = (b29[k], b30[k])
print(f"  Fields that changed from lap 29 to lap 30:")
for k, (old, new) in changed.items():
    print(f"    {k}: {repr(old)[:60]} -> {repr(new)[:60]}")

# Math available: field-wise equality comparison between adjacent rows
print("  Math status: FULLY SUPPORTED (field-wise diff between adjacent rows)")
print()

# ============================================================
# Q7: Pause→play alternation
# ============================================================
print("=" * 60)
print("Q7: Pause→play alternation consistency")
print("=" * 60)
actions = [b["action"] for b in tape]
expected = []
for i in range(N):
    if i == 0:
        expected.append(actions[0])  # first can be anything
    else:
        expected.append("play" if actions[i-1] == "pause" else "pause")
breaks = [(i, actions[i], expected[i]) for i in range(N) if actions[i] != expected[i]]
print(f"  Alternation breaks: {len(breaks)}")
if breaks:
    for idx, actual, exp in breaks:
        print(f"    Lap {tape[idx]['lap']}: got '{actual}', expected '{exp}'")
else:
    print("  Perfect alternation (after lap 1).")

# Math available: sequential pattern check on field
print("  Math status: FULLY SUPPORTED (sequential pattern matching)")
print()

# ============================================================
# Q8: Voice changes between paired pause→play
# ============================================================
print("=" * 60)
print("Q8: Voice changes between paired pause→play")
print("=" * 60)
voice_changes = 0
voice_stays = 0
for i in range(0, N - 1, 2):
    if tape[i]["action"] == "pause" and tape[i+1]["action"] == "play":
        if tape[i]["voice"] != tape[i+1]["voice"]:
            voice_changes += 1
            if voice_changes <= 5:
                print(f"    Lap {tape[i]['lap']}→{tape[i+1]['lap']}: "
                      f"'{tape[i]['voice']}' -> '{tape[i+1]['voice']}'")
        else:
            voice_stays += 1
pairs = (N - 1) // 2
print(f"  Paired pause→play: {pairs}")
print(f"  Voice changed: {voice_changes} ({100*voice_changes/max(pairs,1):.1f}%)")
print(f"  Voice stayed:  {voice_stays} ({100*voice_stays/max(pairs,1):.1f}%)")

# Math available: paired comparison on field
print("  Math status: FULLY SUPPORTED (paired field comparison)")
print()

# ============================================================
# Q9: Where does style switch from opera→speak?
# ============================================================
print("=" * 60)
print("Q9: Style transitions")
print("=" * 60)
styles = [b["style"] for b in tape]
transitions = []
for i in range(1, N):
    if styles[i] != styles[i-1]:
        transitions.append((i, styles[i-1], styles[i]))
        print(f"    Lap {tape[i-1]['lap']}→{tape[i]['lap']}: '{styles[i-1]}' -> '{styles[i]}'")

# Math available: sequential field transition detection
print("  Math status: FULLY SUPPORTED (field transition detection)")
print()

# ============================================================
# Q10: Alarm valence beats
# ============================================================
print("=" * 60)
print("Q10: Valence distribution")
print("=" * 60)
valence_counts = Counter(b["valence"] for b in tape)
print(f"  Distribution: {dict(valence_counts)}")
alarm_beats = [(b["lap"], b["object"][:80]) for b in tape if b["valence"] == "alarm"]
print(f"  Alarm beats: {len(alarm_beats)}")
for lap, obj in alarm_beats:
    print(f"    Lap {lap}: {obj}")

# Math available: filter(field == value), counter
print("  Math status: FULLY SUPPORTED (filter + counter on enum field)")
print()

# ============================================================
# Q11: Which source tape feeds most beats?
# ============================================================
print("=" * 60)
print("Q11: Source tape frequency")
print("=" * 60)
all_tapes = []
for b in tape:
    if "tapes" in b:
        all_tapes.extend(b["tapes"])
tape_counts = Counter(all_tapes)
print(f"  Unique source tapes: {len(tape_counts)}")
for t, c in tape_counts.most_common():
    print(f"    {t}: {c} beats")

# Math available: flatten array field, counter
print("  Math status: FULLY SUPPORTED (flatten array field, counter)")
print()

# ============================================================
# Q12: Real-world news events (frequency in narrated text)
# ============================================================
print("=" * 60)
print("Q12: News event frequency (from 'object' text)")
print("=" * 60)
# Extract the headline portion: between "published" and " - " or end
event_pattern = re.compile(r'published\s+(.+?)(?:\s+-\s+[A-Za-z]|$)')
events = []
for b in tape:
    m = event_pattern.search(b["object"])
    if m:
        events.append(m.group(1).strip())
event_counts = Counter(events)
print(f"  Unique events: {len(event_counts)}")
for ev, c in event_counts.most_common():
    print(f"    [{c}x] {ev[:80]}")

# Math available: regex extraction on text field, counter
# Math missing: no structured "event_id" or "headline" field
print("  Math status: PARTIALLY SUPPORTED")
print("  GAP: Headlines are embedded in free-text 'object'. Regex extraction works")
print("       but is fragile. No event identity or dedup primitive.")
print("  FIX: Add 'headline' field OR define a deterministic extraction grammar.")
print()

# ============================================================
# Q13: Unique source tapes across all beats
# ============================================================
print("=" * 60)
print("Q13: Unique source tapes (already answered in Q11)")
print("=" * 60)
print(f"  {len(tape_counts)} unique source tapes")
print("  Math status: FULLY SUPPORTED (see Q11)")
print()

# ============================================================
# Q14: Lap 35+ tape configuration change
# ============================================================
print("=" * 60)
print("Q14: Tape array expansion at lap 35")
print("=" * 60)
lap34_tapes = tape[33]["tapes"]  # lap 34
lap35_tapes = tape[34]["tapes"]  # lap 35
print(f"  Lap 34 tapes: {len(lap34_tapes)} -> {lap34_tapes}")
print(f"  Lap 35 tapes: {len(lap35_tapes)} -> {lap35_tapes}")
added = set(lap35_tapes) - set(lap34_tapes)
print(f"  Added: {added}")

# Math available: set-diff on array field between rows
print("  Math status: FULLY SUPPORTED (set-diff on array field)")
print()

# ============================================================
# SUMMARY: INQUIRY MATH GAP REPORT
# ============================================================
print("=" * 60)
print("INQUIRY MATH GAP REPORT")
print("=" * 60)
print()

report = {
    "fully_supported": [
        "Q1: Unique value count on field (set, len)",
        "Q2: argmax on counter(field)",
        "Q4: min/max/delta on datetime field",
        "Q5: pairwise delta on sequential field, argmax",
        "Q6: field-wise diff between adjacent rows",
        "Q7: sequential pattern matching on field",
        "Q8: paired field comparison",
        "Q9: field transition detection",
        "Q10: filter(field == value), counter on enum",
        "Q11: flatten array field, counter",
        "Q13: same as Q11",
        "Q14: set-diff on array field between rows",
    ],
    "partially_supported": [
        "Q3: Source feeds — requires regex on free-text 'object'. No 'source' field.",
        "Q12: News events — requires regex on free-text 'object'. No 'headline'/'event_id' field.",
    ],
    "completely_missing": [
        "No cross-tape join primitive (can't link beats across tapes without loading both)",
        "No aggregation window primitive (can't ask 'in the last 5 minutes' without manual slice)",
        "No causal thread primitive (can't ask 'what led to X' — no precedence graph)",
        "No narrative arc primitive (can't ask 'what was the climax' — no peak detection on valence/priority)",
        "No text similarity primitive (can't ask 'which beats are about the same event' without fuzzy match)",
        "No percentile/rank primitive (can't ask 'top 3 most urgent beats' without rank)",
    ]
}

print("FULLY SUPPORTED (12 questions):")
for item in report["fully_supported"]:
    print(f"  ✓ {item}")
print()
print("PARTIALLY SUPPORTED (2 questions) — need schema additions:")
for item in report["partially_supported"]:
    print(f"  △ {item}")
print()
print("COMPLETELY MISSING (6 primitives) — need new math:")
for item in report["completely_missing"]:
    print(f"  ✗ {item}")
print()

# ============================================================
# RECOMMENDED SCHEMA ADDITIONS
# ============================================================
print("=" * 60)
print("RECOMMENDED SCHEMA ADDITIONS")
print("=" * 60)
print("""
Add these fields to tape rows (all optional, deterministic defaults):

  source: str           — the publisher/domain (extracted at ingest time)
  headline: str         — the event headline (extracted at ingest time)
  event_id: str         — stable identifier for dedup across tapes
  category: str         — domain tag (news, sports, incident, etc.)
  causal_parent: int    — lap number of the beat that caused this one
  arc_position: float   — normalized [0,1] position in narrative arc
""")

print()
print("=" * 60)
print("RECOMMENDED INQUIRY PRIMITIVES TO IMPLEMENT")
print("=" * 60)
print("""
All primitives should be pure functions: f(tape, params) -> result
Deterministic. No randomness. Traceable.

1. aggregate(field, fn)     — apply fn (count, sum, mean, min, max, mode) to a field
2. filter(tape, predicate)  — filter beats by field predicate
3. window(tape, start, end) — temporal or lap-range window
4. pairwise(fn, field)      — apply fn to consecutive pairs (delta, diff, etc.)
5. flatten(field)           — expand array field into flat list
6. extract(text, grammar)   — deterministic text extraction (domain/headline/source)
7. rank(field, top_k)       — rank beats by field value, return top k
8. causal_thread(lap_id, depth) — trace causal_parent chain backwards N steps
9. arc_peak(valence_fn, window) — detect peaks in valence/priority curve
10. similarity(a, b, metric) — text similarity between two beats (e.g. Jaccard on tokens)
11. join(tape_a, tape_b, key) — cross-tape join on matching field
12. setdiff(row_a, row_b, field) — set difference on array fields
""")
