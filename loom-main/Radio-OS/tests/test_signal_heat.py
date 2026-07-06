"""Tests for signal_heat.py — the emergent-airtime engine.

Pure unit tests (no runtime). Runnable two ways:
    python -m pytest tests/test_signal_heat.py -q     (if pytest is installed)
    python tests/test_signal_heat.py                  (standalone; the venv has no pytest)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import signal_heat as sh  # noqa: E402

T0 = 1_000_000.0


def _cfg(**over):
    return sh.normalize_heat_config(over or {}, {})


def test_decay_halves_at_half_life():
    cfg = _cfg()
    st = {}
    sh.bump_heat(st, {"source": "a", "priority": 80}, cfg, T0)
    h0 = sh.source_heat(st, "a", cfg, T0)
    h1 = sh.source_heat(st, "a", cfg, T0 + cfg["half_life_sec"])
    assert h0 > 0
    assert abs(h1 - h0 / 2) < 1e-6


def test_bump_raises_then_cools():
    cfg = _cfg()
    st = {}
    sh.bump_heat(st, {"source": "a", "priority": 50}, cfg, T0)
    hot = sh.source_heat(st, "a", cfg, T0)
    cold = sh.source_heat(st, "a", cfg, T0 + 8 * cfg["half_life_sec"])
    assert hot > cold
    assert cold < 0.05


def test_repeated_bumps_accumulate_but_cap():
    cfg = _cfg()
    st = {}
    for _ in range(10):
        sh.bump_heat(st, {"source": "a", "priority": 100}, cfg, T0)
    assert sh.source_heat(st, "a", cfg, T0) <= 1.0 + 1e-9


def test_quiet_floor_drops_cold_sources():
    cfg = _cfg()
    st = {}
    sh.bump_heat(st, {"source": "cold", "priority": 40}, cfg, T0)
    # rank long after the bump → source has receded below the quiet floor and is dropped
    later = T0 + 10 * cfg["half_life_sec"]
    ranked = sh.rank_candidates([{"source": "cold", "priority": 90, "post_id": "x"}], st, cfg, later)
    assert ranked == []


def test_interrupt_flips_at_threshold():
    cfg = _cfg(interrupt_threshold=0.6)
    st = {}
    sh.bump_heat(st, {"source": "a", "priority": 50}, cfg, T0)  # heat 0.5 < 0.6
    ranked_low = sh.rank_candidates([{"source": "a", "priority": 10, "post_id": "1"}], st, cfg, T0)
    assert ranked_low and ranked_low[0]["interrupt"] is False
    sh.bump_heat(st, {"source": "a", "priority": 30}, cfg, T0)  # heat ~0.8 >= 0.6
    ranked_hi = sh.rank_candidates([{"source": "a", "priority": 10, "post_id": "2"}], st, cfg, T0)
    assert ranked_hi[0]["interrupt"] is True


def test_hot_source_outranks_higher_priority_cold_source():
    cfg = _cfg()
    st = {}
    sh.bump_heat(st, {"source": "hot", "priority": 80}, cfg, T0)      # hot world
    sh.bump_heat(st, {"source": "cold", "priority": 20}, cfg, T0)     # barely warm
    ranked = sh.rank_candidates(
        [{"source": "cold", "priority": 95, "post_id": "c"},
         {"source": "hot", "priority": 40, "post_id": "h"}],
        st, cfg, T0,
    )
    assert ranked[0]["source"] == "hot"


def test_is_silent_when_all_cold():
    cfg = _cfg()
    st = {}
    assert sh.is_silent(st, cfg, T0) is True  # nothing observed yet
    sh.bump_heat(st, {"source": "a", "priority": 60}, cfg, T0)
    assert sh.is_silent(st, cfg, T0) is False
    assert sh.is_silent(st, cfg, T0 + 12 * cfg["half_life_sec"]) is True


def test_per_source_override_changes_cooling():
    # A source with a tiny half-life cools far faster than the global default.
    per_source = {"fast": {"heat": {"half_life_sec": 60}}, "slow": {"heat": {"half_life_sec": 3600}}}
    cfg = sh.normalize_heat_config({}, per_source)
    st = {}
    sh.bump_heat(st, {"source": "fast", "priority": 80}, cfg, T0)
    sh.bump_heat(st, {"source": "slow", "priority": 80}, cfg, T0)
    later = T0 + 600  # 10 min
    assert sh.source_heat(st, "fast", cfg, later) < sh.source_heat(st, "slow", cfg, later)


def test_normalize_config_merges_global_and_defaults():
    cfg = sh.normalize_heat_config({"gain": 2.0}, {"x": {"heat": {"quiet_floor": 0.2}}})
    assert cfg["gain"] == 2.0
    assert cfg["half_life_sec"] == sh.DEFAULT_HEAT_CONFIG["half_life_sec"]
    assert cfg["sources"]["x"]["quiet_floor"] == 0.2


def test_integration_curate_reorders_by_heat():
    """GeneratedMetaPlugin.curate_candidates must shift airtime to the hot world and let it recede."""
    import importlib.util
    gen_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins", "meta", "generated.py"))
    spec = importlib.util.spec_from_file_location("generated_under_test", gen_path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    plugin = gen.GeneratedMetaPlugin()
    plugin.spec = {
        "station": "TestFM",
        "sources": {
            "trades": {"lens": "a trade update", "heat": {"half_life_sec": 3600}},
            "storm": {"lens": "a storm update", "heat": {"half_life_sec": 30}},
        },
        "voices": ["host"],
    }
    mem = {}
    r1 = plugin.curate_candidates(
        [{"source": "storm", "priority": 90, "post_id": "s1"},
         {"source": "trades", "priority": 50, "post_id": "t1"}], mem)
    assert r1[0]["source"] == "storm"  # the just-spiked world leads

    # 10 minutes pass: storm (30s half-life) goes cold, trades (1h) stays warm.
    for s in mem["_signal_heat_state"]["sources"].values():
        s["last_ts"] -= 600
    r2 = plugin.curate_candidates([{"source": "trades", "priority": 55, "post_id": "t2"}], mem)
    assert r2 and all(c["source"] == "trades" for c in r2)  # the steady world now carries the show


def _run_standalone():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERR   {fn.__name__}: {exc!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
