from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from elite_edap_bridge import (
    EDAPBridge,
    build_edap_waypoints,
    compute_route_signature,
    read_json_file,
    summarize_route_progress,
    write_json_file,
)


PLUGIN_NAME = "elite_trade_crew"
PLUGIN_DESC = "Elite Dangerous trade-loop crew that writes EDAP waypoint plans and narrates progress."
IS_FEED = True

FEED_DEFAULTS = {
    "enabled": False,
    "route_file": "./trade_loop.sample.json",
    "waypoint_output": "./runtime_edap_waypoints.json",
    "poll_sec": 15,
    "priority": 88,
    "status_cooldown_sec": 120,
    "auto_sync": True,
    "push_to_edap": True,
    "auto_start": False,
    "stop_before_sync": False,
    "write_tce_shopping_list": False,
    "edap_root": "",
    "edap_actions_port": 15570,
    "edap_events_port": 15571,
    "crew_name": "Piper Crew",
    "ship_name": "Piper",
    "commander_name": "Commander",
    "adventure_style": "buy low, sell high, and keep the route moving",
}

RADIO_OS_ROOT = Path(__file__).resolve().parents[1]


def now_ts() -> int:
    return int(time.time())


def _sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _runtime_log(runtime: dict[str, Any] | None, message: str) -> None:
    if isinstance(runtime, dict):
        log_fn = runtime.get("log")
        if callable(log_fn):
            try:
                log_fn("EDAP", message)
                return
            except TypeError:
                log_fn(message)
                return
    print(f"[elite_trade_crew] {message}")


def _resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def parse_commodity_map(text: str) -> dict[str, int]:
    items: dict[str, int] = {}
    for raw_part in (text or "").replace("\n", ",").replace(";", ",").split(","):
        part = raw_part.strip()
        if not part:
            continue

        if ":" in part:
            name, qty_text = part.rsplit(":", 1)
        elif "=" in part:
            name, qty_text = part.rsplit("=", 1)
        else:
            name, qty_text = part, "0"

        name = name.strip()
        qty_text = qty_text.strip() or "0"
        if not name:
            continue

        items[name] = int(float(qty_text))
    return items


def format_commodity_map(items: dict[str, Any]) -> str:
    if not items:
        return ""
    return ", ".join(f"{name}:{qty}" for name, qty in items.items())


def choose_preferred_ollama_gpu(gpus: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not gpus:
        return None

    for gpu in gpus:
        name = str(gpu.get("name") or "").lower()
        if "1080" in name and "ti" in name:
            return gpu

    if len(gpus) > 1:
        return gpus[1]

    return gpus[0]


def detect_nvidia_gpus() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []

    gpus: list[dict[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) < 4:
            continue
        gpus.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "uuid": parts[2],
                "memory_total": parts[3],
            }
        )
    return gpus


def default_edap_root() -> Path | None:
    candidates = [
        Path.home() / "Downloads" / "EDAPGui-1.9.1" / "EDAPGui-1.9.1",
        Path.home() / "Downloads" / "EDAPGui",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def run_ollama_gpu_pin(force_restart: bool = True) -> tuple[bool, str]:
    script_path = RADIO_OS_ROOT / "start-ollama-1080ti.ps1"
    if not script_path.exists():
        return False, f"Missing launcher script: {script_path}"

    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if force_restart:
        command.append("-ForceRestart")

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = (result.stdout or result.stderr or "Ollama started").strip()
        return True, output
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or exc.stderr or str(exc)).strip()
        return False, output


def _manifest_path(base_dir: Path) -> Path:
    return base_dir / "manifest.yaml"


def _default_route_plan() -> dict[str, Any]:
    return {
        "name": "Piper Crew Trading Loop",
        "repeat": True,
        "global_buy_commodities": {},
        "global_update_commodity_count": False,
        "legs": [
            {
                "key": "buy-leg",
                "system_name": "",
                "station_name": "",
                "buy_commodities": {},
                "sell_commodities": {},
                "system_bookmark_type": "",
                "system_bookmark_number": -1,
                "update_commodity_count": True,
            }
        ],
    }


def _load_manifest(base_dir: Path) -> dict[str, Any]:
    path = _manifest_path(base_dir)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _save_manifest(base_dir: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(base_dir)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, default_flow_style=False, sort_keys=False)


def _load_feed_cfg(base_dir: Path) -> dict[str, Any]:
    manifest = _load_manifest(base_dir)
    cfg = copy.deepcopy(FEED_DEFAULTS)
    cfg.update((((manifest.get("feeds") or {}).get("elite_trade_crew")) or {}))
    return cfg


