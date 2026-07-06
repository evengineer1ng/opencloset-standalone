"""The kind registry — maps declared ``kind`` strings to organ/source factories.

This is what makes an `.oradio` *data*: the artifact names a world by kind ("ftb", "neikos",
…) and a telemetry source by kind ("atl_league", "moco", "simulated_spatial_array"); the
loader resolves the kind here. Factories import lazily (inside the function) so registering a
kind never pulls a heavy backend — only *using* it does.

To add a new world/source to the whole product, you register a factory here; no engine change.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from oradio_engine.bootstrap import ensure_repo_plugin_paths

# kind -> factory(name, **params) -> SimulationOrgan
ORGAN_KINDS: Dict[str, Callable[..., Any]] = {}
SOURCE_KINDS: Dict[str, Callable[..., Any]] = {}
# kind -> {"sensitive": bool, "reads": str} — what a telemetry source consumes, for the
# consent handshake. A source must ADVERTISE this before it ever touches the endpoint.
SOURCE_META: Dict[str, Dict[str, Any]] = {}


def register_organ(kind: str, factory: Callable[..., Any]) -> None:
    ORGAN_KINDS[kind] = factory


def register_source(kind: str, factory: Callable[..., Any], *, sensitive: bool = False, reads: str = "") -> None:
    SOURCE_KINDS[kind] = factory
    SOURCE_META[kind] = {"sensitive": sensitive, "reads": reads}


def build_organ(kind: str, name: str, **params: Any) -> Any:
    ensure_repo_plugin_paths()
    if kind not in ORGAN_KINDS:
        raise KeyError(f"unknown world/organ kind {kind!r}; known: {sorted(ORGAN_KINDS)}")
    return ORGAN_KINDS[kind](name, **params)


def build_source(kind: str, name: str, **params: Any) -> Any:
    ensure_repo_plugin_paths()
    if kind not in SOURCE_KINDS:
        raise KeyError(f"unknown telemetry source kind {kind!r}; known: {sorted(SOURCE_KINDS)}")
    return SOURCE_KINDS[kind](name, **params)


# --------------------------------------------------------------------------- #
# World organs (lazy)
# --------------------------------------------------------------------------- #
def _ftb(name: str, *, seed: int = 42, ratio: int = 7, **_):
    from oradio_engine.shims.ftb_shim import FTBOrgan
    return FTBOrgan.from_seed(name, seed, world_ticks_per_clock_tick=ratio)


def _neikos(name: str, *, seed: int = 42, ratio: int = 10, **_):
    from oradio_engine.shims.neikos_shim import NeikosOrgan
    return NeikosOrgan.from_seed(name, seed, world_ticks_per_clock_tick=ratio)


def _oracle(name: str, *, seed: int = 42, ratio: int = 15, decree_interval: int = 15, **_):
    from oradio_engine.shims.oracle_shim import OracleKingdomOrgan
    return OracleKingdomOrgan.from_seed(
        name, seed, world_ticks_per_clock_tick=ratio, decree_interval=decree_interval
    )


def _forkuniverse(name: str, *, ratio: int = 12, creation: Dict[str, Any] | None = None, **_):
    from oradio_engine.shims.forkuniverse_shim import ForkUniverseOrgan
    return ForkUniverseOrgan.from_request(name, world_ticks_per_clock_tick=ratio, **(creation or {}))


def _navigator(name: str, *, seed: int = 0, policy: dict | None = None, **_):
    from oradio_engine.shims.pokemon_shim import make_navigator
    return make_navigator(name, seed=seed, policy=policy)


register_organ("ftb", _ftb)
register_organ("neikos", _neikos)
register_organ("oracle", _oracle)
register_organ("forkuniverse", _forkuniverse)
register_organ("navigator", _navigator)


# --------------------------------------------------------------------------- #
# Telemetry sources (lazy)
# --------------------------------------------------------------------------- #
def _atl_league(name: str, *, db_path: str, **_):
    from oradio_engine.shims.atl_shim import ATLOrgan
    return ATLOrgan.from_db(name, db_path)


def _moco(name: str, *, telemetry_path: str | None = None, **_):
    from oradio_engine.shims.moco_shim import make_moco_organ
    return make_moco_organ(name, telemetry_path=telemetry_path)


def _simulated_spatial_array(name: str, *, nodes: list | None = None, dwell: int = 1, **_):
    from oradio_engine.shims.spatial_shim import make_simulated_spatial_array
    return make_simulated_spatial_array(name, nodes=nodes or ["front_door", "living_room", "kitchen"], dwell=dwell)


def _video_capture_sim(name: str, *, frames: list | None = None, **_):
    from oradio_engine.shims.pokemon_shim import make_simulated_capture
    return make_simulated_capture(name, frames=frames)


def _basketball_pbp(name: str, *, path: str | None = None, plays: list | None = None, per_poll: int = 1, **_):
    from oradio_engine.shims.basketball_shim import make_basketball_pbp
    return make_basketball_pbp(name, path=path, plays=plays, per_poll=per_poll)


def _tape_replay(name: str, *, path: str | None = None, rows: list | None = None, per_poll: int = 1, **_):
    from oradio_engine.shims.tape_replay import make_tape_replay
    return make_tape_replay(name, path=path, rows=rows, per_poll=per_poll)


def _pc_telemetry(name: str, **_):
    # STUB (sensitive): the real adapter reads OS resource/activity metrics. Simulated for now.
    from oradio_engine.live import LiveFeedOrgan, ScriptedSource
    seq = [{"title": "cpu_spike", "body": "cpu 82%", "type": "pc", "priority": 0.5},
           {"title": "focus", "body": "switched to editor", "type": "pc", "priority": 0.4},
           {"title": "idle", "body": "no input 5m", "type": "pc", "priority": 0.3}]
    return LiveFeedOrgan(name, source=ScriptedSource(seq))


def _ring_telemetry(name: str, **_):
    # STUB (sensitive): the real adapter reads a COLMI R02 over BLE (HR / motion / sleep).
    from oradio_engine.live import LiveFeedOrgan, ScriptedSource
    seq = [{"title": "hr", "body": "heart rate 72", "type": "ring", "priority": 0.4},
           {"title": "motion", "body": "walking", "type": "ring", "priority": 0.5},
           {"title": "hr_rise", "body": "heart rate 118", "type": "ring", "priority": 0.7}]
    return LiveFeedOrgan(name, source=ScriptedSource(seq))


# benign (simulated / public) — no consent needed
register_source("simulated_spatial_array", _simulated_spatial_array)
register_source("video_capture_sim", _video_capture_sim)
register_source("basketball_pbp", _basketball_pbp)  # public sports event log; benign, replay-only
register_source("tape_replay", _tape_replay)        # generic pre-baked thin-wire tape; benign
# sensitive — touch personal endpoints; the Club must get consent first
register_source("atl_league", _atl_league, sensitive=True, reads="a local league.sqlite (your research/trading data)")
register_source("moco", _moco, sensitive=True, reads="your motion/pose telemetry (MoCo classifier output)")
register_source("pc_telemetry", _pc_telemetry, sensitive=True, reads="your PC activity + resource metrics")
register_source("ring_telemetry", _ring_telemetry, sensitive=True, reads="your COLMI R02 ring: heart rate, motion, sleep")
