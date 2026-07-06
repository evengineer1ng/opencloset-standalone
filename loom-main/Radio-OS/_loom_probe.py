"""Headless end-to-end probe: does a person's IDEA become a distinct .oradio world?

We build two Loom descriptors via the real loom_studio compiler, hold the SEED
constant, and vary only the PREMISE/idea. Then we load each through the real
oradio_engine federation and compare the bus. If the premise drives content, the
two runs diverge. If only the seed matters, the idea is cosmetic.
"""
import json
from loom_studio import build_loom_descriptor, LoomFormState
from oradio_engine import load_oradio

def make(idea_title, premise):
    return LoomFormState(
        name=idea_title, world_kind="forkuniverse", seed=42, premise=premise,
        enabled_signals=[], spatial_nodes=[], loop_mode="builtin",
        builtin_theme="ribbon", loop_path="", voice_provider="none",
        voice_assignments={}, transient_enabled=False, transient_title="",
        transient_min_priority=0.6, transient_body_template="",
    )

def run(label, state, ticks=25, genre=None):
    desc = build_loom_descriptor(state)
    # Patch the one missing required field loom_studio omits, so forkuniverse loads at all.
    desc["world"]["creation"]["time_period"] = "present_day"
    if genre:
        desc["world"]["creation"]["genre_mix"] = genre
    eng = load_oradio(desc)
    for _ in range(ticks):
        eng.tick()
    bus = eng.bus
    headlines = [getattr(c, "title", "") for c in bus][:6]
    print(f"\n=== {label} ===")
    print(f"  premise: {state.premise!r}")
    print(f"  bus size: {len(bus)}")
    for h in headlines:
        print(f"    - {h}")
    return [getattr(c, "title", "") for c in bus]

a = run("LOOM A: goosebumps horror neighborhood",
        make("Fear Street", "a goosebumps horror neighborhood where a cursed ventriloquist dummy stalks kids"),
        genre={"horror": 1.0})
b = run("LOOM B: corporate quarterly earnings",
        make("Boardroom", "a tense corporate boardroom tracking quarterly earnings and hostile takeovers"),
        genre={"drama": 1.0})

print("\n=== VERDICT ===")
print(f"  same seed (42), different premise + genre.")
print(f"  buses identical? {a == b}")
print(f"  A-only headlines: {len(set(a) - set(b))} / {len(set(a))}")
print(f"  shared headlines: {len(set(a) & set(b))}")
