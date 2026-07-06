import sys
sys.path.insert(0, r"C:\Users\evana\OneDrive\Documents\Radio-OS")
import importlib.util

spec = importlib.util.spec_from_file_location("plugins.neikos", r"C:\Users\evana\OneDrive\Documents\Radio-OS\plugins\neikos\__init__.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["plugins.neikos"] = mod
spec.loader.exec_module(mod)

topo = mod.generate_island_topology(1)
species_map = mod.generate_species_roster(topo)

# Githi (solo stage1) - should have evo_level=None
sp = species_map.get("sp_0229")
d = sp.to_dict()
print(f"Githi: stage={d['evo_stage']}, evolves_to={d['evolves_to']}, evo_level={d['evo_level']}")
assert d["evo_level"] is None, f"FAIL: solo stage1 evo_level should be None, got {d['evo_level']}"

# Find a stage1 species WITH evolution
stage1_evo = next(s for s in species_map.values() if s.evolution_stage == 1 and s.evolves_to)
d2 = stage1_evo.to_dict()
print(f"{stage1_evo.name}: stage={d2['evo_stage']}, evolves_to={d2['evolves_to']}, evo_level={d2['evo_level']}")
assert d2["evo_level"] == 16, f"FAIL: stage1+evo evo_level should be 16, got {d2['evo_level']}"

# Find a stage2 species WITH evolution (mid-evolution)
stage2_mid = next(s for s in species_map.values() if s.evolution_stage == 2 and s.evolves_to)
d3 = stage2_mid.to_dict()
print(f"{stage2_mid.name}: stage={d3['evo_stage']}, evolves_to={d3['evolves_to']}, evo_level={d3['evo_level']}")
assert d3["evo_level"] == 36, f"FAIL: stage2+evo evo_level should be 36, got {d3['evo_level']}"

# Find a stage2 final (no evolves_to)
stage2_final = next(s for s in species_map.values() if s.evolution_stage == 2 and not s.evolves_to)
d4 = stage2_final.to_dict()
print(f"{stage2_final.name}: stage={d4['evo_stage']}, evolves_to={d4['evolves_to']}, evo_level={d4['evo_level']}")
assert d4["evo_level"] is None, f"FAIL: stage2 final evo_level should be None, got {d4['evo_level']}"

# Count totals as sanity check
wrong = sum(1 for s in species_map.values()
            if not s.evolves_to and s.to_dict().get("evo_level") is not None)
print(f"Species with no evolves_to but non-None evo_level: {wrong} (should be 0)")
assert wrong == 0, "FAIL: some non-evolving species still expose evo_level"

print("ALL ASSERTIONS PASSED")
