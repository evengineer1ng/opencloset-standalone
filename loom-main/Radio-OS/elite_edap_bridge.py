from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_GLOBAL_SHOPPING_LIST = {
    "BuyCommodities": {},
    "UpdateCommodityCount": False,
    "Skip": False,
    "Completed": False,
}


def _deep_copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)


def _default_leg(index: int, leg: dict[str, Any]) -> dict[str, Any]:
    system_name = str(leg.get("system_name") or leg.get("system") or "").strip()
    station_name = str(leg.get("station_name") or leg.get("station") or "").strip()
    if not system_name and not station_name:
        raise ValueError(f"Route leg {index} must define at least a system or station")

    waypoint = {
        "SystemName": system_name,
        "StationName": station_name,
        "GalaxyBookmarkType": str(leg.get("galaxy_bookmark_type") or ""),
        "GalaxyBookmarkNumber": int(leg.get("galaxy_bookmark_number", -1)),
        "SystemBookmarkType": str(leg.get("system_bookmark_type") or ""),
        "SystemBookmarkNumber": int(leg.get("system_bookmark_number", -1)),
        "SellCommodities": _deep_copy_dict(leg.get("sell_commodities") or {}),
        "BuyCommodities": _deep_copy_dict(leg.get("buy_commodities") or {}),
        "UpdateCommodityCount": bool(leg.get("update_commodity_count", True)),
        "FleetCarrierTransfer": bool(leg.get("fleet_carrier_transfer", False)),
        "Skip": bool(leg.get("skip", False)),
        "Completed": bool(leg.get("completed", False)),
    }
    return waypoint


def build_edap_waypoints(route_plan: dict[str, Any]) -> dict[str, Any]:
    legs = route_plan.get("legs") or route_plan.get("route") or []
    if not isinstance(legs, list) or not legs:
        raise ValueError("Route plan must include a non-empty 'legs' list")

    global_buy = route_plan.get("global_buy_commodities") or {}
    global_update = bool(route_plan.get("global_update_commodity_count", False))
    waypoints: dict[str, Any] = {
        "GlobalShoppingList": {
            "BuyCommodities": _deep_copy_dict(global_buy),
            "UpdateCommodityCount": global_update,
            "Skip": False,
            "Completed": False,
        }
    }

    for index, leg in enumerate(legs, start=1):
        if not isinstance(leg, dict):
            raise ValueError(f"Route leg {index} must be an object")
        key = str(leg.get("key") or index)
        waypoints[key] = _default_leg(index, leg)

    if route_plan.get("repeat", True):
        waypoints["rep"] = {
            "SystemName": "REPEAT",
            "StationName": "",
            "GalaxyBookmarkType": "",
            "GalaxyBookmarkNumber": -1,
            "SystemBookmarkType": "",
            "SystemBookmarkNumber": -1,
            "SellCommodities": {},
            "BuyCommodities": {},
            "UpdateCommodityCount": False,
            "FleetCarrierTransfer": False,
            "Skip": False,
            "Completed": False,
        }

    return waypoints


def compute_route_signature(route_plan: dict[str, Any]) -> str:
    payload = json.dumps(route_plan, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def read_json_file(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_file(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)
    return output_path


def summarize_route_progress(waypoints: dict[str, Any]) -> dict[str, Any]:
    legs: list[dict[str, Any]] = []
    next_leg: dict[str, Any] | None = None

    for key, leg in waypoints.items():
        if key == "GlobalShoppingList":
            continue
        if str(leg.get("SystemName", "")).upper() == "REPEAT":
            continue

        current_leg = {
            "key": key,
            "system_name": str(leg.get("SystemName") or ""),
            "station_name": str(leg.get("StationName") or ""),
            "completed": bool(leg.get("Completed", False)),
            "skip": bool(leg.get("Skip", False)),
            "buy_commodities": _deep_copy_dict(leg.get("BuyCommodities") or {}),
            "sell_commodities": _deep_copy_dict(leg.get("SellCommodities") or {}),
        }
        legs.append(current_leg)
        if next_leg is None and not current_leg["completed"] and not current_leg["skip"]:
            next_leg = current_leg

    completed = sum(1 for leg in legs if leg["completed"])
    actionable = sum(1 for leg in legs if not leg["skip"])
    progress_pct = 100.0 if actionable == 0 else round((completed / actionable) * 100.0, 2)

    return {
        "completed": completed,
        "actionable": actionable,
        "progress_pct": progress_pct,
        "next_leg": next_leg,
        "legs": legs,
        "repeat_enabled": any(
            str(leg.get("SystemName", "")).upper() == "REPEAT" and not bool(leg.get("Skip", False))
            for leg in waypoints.values()
            if isinstance(leg, dict)
        ),
    }


@dataclass
class EDAPClientHandles:
    client: Any
    load_waypoint_action: type[Any]
    start_waypoint_action: type[Any]
    stop_all_assists_action: type[Any]
    generic_action: type[Any]


class EDAPBridge:
    def __init__(self, edap_root: str | Path, actions_port: int = 15570, events_port: int = 15571):
        self.edap_root = Path(edap_root)
        self.actions_port = int(actions_port)
        self.events_port = int(events_port)

    def _load_client(self) -> EDAPClientHandles:
        if not self.edap_root.exists():
            raise FileNotFoundError(f"EDAP root not found: {self.edap_root}")

        root_str = str(self.edap_root)
        inserted = False
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
            inserted = True

        try:
            from EDAP_EDMesg_Interface import (  # type: ignore
                GenericAction,
                LoadWaypointFileAction,
                StartWaypointAssistAction,
                StopAllAssistsAction,
                create_edap_client,
            )

            client = create_edap_client(self.actions_port, self.events_port)
            return EDAPClientHandles(
                client=client,
                load_waypoint_action=LoadWaypointFileAction,
                start_waypoint_action=StartWaypointAssistAction,
                stop_all_assists_action=StopAllAssistsAction,
                generic_action=GenericAction,
            )
        finally:
            if inserted:
                try:
                    sys.path.remove(root_str)
                except ValueError:
                    pass

    def push_waypoints(
        self,
        waypoint_path: str | Path,
        *,
        start_assist: bool = False,
        stop_first: bool = False,
        write_tce_shopping_list: bool = False,
    ) -> None:
        handles = self._load_client()
        try:
            if stop_first:
                handles.client.publish(handles.stop_all_assists_action())
            handles.client.publish(handles.load_waypoint_action(filepath=str(Path(waypoint_path))))
            if write_tce_shopping_list:
                handles.client.publish(handles.generic_action(name="WriteTCEShoppingList"))
            if start_assist:
                handles.client.publish(handles.start_waypoint_action())
        finally:
            handles.client.close()

    def start_waypoint_assist(self) -> None:
        handles = self._load_client()
        try:
            handles.client.publish(handles.start_waypoint_action())
        finally:
            handles.client.close()

    def stop_all_assists(self) -> None:
        handles = self._load_client()
        try:
            handles.client.publish(handles.stop_all_assists_action())
        finally:
            handles.client.close()

    def write_tce_shopping_list(self) -> None:
        handles = self._load_client()
        try:
            handles.client.publish(handles.generic_action(name="WriteTCEShoppingList"))
        finally:
            handles.client.close()