"""Offline integration test: XP, leveling, and evolution pipeline."""
import sys
sys.path.insert(0, r"C:\Users\evana\OneDrive\Documents\Radio-OS")
import importlib.util

spec = importlib.util.spec_from_file_location(
    "plugins.neikos",
    r"C:\Users\evana\OneDrive\Documents\Radio-OS\plugins\neikos\__init__.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["plugins.neikos"] = mod
spec.loader.exec_module(mod)

print("Module loaded OK")

# ── 1. XP thresholds are monotonically increasing ──────────
for lvl in range(1, 6):
    print(f"  Lv{lvl}: floor={mod.xp_for_level(lvl)}, marginal_to_next={mod.xp_to_next_level(lvl)}")
assert mod.xp_for_level(1) == 0
assert mod.xp_for_level(2) == 20   # 1*20
assert mod.xp_for_level(3) == 60   # (1+2)*20
print("PASS: XP thresholds correct")

# ── 2. award_battle_xp produces level_up events ────────────
topo = mod.generate_island_topology(1)
species_map = mod.generate_species_roster(topo)

# Pick a stage1 creature with evolution
stage1_evo = next(s for s in species_map.values() if s.evolution_stage == 1 and s.evolves_to)
print(f"Using: {stage1_evo.name} ({stage1_evo.species_id}), stage={stage1_evo.evolution_stage}, evolves_to={stage1_evo.evolves_to}")

inst = mod.CreatureInstance(
    instance_id=stage1_evo.species_id + "_test",
    species_id=stage1_evo.species_id,
    level=1,
    temperament=0.5,
)
inst.xp = 0

all_lu = []
battle_count = 0
while inst.level < 16 and battle_count < 100:
    lu = mod.award_battle_xp(inst, stage1_evo, max(5.0, float(inst.level)), 10, True)
    all_lu.extend(lu)
    battle_count += 1

print(f"Battles to reach Lv16: {battle_count}, final level={inst.level}, xp={inst.xp}")
xp_needed = mod.xp_for_level(16)
print(f"  (Lv16 XP floor: {xp_needed}, ~{xp_needed // 60} battles at 60 XP each)")
assert inst.level >= 16, f"FAIL: expected level >= 16, got {inst.level} (total XP={inst.xp}, need {xp_needed})"
assert len(all_lu) >= 15, f"FAIL: expected at least 15 level-up events, got {len(all_lu)}"
print(f"PASS: {len(all_lu)} level-up events over {battle_count} battles")

# ── 3. check_evolution triggers at Lv16 for stage1 ────────
player_team = [(inst.instance_id, inst.species_id)]
evo_evs = mod.check_evolution(inst, species_map, player_team)
print(f"Evolution events at Lv{inst.level}: {len(evo_evs)}")
assert len(evo_evs) == 1, f"FAIL: expected 1 evolution event, got {len(evo_evs)}"
ev = evo_evs[0]
assert ev["type"] == "evolved"
assert ev["new_evo_stage"] == 2
assert ev["old_species_id"] == stage1_evo.species_id
next_sp = species_map[ev["new_species_id"]]
print(f"  {ev['old_species_name']} -> {ev['new_species_name']} (stage {ev['new_evo_stage']}) at Lv{ev['level']}")
print(f"  player_team updated: {player_team[0][1]} should == {ev['new_species_id']}")
assert player_team[0][1] == ev["new_species_id"], f"FAIL: player_team not updated. Got {player_team[0][1]}"
assert inst.species_id == ev["new_species_id"], f"FAIL: inst.species_id not updated. Got {inst.species_id}"
print("PASS: Evolution triggered, player_team + inst.species_id updated correctly")

# ── 4. check_evolution does NOT re-trigger on same creature ─
evo_evs2 = mod.check_evolution(inst, species_map, player_team)
assert len(evo_evs2) == 0, f"FAIL: evolution should not re-trigger, got {len(evo_evs2)} events"
print("PASS: No double-evolution on same creature")

# ── 5. Stage2 → Stage3 evolution at Lv36 ──────────────────
# Find a stage2 species with evolution
stage2_evo = species_map.get(ev["new_species_id"])
if stage2_evo and stage2_evo.evolves_to:
    # Drive to level 36
    while inst.level < 36 and battle_count < 400:
        lu = mod.award_battle_xp(inst, stage2_evo, max(5.0, float(inst.level)), 10, True)
        battle_count += 1
    print(f"Battles to reach Lv36: total={battle_count}, level={inst.level}")
    assert inst.level >= 36, f"FAIL: expected level >= 36, got {inst.level}"
    evo_evs3 = mod.check_evolution(inst, species_map, player_team)
    assert len(evo_evs3) == 1, f"FAIL: expected stage2->3 evolution, got {len(evo_evs3)}"
    assert evo_evs3[0]["new_evo_stage"] == 3
    print(f"  {evo_evs3[0]['old_species_name']} -> {evo_evs3[0]['new_species_name']} (stage 3)")
    print("PASS: Stage2 -> Stage3 evolution at Lv36")
else:
    print(f"SKIP: {stage2_evo.name if stage2_evo else '?'} has no stage3 evolution (terminal stage2)")

# ── 6. level_up event data shape ────────────────────────────
sample_lu = all_lu[0]
assert "instance_id" in sample_lu
assert "new_level" in sample_lu
assert "xp_gained" in sample_lu
assert "species_name" in sample_lu
print(f"PASS: level_up event shape correct: {list(sample_lu.keys())}")

# ── 7. evolved event data shape ─────────────────────────────
assert "instance_id" in ev
assert "old_species_name" in ev
assert "new_species_name" in ev
assert "new_evo_stage" in ev
assert "level" in ev
print(f"PASS: evolved event shape correct: {list(ev.keys())}")

# ── 8. XP progress fields in state ─────────────────────────
# Simulate what /api/state does
xp_floor = mod.xp_for_level(inst.level)
xp_to_next = mod.xp_to_next_level(inst.level)
print(f"  State XP fields: level={inst.level}, xp={inst.xp}, xp_floor={xp_floor}, xp_to_next={xp_to_next}")
assert inst.xp >= xp_floor, "FAIL: inst.xp should be >= xp_floor for current level"
print("PASS: XP floor/next values sane")

print("\n=== ALL TESTS PASSED ===")
