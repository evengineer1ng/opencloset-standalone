"""The responsible telemetry handshake: advertise endpoints before consuming; the Club withholds
sensitive feeds without explicit, remembered consent; the station still runs (degraded, not errored).

Also: a telemetry-only `.oradio` (me.oradio) is valid — "me" emerges from the telemetry, no world.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Club, open_oradio  # noqa: E402
from oradio_engine.descriptor import OradioDescriptor  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")

ME = {
    "oradio": "me",
    "telemetry": [
        {"source": "pc_telemetry", "name": "pc"},
        {"source": "ring_telemetry", "name": "ring"},
    ],
    "lens": "identity",
    "club": ["voices"],
}


def test_telemetry_only_oradio_is_valid():
    desc = OradioDescriptor.from_dict(ME)
    assert desc.worlds == [] and len(desc.telemetry) == 2, "me.oradio has no world; it emerges from telemetry"


def test_manifest_advertises_before_consuming(tmp_path):
    club = Club(store_path=str(tmp_path / "club.json"))
    manifest = club.telemetry_manifest(OradioDescriptor.from_dict(ME))
    assert {r.name for r in manifest} == {"pc", "ring"}
    assert all(r.sensitive for r in manifest), "personal endpoints are flagged sensitive"
    assert all(not r.consented for r in manifest), "nothing consented yet"
    assert any("COLMI R02" in r.reads for r in manifest), "it says exactly what it would read"


def test_sensitive_endpoints_withheld_without_consent_but_station_still_runs(tmp_path):
    res = open_oradio(ME, club=Club(store_path=str(tmp_path / "club.json")))
    assert res.ok, "denying telemetry does NOT break the station"
    assert {r.name for r in res.withheld} == {"pc", "ring"}, "both sensitive feeds withheld"
    # the federation built with NO sensitive sources — it touched nothing un-consented
    assert res.engine.organs == {}
    res.engine.run(steps=3)  # runs cleanly, just quiet


def test_consent_is_asked_once_then_remembered(tmp_path):
    club = Club(store_path=str(tmp_path / "club.json"))
    # explicit "yes, go ahead" → grants + remembers consent
    first = open_oradio(ME, club=club, allow_sensitive=True)
    assert first.withheld == [] and set(first.engine.organs) == {"pc", "ring"}
    first.engine.run(steps=3)
    assert any(c.source in ("pc", "ring") for c in first.engine.bus), "consented feeds now flow"

    # a SECOND open (no allow_sensitive) — consent persisted, no re-ask, no withhold
    second = open_oradio(ME, club=club)
    assert second.withheld == [], "configure once, reuse forever — consent is remembered"
    assert set(second.engine.organs) == {"pc", "ring"}


def test_benign_sources_never_need_consent(tmp_path):
    res = open_oradio({
        "oradio": "house",
        "world": {"organ": "neikos", "name": "isle", "seed": 42},
        "telemetry": [{"source": "simulated_spatial_array", "name": "array", "nodes": ["a", "b"]}],
    }, club=Club(store_path=str(tmp_path / "club.json")))
    assert res.withheld == [], "a simulated/benign source is never gated"
    assert "array" in res.engine.organs


def test_revoke_consent(tmp_path):
    club = Club(store_path=str(tmp_path / "club.json"))
    club.grant_consent("ring_telemetry")
    assert club.has_consent("ring_telemetry")
    club.revoke_consent("ring_telemetry")
    assert not club.has_consent("ring_telemetry"), "consent is revocable"


def test_me_example_file_parses_and_gates():
    pytest.importorskip("yaml")
    path = os.path.join(EXAMPLES, "me.oradio")
    res = open_oradio(path, club=Club(store_path=os.path.join(os.path.dirname(path), "_t_club.json")))
    try:
        assert res.name == "me"
        assert {r.name for r in res.withheld} == {"pc", "ring"}, "the flagship gates its personal feeds"
    finally:
        cj = os.path.join(os.path.dirname(path), "_t_club.json")
        if os.path.exists(cj):
            os.remove(cj)
