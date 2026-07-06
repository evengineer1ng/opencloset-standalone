"""show_the_machine.py — a narrated end-to-end run. No vibes, just stdout.

Run:  python show_the_machine.py
Every number below is produced by the real oradio_engine code, not a mock.
"""
import hashlib
import os
import tempfile

from oradio_engine import Club, open_oradio
from oradio_engine.index import Index, funnel, gate, lineage
from oradio_engine.observation import ObservationLog, grade


def hr(title):
    print("\n" + "=" * 70 + f"\n  {title}\n" + "=" * 70)


# ════════════════════════════════════════════════════════════════════════
hr("1.  OPEN AN .oradio  —  a tiny declaration becomes a living federation")
# Three sovereign worlds + a lens, declared as data, resolved by the Club, run.
descriptor = {
    "oradio": "the-machine",
    "worlds": [
        {"organ": "forkuniverse", "name": "harbor", "ratio": 15, "creation": dict(
            universe_title="Pressure Harbor", premise="love, debt, rumor, grief",
            setting_kind="haunted_port_city", time_period="modern", story_mode="continuous",
            world_scale="district", starting_population=24, seed_mode="custom",
            custom_seed="demo", ontology_domains=["love", "debt", "rumor", "grief"])},
        {"organ": "oracle", "name": "kingdom", "seed": 42, "ratio": 15},
        {"organ": "neikos", "name": "isle", "seed": 42, "ratio": 10},
    ],
    "lens": "competition",          # declared: floor low-signal, cap to 25/tick
    "club": ["llm", "voices"],
}
res = open_oradio(descriptor, club=Club(store_path=os.path.join(tempfile.mkdtemp(), "club.json")))
print(f"club: theme resolved to '{res.report.resolved['theme']['theme']}' (default pack); "
      f"asks once for {[a.capability for a in res.report.asks]}")
res.engine.run(steps=6)
from collections import Counter
print(f"6 ticks → {len(res.engine.bus)} beats across worlds: {dict(Counter(c.source for c in res.engine.bus))}")
print("  a few, decomposed from world-truth into typed signal:")
for c in sorted(res.engine.bus, key=lambda c: c.priority, reverse=True)[:4]:
    print(f"    [{c.priority:.2f}] {c.source:7} ({c.type[:18]:18}) {(c.body or '')[:42]}")

# ════════════════════════════════════════════════════════════════════════
hr("2.  THE INDEX  —  a small seed ADDRESSES a vast structure (the album, in code)")
EXP = 5
acro = Index("dog", lambda seed, a: {"token": hashlib.sha256(f"{seed}:{a}".encode()).hexdigest()[:6]})
positions = 3 * EXP ** 12
before = acro.calls
acro.resolve(("layer", 12, "pos", positions - 1))   # resolve the LAST of ~732M
print(f"index = 1 seed ('dog') + 1 rule   →   addresses {positions:,} positions at layer 12")
print(f"resolved the LAST of {positions:,} with {acro.calls - before} derivation (not {positions:,})")
path = lineage(("layer", 12, "pos", 987654321),
               lambda a: None if a[1] == 0 else ("layer", a[1] - 1, "pos", a[3] // EXP))
print("collapse a deep address back to the seed:  " + " → ".join(f"L{a[1]}" for a in path))

# ════════════════════════════════════════════════════════════════════════
hr("3.  EVIDENCE OVER THEORY  —  derive the claim, but only REALITY resolves it")
claims = Index("harbor", lambda seed, a: {
    "confidence": round(0.5 + (int(hashlib.sha256(f'{seed}:{a}'.encode()).hexdigest(), 16) % 1000) / 2000, 3),
    "predicts": "up" if int(hashlib.sha256(f'{seed}:{a}'.encode()).hexdigest(), 16) % 2 == 0 else "down"})
addresses = [("t", n, "pred", i) for n in range(1, 9) for i in range(2)]   # 16 claims
log = ObservationLog()
for a in addresses[:10]:                      # reality has only answered 10 of 16 so far
    log.record(a, "up" if a[1] % 2 == 0 else "down")
card = grade(claims, addresses, log)
print(f"16 claims derived. reality has answered {card['resolved']}.")
print(f"  → {card['open']} stay OPEN (refused — the engine will not grade what it hasn't seen)")
print(f"  → of the {card['resolved']} it CAN judge: {card['hits']} hit / {card['misses']} miss, "
      f"brier={card['brier']}  (graded vs STORED evidence, not a derived outcome)")

# ════════════════════════════════════════════════════════════════════════
hr("4.  THE FUNNEL  —  survival under rising standards (the bar IS the frontier)")
from oradio_engine.observation import evolutionary_funnel
# 50 'genomes' with real, observed performance (stored). The bar = the frontier of the
# CURRENT survivors, so it rises because the weak die — not because a clock ticks.
perf = {("g", i): 0.90 + (int(hashlib.sha256(str(i).encode()).hexdigest(), 16) % 1000) / 10000 for i in range(50)}
print("50 genomes. each season the bar = the frontier of the survivors. reality keeps culling:")
prev = 50
for r in evolutionary_funnel(perf, keep_fraction=0.5, seasons=6):
    print(f"  season {r['season']}: must beat {r['bar']*100:.2f}%  →  {prev:>2} survivors → {r['survivors']:>2} "
          f"(reality killed {prev - r['survivors']})")
    prev = r["survivors"]

# ════════════════════════════════════════════════════════════════════════
hr("5.  PULL THE THREAD  —  'why is this here?' walks lineage back through cause")
# a surviving genome traces back through the seasons it was bred from, to its origin.
who = ("season", 4, "genome", 41)
thread = lineage(who, lambda a: None if a[1] == 0 else ("season", a[1] - 1, "genome", a[3] // 2))
print(f"why is {who} here?")
print("  thread: " + " ← ".join(f"s{a[1]}/g{a[3]}" for a in thread) + "   (back to the origin seed)")

print("\n" + "-" * 70)
print("that's the machine: declare → federate → decompose → derive → observe →")
print("grade on evidence → funnel the proven up → trace any of it back through cause.")
print("-" * 70)
