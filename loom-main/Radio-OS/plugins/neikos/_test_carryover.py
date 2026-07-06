"""
Behavioral carryover integration test.
Exercises: play -> compute_axis -> compute_signature -> save -> apply_profile -> verify island shifts.
No network, no server. Offline only.
Run from repo root: python plugins/neikos/_test_carryover.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import tempfile, json

# Import the module under test
import importlib.util, pathlib
_init = pathlib.Path(__file__).parent / "__init__.py"
spec = importlib.util.spec_from_file_location("neikos_plugin", _init)
nk = importlib.util.module_from_spec(spec)
sys.modules["neikos_plugin"] = nk  # register so dataclass __module__ resolves
spec.loader.exec_module(nk)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

def check(label, cond, detail=""):
    if cond:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}{(' — ' + detail) if detail else ''}")
    return cond

errors = 0

print("\n=== Carryover Test 1: CURIOUS axis via heavy anomaly/explore ===")
t1 = nk.PlayerTrajectory()
t1.exploration_depth   = 60.0
t1.anomaly_exposure    = 55.0
t1.competitive_focus   = 10.0
t1.research_investment = 20.0
t1.risk_appetite       = 30.0
t1.breeding_intensity  = 5.0
ax1 = nk.compute_behavioral_axis(t1)
errors += not check("axis == CURIOUS", ax1 == nk.BehavioralAxis.CURIOUS, f"got {ax1}")

print("\n=== Carryover Test 2: DOMINANT axis via competitive play ===")
t2 = nk.PlayerTrajectory()
t2.competitive_focus   = 80.0
t2.risk_appetite       = 75.0
t2.exploration_depth   = 5.0
t2.anomaly_exposure    = 5.0
t2.research_investment = 5.0
t2.breeding_intensity  = 10.0
ax2 = nk.compute_behavioral_axis(t2)
errors += not check("axis == DOMINANT", ax2 == nk.BehavioralAxis.DOMINANT, f"got {ax2}")

print("\n=== Carryover Test 3: STABILIZING axis via research/breeding ===")
t3 = nk.PlayerTrajectory()
t3.research_investment = 70.0
t3.breeding_intensity  = 60.0
t3.anomaly_exposure    = 5.0
t3.competitive_focus   = 10.0
t3.risk_appetite       = 20.0
t3.exploration_depth   = 15.0
ax3 = nk.compute_behavioral_axis(t3)
errors += not check("axis == STABILIZING", ax3 == nk.BehavioralAxis.STABILIZING, f"got {ax3}")

print("\n=== Carryover Test 4: compute_behavioral_signature populates all fields ===")
ledger = nk.IslandLedger()
tier = nk.ContainmentTier.TIER_II
sig = nk.compute_behavioral_signature(t1, ledger, tier, seed=42)
errors += not check("sig.behavioral_axis == CURIOUS", sig.behavioral_axis == "CURIOUS", sig.behavioral_axis)
errors += not check("sig.anomaly_engagement_history > 0", sig.anomaly_engagement_history > 0, str(sig.anomaly_engagement_history))
errors += not check("sig.completed_tier == 2", sig.completed_tier == 2, str(sig.completed_tier))
errors += not check("sig.echo_seeds == [42]", sig.echo_seeds == [42], str(sig.echo_seeds))
errors += not check("sig.run_count == 1", sig.run_count == 1, str(sig.run_count))

print("\n=== Carryover Test 5: save + load round-trip ===")
with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
    tmp = f.name
try:
    path = nk.save_behavioral_profile(sig, path=tmp)
    loaded = nk.load_behavioral_profile(path=tmp)
    errors += not check("loaded is not None", loaded is not None)
    errors += not check("round-trip axis", loaded.behavioral_axis == "CURIOUS", str(loaded.behavioral_axis))
    errors += not check("round-trip run_count == 1", loaded.run_count == 1, str(loaded.run_count))
    errors += not check("round-trip echo_seeds", loaded.echo_seeds == [42], str(loaded.echo_seeds))
finally:
    os.unlink(tmp)

print("\n=== Carryover Test 6: save merge accumulates anomaly history ===")
with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
    tmp2 = f.name
try:
    # Run 1
    sig1 = nk.compute_behavioral_signature(t1, ledger, nk.ContainmentTier.TIER_I, seed=1)
    nk.save_behavioral_profile(sig1, path=tmp2)
    p1 = nk.load_behavioral_profile(path=tmp2)
    # Run 2 with different trajectory
    sig2 = nk.compute_behavioral_signature(t2, ledger, nk.ContainmentTier.TIER_II, seed=2)
    nk.save_behavioral_profile(sig2, path=tmp2)
    p2 = nk.load_behavioral_profile(path=tmp2)
    errors += not check("run_count == 2 after merge", p2.run_count == 2, str(p2.run_count))
    errors += not check("anomaly accumulates", p2.anomaly_engagement_history >= p1.anomaly_engagement_history, 
                        f"{p2.anomaly_engagement_history} >= {p1.anomaly_engagement_history}")
    errors += not check("completed_tier == max(1,2) == 2", p2.completed_tier == 2, str(p2.completed_tier))
    errors += not check("echo_seeds contains both", set(p2.echo_seeds) == {1, 2}, str(p2.echo_seeds))
    # Latest axis wins
    errors += not check("latest axis wins (DOMINANT)", p2.behavioral_axis == "DOMINANT", p2.behavioral_axis)
finally:
    os.unlink(tmp2)

def make_ctrl(seed=1):
    """Create a NKController with a minimal runtime stub and init island."""
    import queue as _q
    stub = {"nk_cmd_q": _q.Queue(), "nk_ui_q": _q.Queue()}
    ctrl = nk.NKController(runtime_stub=stub, config={})
    ctrl.init_island(seed)
    return ctrl

print("\n=== Carryover Test 7: apply_profile_to_island modifies state ===")
ctrl = make_ctrl(seed=1)
# Access freshly inited state
st = ctrl._state
errors += not check("state initialized", st is not None)
base_anomaly_stability = st.ledger.anomaly_stability

# Build a high-disruption / high-anomaly profile
profile_hi = nk.BehavioralProfileSignature(
    behavioral_axis="CURIOUS",
    anomaly_engagement_history=50.0,  # > 20 threshold
    ecological_disruption_pattern=0.8,  # > 0.3 threshold
    dominance_harmony_bias=0.6,         # > 0.1 threshold
    completed_tier=2,
    echo_seeds=[1],
    run_count=1,
)
before_comp = st.player_trajectory.competitive_focus
before_stab = st.ledger.anomaly_stability
before_eco  = st.ledger.ecological_balance

nk.apply_profile_to_island(profile_hi, st)

errors += not check("anomaly_stability decreased", st.ledger.anomaly_stability < before_stab,
                    f"{st.ledger.anomaly_stability} vs {before_stab}")
errors += not check("ecological_balance decreased", st.ledger.ecological_balance < before_eco,
                    f"{st.ledger.ecological_balance} vs {before_eco}")
errors += not check("competitive_focus nudged up (high dominance bias)", 
                    st.player_trajectory.competitive_focus > before_comp,
                    f"{st.player_trajectory.competitive_focus} vs {before_comp}")

print("\n=== Carryover Test 8: run_count >=2 raises base tier floor ===")
ctrl2 = make_ctrl(seed=1)
st2 = ctrl2._state
base_tier_before = st2.base_tier

profile_run2 = nk.BehavioralProfileSignature(
    behavioral_axis="CURIOUS",
    anomaly_engagement_history=5.0,
    ecological_disruption_pattern=0.0,
    dominance_harmony_bias=0.0,
    completed_tier=1,
    echo_seeds=[99],
    run_count=2,   # triggers tier floor escalation
)
nk.apply_profile_to_island(profile_run2, st2)
errors += not check("base_tier escalated by 1", st2.base_tier.value == base_tier_before.value + 1,
                    f"{st2.base_tier} vs was {base_tier_before}")
errors += not check("current_tier matches base_tier", st2.current_tier == st2.base_tier)

print("\n=== Carryover Test 9: full NKController expedition lifecycle ===")
# Use a temp profile path so we don't pollute the real save
with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
    tmp3 = f.name
os.unlink(tmp3)  # delete so save_behavioral_profile treats it as fresh

# Monkeypatch profile path
orig_path_fn = nk._nk_profile_path
nk._nk_profile_path = lambda: tmp3

try:
    ctrl3 = make_ctrl(seed=1)
    st3 = ctrl3._state
    # Simulate real play: move around + anomaly exposure
    st3.player_trajectory.exploration_depth  = 45.0
    st3.player_trajectory.anomaly_exposure   = 40.0
    st3.player_trajectory.competitive_focus  = 15.0
    st3.player_trajectory.research_investment= 30.0
    # Complete expedition
    ctrl3._cmd_new_expedition(next_seed=7)

    # Load and check saved profile
    p = nk.load_behavioral_profile(path=tmp3)
    errors += not check("profile saved after expedition", p is not None)
    errors += not check("axis reflects play (CURIOUS expected)", p.behavioral_axis == "CURIOUS",
                        f"got {p.behavioral_axis}")
    errors += not check("anomaly history > 0", p.anomaly_engagement_history > 0)
    errors += not check("run_count == 1", p.run_count == 1, str(p.run_count))

    # New island should have profile applied
    st4 = ctrl3._state
    errors += not check("new seed applied (7)", st4.seed == 7, str(st4.seed))
    # Since run_count==1 in the profile (first expedition), floor is not raised yet
    # but ledger nudges should be in effect; we verify state is fresh (tick=0)
    errors += not check("new island tick=0", st4.tick == 0, str(st4.tick))

    # Now complete a second expedition — should trigger tier floor escalation
    st4.player_trajectory.exploration_depth  = 45.0
    st4.player_trajectory.anomaly_exposure   = 35.0
    ctrl3._cmd_new_expedition(next_seed=42)
    p2 = nk.load_behavioral_profile(path=tmp3)
    errors += not check("run_count == 2 after 2nd expedition", p2.run_count == 2, str(p2.run_count))
    errors += not check("echo_seeds includes both seeds", set(p2.echo_seeds).issuperset({1, 7}), str(p2.echo_seeds))
    st5 = ctrl3._state
    # Tier floor should be raised by 1 on 3rd expedition
    ctrl3._cmd_new_expedition(next_seed=99)
    st6 = ctrl3._state
    seed1_tier = nk._seed_to_base_tier(99)
    expected_escalated = nk.ContainmentTier(min(5, seed1_tier.value + 1))
    errors += not check(f"tier floor raised to {expected_escalated.name} on run 3",
                        st6.base_tier == expected_escalated,
                        f"got {st6.base_tier}")
finally:
    try:
        os.unlink(tmp3)
    except FileNotFoundError:
        pass
    nk._nk_profile_path = orig_path_fn

print("\n=== Carryover Test 10: None profile is safe no-op ===")
ctrl4 = make_ctrl(seed=1)
st_noop = ctrl4._state
comp_before = st_noop.player_trajectory.competitive_focus
nk.apply_profile_to_island(None, st_noop)
errors += not check("no-op on None profile", st_noop.player_trajectory.competitive_focus == comp_before)

# --- Summary ---
print()
if errors == 0:
    print(f"=== ALL TESTS PASSED ===\n")
else:
    print(f"=== {errors} TEST(S) FAILED ===\n")
    sys.exit(1)
