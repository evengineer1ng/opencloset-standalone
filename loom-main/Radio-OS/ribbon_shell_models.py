from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml


@dataclass(frozen=True)
class LaunchItem:
    item_id: str
    title: str
    subtitle: str
    kind: str
    target_path: str
    category: str
    meta: Dict[str, Any]


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def discover_station_items(radio_root: Path) -> List[LaunchItem]:
    stations_dir = radio_root / "stations"
    items: List[LaunchItem] = []
    if not stations_dir.exists():
        return items
    for station_dir in sorted(p for p in stations_dir.iterdir() if p.is_dir()):
        manifest_path = station_dir / "manifest.yaml"
        if not manifest_path.exists():
            continue
        manifest = _read_yaml(manifest_path)
        station = manifest.get("station") if isinstance(manifest.get("station"), dict) else {}
        title = str(station.get("name") or station_dir.name)
        host = str(station.get("host") or "").strip()
        category = str(station.get("category") or "Custom").strip() or "Custom"
        subtitle = host or category
        items.append(
            LaunchItem(
                item_id=f"station:{station_dir.name}",
                title=title,
                subtitle=subtitle,
                kind="station",
                target_path=str(station_dir),
                category=category,
                meta={"manifest": manifest, "station_id": station_dir.name},
            )
        )
    return items


def discover_oradio_items(radio_root: Path) -> List[LaunchItem]:
    exports_dir = radio_root / "exports"
    items: List[LaunchItem] = []
    if not exports_dir.exists():
        return items
    for artifact in sorted(exports_dir.glob("*.oradio")):
        title = artifact.stem
        items.append(
            LaunchItem(
                item_id=f"oradio:{artifact.stem}",
                title=title,
                subtitle=".oradio artifact",
                kind="oradio",
                target_path=str(artifact),
                category="Artifacts",
                meta={},
            )
        )
    return items


def discover_tool_items(workspace_root: Path, radio_root: Path) -> List[LaunchItem]:
    tools = [
        LaunchItem(
            item_id="tool:simulator_manager",
            title="Simulator",
            subtitle="Open the station simulator",
            kind="tool",
            target_path=str(radio_root / "shell_bookmark.py"),
            category="Tools",
            meta={"args": ["--desktop"]},
        ),
        LaunchItem(
            item_id="tool:loom_studio",
            title="Loom Studio",
            subtitle="Author descriptor-style .oradio files",
            kind="tool",
            target_path=str(radio_root / "loom_studio.py"),
            category="Tools",
            meta={"args": []},
        ),
        LaunchItem(
            item_id="tool:audio_cli",
            title="Audio CLI",
            subtitle="Shell-level voice surface",
            kind="tool",
            target_path=str(radio_root / "audio_cli.py"),
            category="Tools",
            meta={"args": []},
        ),
    ]
    oc_path = workspace_root / "oc.py"
    if oc_path.exists():
        tools.append(
            LaunchItem(
                item_id="tool:opencloset",
                title="OpenCloset",
                subtitle="Harness entry point",
                kind="tool",
                target_path=str(oc_path),
                category="Tools",
                meta={"args": []},
            )
        )
    return tools


def assemble_catalog(workspace_root: Path, radio_root: Path) -> List[LaunchItem]:
    items = []
    items.extend(discover_station_items(radio_root))
    items.extend(discover_oradio_items(radio_root))
    items.extend(discover_tool_items(workspace_root, radio_root))
    return items


def filter_items(items: Iterable[LaunchItem], bucket: str, query: str) -> List[LaunchItem]:
    needle = query.strip().lower()
    out: List[LaunchItem] = []
    for item in items:
        if bucket != "All" and item.kind.capitalize() != bucket and item.category != bucket:
            continue
        if needle:
            hay = " ".join([item.title, item.subtitle, item.kind, item.category]).lower()
            if needle not in hay:
                continue
        out.append(item)
    return out
