"""ATL / League PUSH organ — polls league.sqlite (read-only), no docker/wanda imports.

Tested two ways:
  1. against a synthetic fixture DB (portable, deterministic) — proves event candidates,
     gradable predictions into the evidence service, cursor advance, and record->replay;
  2. an optional smoke test against the REAL wanda league.sqlite if it exists on this machine.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, EvidenceService, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.atl_shim import ATLOrgan  # noqa: E402

REAL_DB = r"C:\Users\evana\Documents\freqtradebotchallenge\wanda\algo_trading_league\data\league.sqlite"


def _fixture_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table dev_runtime_events (id integer primary key, candidate_id text,
            created_at text, event_type text, title text, details text);
        create table ml_promotion_recommendations (id integer primary key, experiment_run_id text,
            candidate_name text, recommendation text, rationale text, blockers text,
            target_surface text, created_at text);
        """
    )
    con.executemany(
        "insert into dev_runtime_events (id,candidate_id,created_at,event_type,title,details) values (?,?,?,?,?,?)",
        [
            (1, "1", "2026-06-12T16:00:00Z", "scheduler", "Start command issued.", "Start completed."),
            (2, "1", "2026-06-12T16:01:00Z", "promotion", "Timmy promoted", "Top-50 surged."),
            (3, "2", "2026-06-12T16:02:00Z", "drawdown", "Dany -1.4%", "Risk flag raised."),
        ],
    )
    con.executemany(
        "insert into ml_promotion_recommendations (id,candidate_name,recommendation,rationale,created_at) values (?,?,?,?,?)",
        [
            (1, "Antimatter Short", "hold", "short-side scarcity", "2026-06-12T16:00:00Z"),
            (2, "Cartographer v3", "promote", "auc above gate", "2026-06-12T16:01:00Z"),
        ],
    )
    con.commit()
    con.close()


def test_atl_organ_satisfies_contract_and_is_live(tmp_path):
    db = tmp_path / "league.sqlite"
    _fixture_db(str(db))
    organ = ATLOrgan.from_db("league", str(db))
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.LIVE


def test_polls_events_and_feeds_evidence(tmp_path):
    db = tmp_path / "league.sqlite"
    _fixture_db(str(db))
    ev = EvidenceService()
    eng = FederationEngine(clock=Clock(), evidence=ev)
    eng.register(ATLOrgan.from_db("league", str(db)))
    eng.run(steps=3)

    # 3 events land on the bus on the first tick (then the DB is quiet).
    assert len([c for c in eng.bus if c.source == "league"]) == 3
    titles = {c.title for c in eng.bus}
    assert "Timmy promoted" in titles
    # 2 promotion recommendations become open gradable claims.
    assert ev.open_count == 2
    assert "league" in ev.by_source()


def test_cursor_advances_no_duplicates(tmp_path):
    db = tmp_path / "league.sqlite"
    _fixture_db(str(db))
    organ = ATLOrgan.from_db("league", str(db))
    first = organ.advance(1)
    assert len(first.events) == 3
    second = organ.advance(2)  # nothing new since cursor
    assert second.events == []


def test_record_then_replay_byte_identical(tmp_path):
    db = tmp_path / "league.sqlite"
    _fixture_db(str(db))
    rec = FederationEngine(clock=Clock())
    rec.register(ATLOrgan.from_db("league", str(db)))
    recorded = [c.as_dict() for c in rec.run(steps=3)]
    tape = next(iter(rec.organs.values())).tape

    rep = FederationEngine(clock=Clock())
    rep.register(ATLOrgan.replay_from("league", tape))
    replayed = [c.as_dict() for c in rep.run(steps=3)]
    assert replayed == recorded


@pytest.mark.skipif(not os.path.exists(REAL_DB), reason="real wanda league.sqlite not on this machine")
def test_smoke_against_real_league_db():
    ev = EvidenceService()
    eng = FederationEngine(clock=Clock(), evidence=ev)
    eng.register(ATLOrgan.from_db("league", REAL_DB))
    eng.run(steps=1)
    assert eng.bus, "the real league should surface events"
    # real DB has promotion recommendations -> open claims
    assert ev.open_count > 0