def _save_feed_cfg(base_dir: Path, feed_cfg: dict[str, Any]) -> None:
    manifest = _load_manifest(base_dir)
    feeds = manifest.setdefault("feeds", {})
    feeds["elite_trade_crew"] = feed_cfg
    _save_manifest(base_dir, manifest)


def _load_route_plan(route_path: Path) -> dict[str, Any]:
    if route_path.exists():
        return read_json_file(route_path)
    return _default_route_plan()


def _describe_leg(leg: dict[str, Any] | None) -> str:
    if not leg:
        return "Route complete. Awaiting the next profitable loop."

    system_name = leg.get("system_name") or "current system"
    station_name = leg.get("station_name") or "local market"
    buys = leg.get("buy_commodities") or {}
    sells = leg.get("sell_commodities") or {}

    actions: list[str] = []
    if sells:
        actions.append(
            "sell " + ", ".join(f"{name} x{qty}" for name, qty in sells.items())
        )
    if buys:
        actions.append(
            "buy " + ", ".join(f"{name} x{qty}" for name, qty in buys.items())
        )

    action_text = "; then ".join(actions) if actions else "dock and reposition for the next leg"
    return f"Next leg: {system_name} / {station_name}; {action_text}."


def _emit_status(runtime: dict[str, Any] | None, cfg: dict[str, Any], summary: dict[str, Any]) -> None:
    if not isinstance(runtime, dict):
        return

    emit_candidate = runtime.get("emit_candidate")
    StationEvent = runtime.get("StationEvent")
    event_q = runtime.get("event_q")
    ui_widget_update = runtime.get("ui_widget_update")

    crew_name = str(cfg.get("crew_name") or "Piper Crew")
    ship_name = str(cfg.get("ship_name") or "Piper")
    adventure_style = str(cfg.get("adventure_style") or "steady trade loops")
    next_leg = summary.get("next_leg")
    title = f"{crew_name} trade update"
    body = (
        f"{ship_name} is {summary['progress_pct']}% through the active route. "
        f"{_describe_leg(next_leg)} Mission profile: {adventure_style}."
    )

    if callable(emit_candidate):
        emit_candidate(
            {
                "id": _sha1(json.dumps(summary, sort_keys=True)),
                "post_id": f"elite-trade-{summary['completed']}-{summary['actionable']}",
                "source": "elite_trade_crew",
                "event_type": "trade_loop_status",
                "title": title,
                "body": body,
                "comments": [],
                "heur": float(cfg.get("priority", 88)),
                "source_site": "elite-dangerous",
            }
        )

    if StationEvent and event_q is not None:
        try:
            event_q.put(
                StationEvent(
                    source="elite_trade_crew",
                    type="status",
                    ts=now_ts(),
                    priority=float(cfg.get("priority", 88)),
                    payload={
                        "title": title,
                        "body": body,
                        "progress_pct": summary["progress_pct"],
                    },
                )
            )
        except Exception:
            pass

    if callable(ui_widget_update):
        ui_widget_update(
            "elite_trade_crew",
            {
                "title": title,
                "body": body,
                "summary": summary,
                "config": {
                    "route_file": str(cfg.get("route_file") or ""),
                    "waypoint_output": str(cfg.get("waypoint_output") or ""),
                    "edap_root": str(cfg.get("edap_root") or ""),
                    "auto_sync": bool(cfg.get("auto_sync", True)),
                    "auto_start": bool(cfg.get("auto_start", False)),
                },
            },
        )


def register_widgets(registry, runtime):
    def factory(parent, rt):
        return EliteTradeCrewWidget(parent, rt)

    registry.register(
        "elite_trade_crew",
        factory,
        title="Elite Trade Crew",
        default_panel="right",
    )


