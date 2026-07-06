from __future__ import annotations

import sys
from pathlib import Path

import yaml


RADIO_OS_DIR = Path(__file__).resolve().parents[1] / "Radio-OS"
if str(RADIO_OS_DIR) not in sys.path:
    sys.path.insert(0, str(RADIO_OS_DIR))

from ribbon_shell_models import assemble_catalog, discover_oradio_items, discover_station_items, filter_items
from ribbon_shell_theme import RibbonStateMachine


def test_discover_station_and_oradio_items(tmp_path: Path):
    radio_root = tmp_path / "Radio-OS"
    station_dir = radio_root / "stations" / "DemoFM"
    station_dir.mkdir(parents=True)
    (station_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"station": {"name": "Demo FM", "host": "Night host", "category": "Worlds"}}),
        encoding="utf-8",
    )
    exports_dir = radio_root / "exports"
    exports_dir.mkdir(parents=True)
    (exports_dir / "DemoFM.oradio").write_text("stub", encoding="utf-8")

    stations = discover_station_items(radio_root)
    artifacts = discover_oradio_items(radio_root)

    assert stations[0].title == "Demo FM"
    assert stations[0].subtitle == "Night host"
    assert artifacts[0].kind == "oradio"


def test_assemble_catalog_includes_tools(tmp_path: Path):
    radio_root = tmp_path / "Radio-OS"
    (radio_root / "stations").mkdir(parents=True)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "oc.py").write_text("print('ok')", encoding="utf-8")
    catalog = assemble_catalog(workspace_root, radio_root)
    ids = {item.item_id for item in catalog}
    assert "tool:simulator_manager" in ids
    assert "tool:opencloset" in ids


def test_filter_items_matches_bucket_and_query(tmp_path: Path):
    radio_root = tmp_path / "Radio-OS"
    station_dir = radio_root / "stations" / "DemoFM"
    station_dir.mkdir(parents=True)
    (station_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"station": {"name": "Demo FM", "host": "Night host", "category": "Worlds"}}),
        encoding="utf-8",
    )
    items = discover_station_items(radio_root)
    assert len(filter_items(items, "All", "demo")) == 1
    assert len(filter_items(items, "Station", "night")) == 1
    assert len(filter_items(items, "Oradio", "demo")) == 0


def test_ribbon_state_machine_dims_after_inactivity():
    machine = RibbonStateMachine()
    machine.boot_started -= 2.0
    machine.tick()
    assert machine.phase == "ACTIVE"
    machine.last_activity -= 8.0
    machine.tick()
    assert machine.phase == "DIM"
    machine.note_launch()
    assert machine.phase == "LAUNCH"