class EliteTradeCrewWidget:
    def __init__(self, parent, runtime):
        self.tk = runtime["tk"]
        self.runtime = runtime
        self.base_dir = Path.cwd()
        self.root = self.tk.Frame(parent, bg="#0e0e0e")
        self.legs: list[dict[str, Any]] = []
        self.selected_index: int | None = None

        self.status_var = self.tk.StringVar(value="Crew console idle")
        self.detail_var = self.tk.StringVar(value="Edit a route, save it, then sync it to EDAP.")
        self.route_name_var = self.tk.StringVar(value="Piper Crew Trading Loop")
        self.route_file_var = self.tk.StringVar(value=FEED_DEFAULTS["route_file"])
        self.waypoint_output_var = self.tk.StringVar(value=FEED_DEFAULTS["waypoint_output"])
        self.edap_root_var = self.tk.StringVar(value="")
        self.global_buy_var = self.tk.StringVar(value="")
        self.repeat_var = self.tk.BooleanVar(value=True)
        self.auto_sync_var = self.tk.BooleanVar(value=True)
        self.push_to_edap_var = self.tk.BooleanVar(value=True)
        self.auto_start_var = self.tk.BooleanVar(value=False)
        self.stop_before_sync_var = self.tk.BooleanVar(value=False)
        self.write_tce_var = self.tk.BooleanVar(value=False)

        self.leg_key_var = self.tk.StringVar(value="")
        self.system_var = self.tk.StringVar(value="")
        self.station_var = self.tk.StringVar(value="")
        self.buy_var = self.tk.StringVar(value="")
        self.sell_var = self.tk.StringVar(value="")
        self.bookmark_type_var = self.tk.StringVar(value="")
        self.bookmark_number_var = self.tk.StringVar(value="-1")
        self.update_count_var = self.tk.BooleanVar(value=True)
        self.skip_var = self.tk.BooleanVar(value=False)

        self._build_ui()
        self.load_from_disk()
        self.root.after(300, self._maybe_launch_setup_wizard)

    def _build_ui(self) -> None:
        bg = "#0e0e0e"
        card = "#161616"
        border = "#2a2a2a"
        text = "#e8e8e8"
        muted = "#9a9a9a"
        accent = "#4cc9f0"

        def section(parent, title):
            frame = self.tk.Frame(parent, bg=card, highlightbackground=border, highlightthickness=1)
            self.tk.Label(frame, text=title, fg=accent, bg=card, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 4))
            return frame

        header = section(self.root, "PIPER CREW")
        header.pack(fill="x", padx=10, pady=(10, 8))
        self.tk.Label(header, textvariable=self.status_var, fg=text, bg=card, font=("Segoe UI", 12, "bold"), wraplength=540, justify="left").pack(anchor="w", padx=10)
        self.tk.Label(header, textvariable=self.detail_var, fg=muted, bg=card, font=("Segoe UI", 9), wraplength=540, justify="left").pack(anchor="w", padx=10, pady=(2, 10))

        controls = self.tk.Frame(self.root, bg=bg)
        controls.pack(fill="x", padx=10, pady=(0, 8))
        for label, command in [
            ("Setup Wizard", self.launch_setup_wizard),
            ("Reload", self.load_from_disk),
            ("Save Plan", self.save_route_plan),
            ("Sync To EDAP", self.sync_to_edap),
            ("Start Route", self.start_route),
            ("Stop Assists", self.stop_assists),
        ]:
            self.tk.Button(
                controls,
                text=label,
                command=command,
                bg="#1d3a46",
                fg="#e8f7fb",
                relief="flat",
                padx=10,
                pady=6,
            ).pack(side="left", padx=(0, 8))

        settings = section(self.root, "Crew Settings")
        settings.pack(fill="x", padx=10, pady=(0, 8))
        self._labeled_entry(settings, "Route Name", self.route_name_var)
        self._labeled_entry(settings, "EDAP Folder", self.edap_root_var)
        self._labeled_entry(settings, "Route File", self.route_file_var)
        self._labeled_entry(settings, "Waypoint Output", self.waypoint_output_var)
        self._labeled_entry(settings, "Global Buy List", self.global_buy_var, hint="Format: Gold:64, Silver:12")
        toggles = self.tk.Frame(settings, bg=card)
        toggles.pack(fill="x", padx=10, pady=(4, 10))
        for label, var in [
            ("Repeat loop", self.repeat_var),
            ("Auto sync on file change", self.auto_sync_var),
            ("Auto push to EDAP", self.push_to_edap_var),
            ("Auto start after sync", self.auto_start_var),
            ("Stop current assist before sync", self.stop_before_sync_var),
            ("Ask EDAP to refresh TCE shopping list", self.write_tce_var),
        ]:
            self.tk.Checkbutton(
                toggles,
                text=label,
                variable=var,
                bg=card,
                fg=text,
                selectcolor="#101010",
                activebackground=card,
                activeforeground=text,
                anchor="w",
            ).pack(fill="x", anchor="w")

        route = section(self.root, "Trade Loop")
        route.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        route_body = self.tk.Frame(route, bg=card)
        route_body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = self.tk.Frame(route_body, bg=card)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.leg_list = self.tk.Listbox(
            left,
            width=34,
            height=14,
            bg="#101010",
            fg=text,
            selectbackground="#25556a",
            activestyle="none",
        )
        self.leg_list.pack(fill="y", expand=False)
        self.leg_list.bind("<<ListboxSelect>>", self._on_leg_select)

        leg_buttons = self.tk.Frame(left, bg=card)
        leg_buttons.pack(fill="x", pady=(8, 0))
        for label, command in [
            ("New Leg", self.new_leg),
            ("Save Leg", self.save_leg),
            ("Remove", self.remove_leg),
            ("Up", self.move_leg_up),
            ("Down", self.move_leg_down),
        ]:
            self.tk.Button(leg_buttons, text=label, command=command, bg="#202020", fg=text, relief="flat").pack(side="left", padx=(0, 6))

        editor = self.tk.Frame(route_body, bg=card)
        editor.pack(side="left", fill="both", expand=True)
        self._labeled_entry(editor, "Leg Key", self.leg_key_var)
        self._labeled_entry(editor, "System", self.system_var)
        self._labeled_entry(editor, "Station", self.station_var)
        self._labeled_entry(editor, "Buy Commodities", self.buy_var, hint="Gold:64, Silver:12")
        self._labeled_entry(editor, "Sell Commodities", self.sell_var, hint="Gold:64, Silver:12")
        self._labeled_entry(editor, "Bookmark Type", self.bookmark_type_var, hint="Optional: Fav, Sys, Sta, Bod")
        self._labeled_entry(editor, "Bookmark Number", self.bookmark_number_var, hint="Use -1 to disable")
        flags = self.tk.Frame(editor, bg=card)
        flags.pack(fill="x", padx=0, pady=(4, 0))
        self.tk.Checkbutton(flags, text="Update commodity counts", variable=self.update_count_var, bg=card, fg=text, selectcolor="#101010", activebackground=card, activeforeground=text).pack(anchor="w")
        self.tk.Checkbutton(flags, text="Skip this leg", variable=self.skip_var, bg=card, fg=text, selectcolor="#101010", activebackground=card, activeforeground=text).pack(anchor="w")

    def _labeled_entry(self, parent, label, var, hint: str | None = None) -> None:
        wrap = self.tk.Frame(parent, bg=parent.cget("bg"))
        wrap.pack(fill="x", pady=(0, 6))
        self.tk.Label(wrap, text=label, fg="#d5d5d5", bg=parent.cget("bg"), font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.tk.Entry(wrap, textvariable=var, bg="#101010", fg="#e8e8e8", insertbackground="#e8e8e8", relief="flat").pack(fill="x", pady=(2, 0))
        if hint:
            self.tk.Label(wrap, text=hint, fg="#8c8c8c", bg=parent.cget("bg"), font=("Segoe UI", 8)).pack(anchor="w", pady=(1, 0))

    def _feed_cfg(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "route_file": self.route_file_var.get().strip() or FEED_DEFAULTS["route_file"],
            "waypoint_output": self.waypoint_output_var.get().strip() or FEED_DEFAULTS["waypoint_output"],
            "poll_sec": FEED_DEFAULTS["poll_sec"],
            "priority": FEED_DEFAULTS["priority"],
            "status_cooldown_sec": FEED_DEFAULTS["status_cooldown_sec"],
            "auto_sync": bool(self.auto_sync_var.get()),
            "push_to_edap": bool(self.push_to_edap_var.get()),
            "auto_start": bool(self.auto_start_var.get()),
            "stop_before_sync": bool(self.stop_before_sync_var.get()),
            "write_tce_shopping_list": bool(self.write_tce_var.get()),
            "edap_root": self.edap_root_var.get().strip(),
            "edap_actions_port": FEED_DEFAULTS["edap_actions_port"],
            "edap_events_port": FEED_DEFAULTS["edap_events_port"],
            "crew_name": "Piper Crew",
            "ship_name": "Piper",
            "commander_name": "Evan",
            "adventure_style": "buy low, sell high, repeat, refuel, repair, and keep the adventure moving",
        }

    def _memory(self) -> dict[str, Any]:
        getter = self.runtime.get("get_memory")
        if callable(getter):
            try:
                return getter() or {}
            except Exception:
                return {}
        return {}

    def _save_memory(self, mem: dict[str, Any]) -> None:
        saver = self.runtime.get("save_memory")
        if callable(saver):
            saver(mem)

    def _wizard_needs_attention(self) -> bool:
        mem = self._memory()
        if not self.edap_root_var.get().strip():
            return True
        if not mem.get("elite_trade_crew_wizard_completed"):
            return True
        return False

    def _maybe_launch_setup_wizard(self) -> None:
        if self._wizard_needs_attention():
            self.launch_setup_wizard()

    def _route_path(self) -> Path:
        return _resolve_path(self.route_file_var.get().strip() or FEED_DEFAULTS["route_file"], self.base_dir)

    def _waypoint_output_path(self) -> Path:
        return _resolve_path(self.waypoint_output_var.get().strip() or FEED_DEFAULTS["waypoint_output"], self.base_dir)

    def _current_route_plan(self) -> dict[str, Any]:
        self._commit_editor_if_needed()
        return {
            "name": self.route_name_var.get().strip() or "Piper Crew Trading Loop",
            "repeat": bool(self.repeat_var.get()),
            "global_buy_commodities": parse_commodity_map(self.global_buy_var.get()),
            "global_update_commodity_count": False,
            "legs": copy.deepcopy(self.legs),
        }

    def _set_route_plan(self, route_plan: dict[str, Any]) -> None:
        self.route_name_var.set(str(route_plan.get("name") or "Piper Crew Trading Loop"))
        self.repeat_var.set(bool(route_plan.get("repeat", True)))
        self.global_buy_var.set(format_commodity_map(route_plan.get("global_buy_commodities") or {}))
        self.legs = copy.deepcopy(route_plan.get("legs") or [])
        self._refresh_leg_list()
        if self.legs:
            self._select_leg(0)
        else:
            self.new_leg()

    def _refresh_leg_list(self) -> None:
        self.leg_list.delete(0, self.tk.END)
        for idx, leg in enumerate(self.legs, start=1):
            system_name = leg.get("system_name") or "(current system)"
            station_name = leg.get("station_name") or "(any station)"
            marker = "SKIP" if leg.get("skip") else "RUN"
            self.leg_list.insert(self.tk.END, f"{idx}. {system_name} -> {station_name} [{marker}]")

    def _select_leg(self, index: int) -> None:
        if not (0 <= index < len(self.legs)):
            return
        self.selected_index = index
        leg = self.legs[index]
        self.leg_list.selection_clear(0, self.tk.END)
        self.leg_list.selection_set(index)
        self.leg_list.activate(index)
        self.leg_key_var.set(str(leg.get("key") or f"leg-{index + 1}"))
        self.system_var.set(str(leg.get("system_name") or ""))
        self.station_var.set(str(leg.get("station_name") or ""))
        self.buy_var.set(format_commodity_map(leg.get("buy_commodities") or {}))
        self.sell_var.set(format_commodity_map(leg.get("sell_commodities") or {}))
        self.bookmark_type_var.set(str(leg.get("system_bookmark_type") or ""))
        self.bookmark_number_var.set(str(leg.get("system_bookmark_number", -1)))
        self.update_count_var.set(bool(leg.get("update_commodity_count", True)))
        self.skip_var.set(bool(leg.get("skip", False)))

    def _clear_editor(self) -> None:
        self.selected_index = None
        self.leg_key_var.set("")
        self.system_var.set("")
        self.station_var.set("")
        self.buy_var.set("")
        self.sell_var.set("")
        self.bookmark_type_var.set("")
        self.bookmark_number_var.set("-1")
        self.update_count_var.set(True)
        self.skip_var.set(False)
        self.leg_list.selection_clear(0, self.tk.END)

    def _editor_has_content(self) -> bool:
        return any(
            value.strip()
            for value in [
                self.leg_key_var.get(),
                self.system_var.get(),
                self.station_var.get(),
                self.buy_var.get(),
                self.sell_var.get(),
                self.bookmark_type_var.get(),
            ]
        )

    def _leg_from_editor(self) -> dict[str, Any]:
        bookmark_number = int(float((self.bookmark_number_var.get() or "-1").strip()))
        key = self.leg_key_var.get().strip() or f"leg-{(self.selected_index or len(self.legs)) + 1}"
        leg = {
            "key": key,
            "system_name": self.system_var.get().strip(),
            "station_name": self.station_var.get().strip(),
            "buy_commodities": parse_commodity_map(self.buy_var.get()),
            "sell_commodities": parse_commodity_map(self.sell_var.get()),
            "system_bookmark_type": self.bookmark_type_var.get().strip(),
            "system_bookmark_number": bookmark_number,
            "update_commodity_count": bool(self.update_count_var.get()),
            "skip": bool(self.skip_var.get()),
        }
        if not leg["system_name"] and not leg["station_name"]:
            raise ValueError("Each route leg needs at least a system or station")
        return leg

    def _commit_editor_if_needed(self) -> None:
        if self.selected_index is not None or self._editor_has_content():
            self.save_leg()

    def load_from_disk(self) -> None:
        feed_cfg = _load_feed_cfg(self.base_dir)
        self.route_file_var.set(str(feed_cfg.get("route_file") or FEED_DEFAULTS["route_file"]))
        self.waypoint_output_var.set(str(feed_cfg.get("waypoint_output") or FEED_DEFAULTS["waypoint_output"]))
        self.edap_root_var.set(str(feed_cfg.get("edap_root") or ""))
        if not self.edap_root_var.get().strip():
            detected_edap = default_edap_root()
            if detected_edap:
                self.edap_root_var.set(str(detected_edap))
        self.auto_sync_var.set(bool(feed_cfg.get("auto_sync", True)))
        self.push_to_edap_var.set(bool(feed_cfg.get("push_to_edap", True)))
        self.auto_start_var.set(bool(feed_cfg.get("auto_start", False)))
        self.stop_before_sync_var.set(bool(feed_cfg.get("stop_before_sync", False)))
        self.write_tce_var.set(bool(feed_cfg.get("write_tce_shopping_list", False)))

        route_plan = _load_route_plan(self._route_path())
        self._set_route_plan(route_plan)
        self.status_var.set("Crew console loaded")
        self.detail_var.set("Route and manifest settings loaded from the current station.")

    def launch_setup_wizard(self) -> None:
        if hasattr(self, "wizard_window") and self.wizard_window and self.wizard_window.winfo_exists():
            self.wizard_window.lift()
            return

        bg = "#0f0f10"
        card = "#17181b"
        border = "#2a2d31"
        text = "#e8e8e8"
        muted = "#9a9a9a"
        accent = "#4cc9f0"

        window = self.tk.Toplevel(self.root)
        window.title("Piper Crew Setup Wizard")
        window.configure(bg=bg)
        window.geometry("760x620")
        self.wizard_window = window

        wizard_status = self.tk.StringVar(value="Walk through the setup once, then run the ship from the main console.")

        outer = self.tk.Frame(window, bg=bg)
        outer.pack(fill="both", expand=True, padx=14, pady=14)

        def section(parent, title, subtitle=None):
            frame = self.tk.Frame(parent, bg=card, highlightbackground=border, highlightthickness=1)
            frame.pack(fill="x", pady=(0, 10))
            self.tk.Label(frame, text=title, fg=accent, bg=card, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            if subtitle:
                self.tk.Label(frame, text=subtitle, fg=muted, bg=card, font=("Segoe UI", 9), justify="left", wraplength=700).pack(anchor="w", padx=12, pady=(0, 8))
            return frame

        intro = section(window, "Piper Crew Setup Wizard", "This wizard pins Ollama to the GTX 1080 Ti, fills the EDAP path, and gets your first trade loop ready.")
        self.tk.Label(intro, textvariable=wizard_status, fg=text, bg=card, font=("Segoe UI", 10, "bold"), justify="left", wraplength=700).pack(anchor="w", padx=12, pady=(0, 10))

        gpu_frame = section(outer, "1. GPU + Ollama", "Radio OS talks to Ollama over HTTP, so the Ollama server itself must be started on the correct GPU.")
        gpu_text = self.tk.Text(gpu_frame, height=5, bg="#101010", fg=text, relief="flat")
        gpu_text.pack(fill="x", padx=12, pady=(0, 8))
        gpu_text.insert("1.0", self._gpu_summary_text())
        gpu_text.configure(state="disabled")
        self.tk.Button(gpu_frame, text="Pin / Restart Ollama On GTX 1080 Ti", command=lambda: self._wizard_pin_ollama(wizard_status), bg="#1d3a46", fg="#e8f7fb", relief="flat", padx=10, pady=6).pack(anchor="w", padx=12, pady=(0, 10))

        edap_frame = section(outer, "2. EDAP Folder", "Use the downloaded EDAPGui copy as the autopilot sidecar.")
        edap_controls = self.tk.Frame(edap_frame, bg=card)
        edap_controls.pack(fill="x", padx=12, pady=(0, 10))
        self.tk.Entry(edap_controls, textvariable=self.edap_root_var, bg="#101010", fg=text, insertbackground=text, relief="flat").pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.tk.Button(edap_controls, text="Use Downloaded Copy", command=lambda: self._wizard_apply_detected_edap(wizard_status), bg="#202020", fg=text, relief="flat").pack(side="left")

        route_frame = section(outer, "3. First Route", "The sample route is safe to start with. You can replace the stations and commodities later from the main console or from Inara notes.")
        self.tk.Button(route_frame, text="Load Sample Trade Loop", command=lambda: self._wizard_load_sample_route(wizard_status), bg="#202020", fg=text, relief="flat").pack(anchor="w", padx=12, pady=(0, 10))

        finish_frame = section(outer, "4. Finish", "Save the current settings to the station manifest and mark the wizard complete.")
        finish_buttons = self.tk.Frame(finish_frame, bg=card)
        finish_buttons.pack(fill="x", padx=12, pady=(0, 10))
        self.tk.Button(finish_buttons, text="Save Wizard Settings", command=lambda: self._wizard_finish(wizard_status, close_only=False), bg="#28603d", fg="#ebfff2", relief="flat", padx=10, pady=6).pack(side="left", padx=(0, 8))
        self.tk.Button(finish_buttons, text="Save And Close", command=lambda: self._wizard_finish(wizard_status, close_only=True), bg="#36506f", fg="#edf6ff", relief="flat", padx=10, pady=6).pack(side="left")

    def _gpu_summary_text(self) -> str:
        gpus = detect_nvidia_gpus()
        if not gpus:
            return "No NVIDIA GPUs were detected by nvidia-smi from inside Radio OS."
        preferred = choose_preferred_ollama_gpu(gpus)
        lines = []
        for gpu in gpus:
            marker = "  <- preferred for Ollama" if preferred and gpu == preferred else ""
            lines.append(f"GPU {gpu['index']}: {gpu['name']} ({gpu['memory_total']}){marker}")
        return os.linesep.join(lines)

    def _wizard_pin_ollama(self, status_var) -> None:
        ok, message = run_ollama_gpu_pin(force_restart=True)
        if ok:
            status_var.set(message)
            self.status_var.set("Ollama pinned to GTX 1080 Ti")
            self.detail_var.set(message)
        else:
            status_var.set(message)
            self.status_var.set("Ollama pinning failed")
            self.detail_var.set(message)

    def _wizard_apply_detected_edap(self, status_var) -> None:
        detected = default_edap_root()
        if detected:
            self.edap_root_var.set(str(detected))
            status_var.set(f"Using EDAP at {detected}")
        else:
            status_var.set("No downloaded EDAP folder was found in the default locations.")

    def _wizard_load_sample_route(self, status_var) -> None:
        self._set_route_plan(_default_route_plan())
        status_var.set("Loaded the sample trade loop. Edit the leg list later from the main console.")

    def _wizard_finish(self, status_var, close_only: bool) -> None:
        self.save_route_plan()
        mem = self._memory()
        mem["elite_trade_crew_wizard_completed"] = True
        self._save_memory(mem)
        status_var.set("Wizard settings saved.")
        if close_only and hasattr(self, "wizard_window") and self.wizard_window and self.wizard_window.winfo_exists():
            self.wizard_window.destroy()

    def new_leg(self) -> None:
        self._clear_editor()
        self.status_var.set("Creating a new trade leg")
        self.detail_var.set("Fill in the system, station, and trade fields, then click Save Leg.")

    def save_leg(self) -> None:
        try:
            leg = self._leg_from_editor()
        except ValueError as exc:
            self.status_var.set("Leg not saved")
            self.detail_var.set(str(exc))
            return

        if self.selected_index is None:
            self.legs.append(leg)
            self.selected_index = len(self.legs) - 1
        else:
            self.legs[self.selected_index] = leg

        self._refresh_leg_list()
        self._select_leg(self.selected_index)
        self.status_var.set("Leg saved")
        self.detail_var.set(_describe_leg({
            "system_name": leg.get("system_name"),
            "station_name": leg.get("station_name"),
            "buy_commodities": leg.get("buy_commodities"),
            "sell_commodities": leg.get("sell_commodities"),
        }))

    def remove_leg(self) -> None:
        if self.selected_index is None:
            return
        self.legs.pop(self.selected_index)
        self._refresh_leg_list()
        if self.legs:
            self._select_leg(max(self.selected_index - 1, 0))
        else:
            self._clear_editor()
        self.status_var.set("Leg removed")
        self.detail_var.set("The trade loop has been updated.")

    def move_leg_up(self) -> None:
        if self.selected_index is None or self.selected_index <= 0:
            return
        idx = self.selected_index
        self.legs[idx - 1], self.legs[idx] = self.legs[idx], self.legs[idx - 1]
        self._refresh_leg_list()
        self._select_leg(idx - 1)

    def move_leg_down(self) -> None:
        if self.selected_index is None or self.selected_index >= len(self.legs) - 1:
            return
        idx = self.selected_index
        self.legs[idx + 1], self.legs[idx] = self.legs[idx], self.legs[idx + 1]
        self._refresh_leg_list()
        self._select_leg(idx + 1)

    def _bridge(self) -> EDAPBridge:
        edap_root = self.edap_root_var.get().strip()
        if not edap_root:
            raise FileNotFoundError("Set the EDAP folder in Crew Settings first")
        return EDAPBridge(edap_root=edap_root)

    def save_route_plan(self) -> None:
        try:
            route_plan = self._current_route_plan()
            route_path = self._route_path()
            waypoint_path = self._waypoint_output_path()
            write_json_file(route_path, route_plan)
            write_json_file(waypoint_path, build_edap_waypoints(route_plan))
            _save_feed_cfg(self.base_dir, self._feed_cfg())
            summary = summarize_route_progress(build_edap_waypoints(route_plan))
            self.status_var.set("Trade plan saved")
            self.detail_var.set(
                f"Saved {len(route_plan['legs'])} leg(s). Next action: {_describe_leg(summary.get('next_leg'))}"
            )
        except Exception as exc:
            self.status_var.set("Save failed")
            self.detail_var.set(str(exc))

    def sync_to_edap(self) -> None:
        try:
            self.save_route_plan()
            bridge = self._bridge()
            bridge.push_waypoints(
                self._waypoint_output_path(),
                start_assist=False,
                stop_first=bool(self.stop_before_sync_var.get()),
                write_tce_shopping_list=bool(self.write_tce_var.get()),
            )
            self.status_var.set("Route synced to EDAP")
            self.detail_var.set("EDAP received the latest waypoint file. Start the route when ready.")
        except Exception as exc:
            self.status_var.set("Sync failed")
            self.detail_var.set(str(exc))

    def start_route(self) -> None:
        try:
            self.save_route_plan()
            bridge = self._bridge()
            bridge.push_waypoints(
                self._waypoint_output_path(),
                start_assist=True,
                stop_first=bool(self.stop_before_sync_var.get()),
                write_tce_shopping_list=bool(self.write_tce_var.get()),
            )
            self.status_var.set("Route armed")
            self.detail_var.set("EDAP loaded the route and was asked to start Waypoint Assist.")
        except Exception as exc:
            self.status_var.set("Start failed")
            self.detail_var.set(str(exc))

    def stop_assists(self) -> None:
        try:
            bridge = self._bridge()
            bridge.stop_all_assists()
            self.status_var.set("Assists stopped")
            self.detail_var.set("EDAP was told to stop all running assists.")
        except Exception as exc:
            self.status_var.set("Stop failed")
            self.detail_var.set(str(exc))

    def _on_leg_select(self, _event=None) -> None:
        if not self.leg_list.curselection():
            return
        self._select_leg(int(self.leg_list.curselection()[0]))

    def on_update(self, data: Any):
        if not isinstance(data, dict):
            return
        title = str(data.get("title") or "Crew update")
        body = str(data.get("body") or "")
        summary = data.get("summary") or {}
        self.status_var.set(title)
        next_leg = summary.get("next_leg")
        if next_leg:
            body = f"{body} Current target: {_describe_leg(next_leg)}"
        self.detail_var.set(body)


def feed_worker(stop_event, mem, cfg, runtime=None):
    runtime_dict = runtime if isinstance(runtime, dict) else None
    base_dir = Path.cwd()
    route_path = _resolve_path(str(cfg.get("route_file") or "./trade_loop.sample.json"), base_dir)
    waypoint_output = _resolve_path(str(cfg.get("waypoint_output") or "./runtime_edap_waypoints.json"), base_dir)
    poll_sec = float(cfg.get("poll_sec", 15))
    status_cooldown_sec = float(cfg.get("status_cooldown_sec", 120))

    while not stop_event.is_set():
        try:
            route_plan = read_json_file(route_path)
            route_sig = compute_route_signature(route_plan)

            if cfg.get("auto_sync", True) and mem.get("elite_trade_crew_route_sig") != route_sig:
                waypoints = build_edap_waypoints(route_plan)
                write_json_file(waypoint_output, waypoints)
                mem["elite_trade_crew_route_sig"] = route_sig
                mem["elite_trade_crew_waypoint_output"] = str(waypoint_output)
                _runtime_log(runtime_dict, f"Wrote EDAP waypoint plan to {waypoint_output}")

                edap_root = str(cfg.get("edap_root") or "").strip()
                if cfg.get("push_to_edap", True) and edap_root:
                    bridge = EDAPBridge(
                        edap_root=edap_root,
                        actions_port=int(cfg.get("edap_actions_port", 15570)),
                        events_port=int(cfg.get("edap_events_port", 15571)),
                    )
                    bridge.push_waypoints(
                        waypoint_output,
                        start_assist=bool(cfg.get("auto_start", False)),
                        stop_first=bool(cfg.get("stop_before_sync", False)),
                        write_tce_shopping_list=bool(cfg.get("write_tce_shopping_list", False)),
                    )
                    _runtime_log(runtime_dict, "Pushed waypoint plan to EDAP over EDMesg")

            progress_source = waypoint_output if waypoint_output.exists() else route_path
            progress_payload = read_json_file(progress_source)
            if "GlobalShoppingList" not in progress_payload:
                progress_payload = build_edap_waypoints(progress_payload)

            summary = summarize_route_progress(progress_payload)
            status_sig = json.dumps(summary, sort_keys=True)
            last_emit_ts = float(mem.get("elite_trade_crew_status_ts", 0))
            if (
                mem.get("elite_trade_crew_status_sig") != status_sig
                or (now_ts() - last_emit_ts) >= status_cooldown_sec
            ):
                _emit_status(runtime_dict, cfg, summary)
                mem["elite_trade_crew_status_sig"] = status_sig
                mem["elite_trade_crew_status_ts"] = now_ts()

        except FileNotFoundError as exc:
            _runtime_log(runtime_dict, f"Trade crew route file missing: {exc}")
        except Exception as exc:
            _runtime_log(runtime_dict, f"Trade crew update failed: {exc}")

        stop_event.wait(poll_sec)