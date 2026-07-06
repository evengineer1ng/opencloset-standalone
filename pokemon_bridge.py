from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib import error, request

try:
    from mss import mss as _mss_factory
    from mss import tools as mss_tools
except ImportError:  # pragma: no cover - handled via runtime error path
    _mss_factory = None
    mss_tools = None


KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
DEFAULT_API_BASE = "http://127.0.0.1:5000"
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0

DEFAULT_BUTTON_KEYMAP = {
    "UP": "UP",
    "DOWN": "DOWN",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
    "A": "Z",
    "B": "X",
    "X": "S",
    "Y": "A",
    "L": "Q",
    "R": "W",
    "START": "M",
    "SELECT": "N",
}

NAMED_VIRTUAL_KEYS = {
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "ENTER": 0x0D,
    "SPACE": 0x20,
    "ESC": 0x1B,
    "TAB": 0x09,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
}

WINDOW_GAME_ALIASES = (
    ("omega ruby", ("pokemon_oras", "Pokemon Omega Ruby")),
    ("alpha sapphire", ("pokemon_oras", "Pokemon Alpha Sapphire")),
    ("pokémon y", ("pokemon_xy", "Pokemon Y")),
    ("pokemon y", ("pokemon_xy", "Pokemon Y")),
    ("pokémon x", ("pokemon_xy", "Pokemon X")),
    ("pokemon x", ("pokemon_xy", "Pokemon X")),
)


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req) as response:
        body = response.read().decode("utf-8")
    return json.loads(body or "{}")


def _post_snapshot(api_base: str, channel: str, snapshot: dict) -> dict:
    url = f"{api_base.rstrip('/')}/api/runtime/agents/{channel}/pokemon/bridge/snapshot"
    return _request_json(url, method="POST", payload=snapshot)


def _post_runtime_event(api_base: str, channel: str, *, event_type: str, payload: dict[str, Any], text: str) -> dict:
    url = f"{api_base.rstrip('/')}/api/runtime/agents/{channel}/events"
    return _request_json(url, method="POST", payload={"type": event_type, "payload": payload, "text": text})


def _print_schema(api_base: str) -> None:
    url = f"{api_base.rstrip('/')}/api/runtime/pokemon/schema"
    payload = _request_json(url)
    print(json.dumps(payload, indent=2, sort_keys=True))


def _get_pokemon_bridge_status(api_base: str, channel: str) -> dict:
    url = f"{api_base.rstrip('/')}/api/runtime/agents/{channel}/pokemon/bridge"
    return _request_json(url)


def _list_runtime_outputs(api_base: str, channel: str, *, limit: int = 10) -> list[dict[str, Any]]:
    url = f"{api_base.rstrip('/')}/api/runtime/agents/{channel}/outputs?limit={limit}"
    payload = _request_json(url)
    return list(payload.get("outputs") or [])


def _patch_pokemon_control(api_base: str, channel: str, payload: dict[str, Any]) -> dict:
    url = f"{api_base.rstrip('/')}/api/runtime/agents/{channel}/pokemon/control"
    return _request_json(url, method="PATCH", payload=payload)


def _load_snapshot_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_hook_command(command: str, *, cwd: str | None = None) -> dict:
    result = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Hook command failed: {result.returncode}")
    payload = json.loads((result.stdout or "").strip() or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("Hook command must emit a JSON object")
    return payload


def _safe_slug(value: str) -> str:
    raw = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    compact = "-".join(part for part in raw.split("-") if part)
    return compact or "pokemon-runtime"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000000Z"


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(current, value)
        else:
            merged[key] = value
    return merged


def _default_output_dir(channel: str) -> Path:
    return Path(__file__).resolve().parent / "artifacts" / "images" / "pokemon_bridge" / _safe_slug(channel)


def _resolve_virtual_key(token: str) -> int:
    normalized = str(token or "").strip().upper()
    if not normalized:
        raise ValueError("input token is required")
    if normalized in NAMED_VIRTUAL_KEYS:
        return NAMED_VIRTUAL_KEYS[normalized]
    if len(normalized) == 1:
        return ord(normalized)
    raise ValueError(f"Unsupported input token: {token}")


def _select_action_command(outputs: list[dict[str, Any]], *, last_output_id: str | None = None) -> dict[str, Any] | None:
    for output in outputs:
        output_id = str(output.get("id") or "").strip()
        if output_id and output_id == str(last_output_id or ""):
            return None

        if str(output.get("output_type") or "") != "pokemon.action_command":
            continue

        payload = dict(output.get("payload") or {})
        buttons = [str(button).strip().upper() for button in list(payload.get("buttons") or []) if str(button).strip()]
        if not buttons:
            continue

        payload["buttons"] = buttons
        payload["output_id"] = output_id
        return payload
    return None


def _maybe_execute_runtime_action(
    *,
    api_base: str,
    channel: str,
    live_bridge: "WindowsCitraBridge" | None,
    focus_window: bool,
    hold_ms: int,
    settle_ms: int,
    last_output_id: str | None,
) -> str | None:
    if live_bridge is None:
        return last_output_id

    status = _get_pokemon_bridge_status(api_base, channel)
    control = dict(status.get("control") or {})
    mode = str(control.get("mode") or "assist").strip().lower()
    step_budget = int(control.get("step_budget") or 0)
    if mode not in {"auto", "step"}:
        return last_output_id
    if mode == "step" and step_budget <= 0:
        return last_output_id

    command = _select_action_command(_list_runtime_outputs(api_base, channel, limit=1000), last_output_id=last_output_id)
    if not command:
        return last_output_id

    input_result = live_bridge.send_input(
        list(command["buttons"]),
        focus_window=focus_window,
        hold_ms=hold_ms,
        settle_ms=settle_ms,
    )
    try:
        _post_runtime_event(
            api_base,
            channel,
            event_type="pokemon.input.result",
            payload=input_result,
            text=f"Injected inputs: {', '.join(input_result['buttons'])}.",
        )
    except error.URLError:
        pass

    print(
        json.dumps(
            {
                "channel": channel,
                "executed_buttons": input_result["buttons"],
                "action_output_id": command.get("output_id") or "",
                "captured_at": input_result.get("captured_at") or "",
            },
            sort_keys=True,
        )
    )

    if mode == "step":
        remaining = max(0, step_budget - 1)
        patch_payload: dict[str, Any] = {"step_budget": remaining}
        if remaining == 0:
            patch_payload["mode"] = "assist"
        try:
            _patch_pokemon_control(api_base, channel, patch_payload)
        except error.URLError:
            pass

    return str(command.get("output_id") or last_output_id or "") or last_output_id


def _resolve_snapshot_source(args) -> str | None:
    explicit = str(args.snapshot_source or "").strip().lower()
    if explicit:
        return explicit
    has_window = bool(args.window_title or args.window_class)
    has_hook = bool(args.hook_command)
    has_file = bool(args.snapshot_file)
    if has_window and has_hook:
        return "hybrid"
    if has_window:
        return "window"
    if has_hook:
        return "hook"
    if has_file:
        return "file"
    return None


def _infer_pokemon_identity(window_title: str) -> tuple[str, str]:
    title = str(window_title or "").strip().lower()
    for token, identity in WINDOW_GAME_ALIASES:
        if token in title:
            return identity
    return ("pokemon_citra", "Pokemon")


@dataclass(slots=True)
class CitraWindowInfo:
    hwnd: int
    title: str
    class_name: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class WindowsCitraBridge:
    def __init__(
        self,
        *,
        title_hint: str | None = None,
        class_name: str | None = None,
        keymap: dict[str, str] | None = None,
        mss_factory: Any | None = None,
    ) -> None:
        self.title_hint = str(title_hint or "Citra").strip()
        self.class_name = str(class_name or "").strip()
        self.keymap = {key.upper(): value for key, value in (keymap or DEFAULT_BUTTON_KEYMAP).items()}
        self._mss_factory = mss_factory or _mss_factory

    def find_window(self) -> CitraWindowInfo:
        if os.name != "nt":
            raise RuntimeError("Live Citra capture is only implemented for Windows")

        user32 = ctypes.windll.user32
        matches: list[CitraWindowInfo] = []
        title_hint = self.title_hint.lower()
        class_hint = self.class_name.lower()

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            title_length = user32.GetWindowTextLengthW(hwnd)
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
            title = title_buffer.value.strip()
            if not title:
                return True

            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            class_name = class_buffer.value.strip()

            if title_hint and title_hint not in title.lower():
                return True
            if class_hint and class_hint != class_name.lower():
                return True

            rect = _Rect()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True

            matches.append(
                CitraWindowInfo(
                    hwnd=int(hwnd),
                    title=title,
                    class_name=class_name,
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                )
            )
            return True

        user32.EnumWindows(callback_type(_enum_callback), 0)

        if not matches:
            raise RuntimeError(f"Could not find a visible Citra window matching '{self.title_hint}'")
        return matches[0]

    def focus_window(self, window: CitraWindowInfo) -> None:
        if os.name != "nt":
            raise RuntimeError("Live Citra control is only implemented for Windows")
        user32 = ctypes.windll.user32
        user32.ShowWindow(window.hwnd, SW_RESTORE)
        user32.SetForegroundWindow(window.hwnd)

    def capture_window_snapshot(self, *, channel: str, output_dir: Path, focus_window: bool = False) -> dict[str, Any]:
        window = self.find_window()
        if focus_window:
            self.focus_window(window)
        frame = self._capture_frame(window, output_dir=output_dir)
        profile_name, game_name = _infer_pokemon_identity(window.title)
        return {
            "captured_at": frame["captured_at"],
            "emulator": {
                "name": "Citra",
                "connected": True,
                "window_title": window.title,
                "profile": profile_name,
                "game": game_name,
            },
            "route": {
                "map_name": "",
                "route": "",
                "objective": "Observe current state from live capture.",
                "next_action": "Wait for hook data or operator input.",
            },
            "objective": {
                "title": f"Observe {game_name} state",
                "summary": f"Live Citra frame captured for {channel}.",
                "why": "Keep the runtime state fresh while higher-confidence hooks or operator actions fill in game semantics.",
                "next_action": "Use hook data or manual guidance to refine the next move.",
            },
            "frame": frame,
        }

    def send_input(
        self,
        buttons: list[str],
        *,
        focus_window: bool = True,
        hold_ms: int = 80,
        settle_ms: int = 60,
    ) -> dict[str, Any]:
        if os.name != "nt":
            raise RuntimeError("Live Citra control is only implemented for Windows")
        if not buttons:
            raise ValueError("At least one button is required")

        window = self.find_window()
        if focus_window:
            self.focus_window(window)

        delivered: list[dict[str, Any]] = []
        user32 = ctypes.windll.user32
        for button in buttons:
            token = self.keymap.get(button.upper(), button)
            vk = _resolve_virtual_key(token)
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(max(hold_ms, 0) / 1000.0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            delivered.append({"button": button.upper(), "mapped_to": str(token).upper(), "vk": vk})
            time.sleep(max(settle_ms, 0) / 1000.0)

        return {
            "buttons": [item["button"] for item in delivered],
            "delivered": True,
            "delivered_buttons": delivered,
            "window_title": window.title,
            "captured_at": _now_iso(),
        }

    def _capture_frame(self, window: CitraWindowInfo, *, output_dir: Path) -> dict[str, Any]:
        if os.name == "nt":
            try:
                return self._capture_frame_via_printwindow(window, output_dir=output_dir)
            except Exception:
                pass

        if self._mss_factory is None or mss_tools is None:
            raise RuntimeError("Live window capture requires the 'mss' package. Add it to the environment first.")

        output_dir.mkdir(parents=True, exist_ok=True)
        captured_at = _now_iso()
        file_name = f"{captured_at.replace(':', '').replace('-', '').replace('.', '')}.png"
        frame_path = output_dir / file_name

        monitor = {
            "left": window.left,
            "top": window.top,
            "width": max(window.width, 1),
            "height": max(window.height, 1),
        }
        with self._mss_factory() as sct:
            shot = sct.grab(monitor)
            frame_path.write_bytes(mss_tools.to_png(shot.rgb, shot.size))

        return {
            "path": str(frame_path),
            "width": monitor["width"],
            "height": monitor["height"],
            "capture_source": "window-region",
            "captured_at": captured_at,
            "window_rect": {
                "left": window.left,
                "top": window.top,
                "right": window.right,
                "bottom": window.bottom,
            },
        }

    def _capture_frame_via_printwindow(self, window: CitraWindowInfo, *, output_dir: Path) -> dict[str, Any]:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        client_rect = _Rect()
        if not user32.GetClientRect(window.hwnd, ctypes.byref(client_rect)):
            raise RuntimeError("Could not read Citra client rect")

        top_left = _Point(0, 0)
        bottom_right = _Point(client_rect.right, client_rect.bottom)
        if not user32.ClientToScreen(window.hwnd, ctypes.byref(top_left)):
            raise RuntimeError("Could not translate Citra client top-left")
        if not user32.ClientToScreen(window.hwnd, ctypes.byref(bottom_right)):
            raise RuntimeError("Could not translate Citra client bottom-right")

        width = max(1, int(bottom_right.x - top_left.x))
        height = max(1, int(bottom_right.y - top_left.y))

        output_dir.mkdir(parents=True, exist_ok=True)
        captured_at = _now_iso()
        file_name = f"{captured_at.replace(':', '').replace('-', '').replace('.', '')}.png"
        frame_path = output_dir / file_name

        hwnd_dc = user32.GetDC(window.hwnd)
        if not hwnd_dc:
            raise RuntimeError("Could not acquire Citra window DC")

        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            user32.ReleaseDC(window.hwnd, hwnd_dc)
            raise RuntimeError("Could not create compatible DC for Citra capture")

        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        if not bitmap:
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(window.hwnd, hwnd_dc)
            raise RuntimeError("Could not create compatible bitmap for Citra capture")

        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
        try:
            flags_to_try = (
                PW_CLIENTONLY | PW_RENDERFULLCONTENT,
                PW_RENDERFULLCONTENT,
                PW_CLIENTONLY,
                0,
            )
            success = False
            for flags in flags_to_try:
                if user32.PrintWindow(window.hwnd, mem_dc, flags):
                    success = True
                    break
            if not success:
                raise RuntimeError("PrintWindow could not capture the Citra client area")

            bitmap_info = _BitmapInfo()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            bitmap_info.bmiHeader.biWidth = width
            bitmap_info.bmiHeader.biHeight = -height
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            bitmap_info.bmiHeader.biCompression = BI_RGB

            raw = ctypes.create_string_buffer(width * height * 4)
            rows_copied = gdi32.GetDIBits(
                mem_dc,
                bitmap,
                0,
                height,
                raw,
                ctypes.byref(bitmap_info),
                DIB_RGB_COLORS,
            )
            if rows_copied != height:
                raise RuntimeError("GetDIBits did not return the expected Citra frame rows")

            bgra = raw.raw
            rgb = bytearray(width * height * 3)
            rgb[0::3] = bgra[2::4]
            rgb[1::3] = bgra[1::4]
            rgb[2::3] = bgra[0::4]
            frame_path.write_bytes(mss_tools.to_png(bytes(rgb), (width, height)))
        finally:
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(window.hwnd, hwnd_dc)

        return {
            "path": str(frame_path),
            "width": width,
            "height": height,
            "capture_source": "window-client",
            "captured_at": captured_at,
            "window_rect": {
                "left": int(top_left.x),
                "top": int(top_left.y),
                "right": int(bottom_right.x),
                "bottom": int(bottom_right.y),
            },
        }


def _load_keymap(path: str | None) -> dict[str, str]:
    if not path:
        return dict(DEFAULT_BUTTON_KEYMAP)
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Keymap file must contain a JSON object")
    merged = dict(DEFAULT_BUTTON_KEYMAP)
    for key, value in payload.items():
        merged[str(key).upper()] = str(value)
    return merged


def _collect_snapshot(
    *,
    source: str,
    snapshot_path: Path | None,
    hook_command: str | None,
    hook_cwd: str | None,
    live_bridge: WindowsCitraBridge | None,
    channel: str,
    output_dir: Path,
    focus_window: bool,
) -> dict[str, Any]:
    if source == "file":
        if snapshot_path is None:
            raise RuntimeError("snapshot file is required for file source")
        return _load_snapshot_file(snapshot_path)

    if source == "hook":
        if not hook_command:
            raise RuntimeError("hook command is required for hook source")
        return _run_hook_command(hook_command, cwd=hook_cwd)

    if source == "window":
        if live_bridge is None:
            raise RuntimeError("live Citra bridge is required for window source")
        return live_bridge.capture_window_snapshot(channel=channel, output_dir=output_dir, focus_window=focus_window)

    if source == "hybrid":
        if live_bridge is None or not hook_command:
            raise RuntimeError("hybrid source requires both live capture and hook command")
        window_snapshot = live_bridge.capture_window_snapshot(channel=channel, output_dir=output_dir, focus_window=focus_window)
        hook_snapshot = _run_hook_command(hook_command, cwd=hook_cwd)
        return _deep_merge_dict(window_snapshot, hook_snapshot)

    raise RuntimeError(f"Unsupported snapshot source: {source}")


def _print_snapshot_result(channel: str, result: dict[str, Any]) -> None:
    print(json.dumps({
        "channel": channel,
        "last_snapshot_at": result.get("bridge", {}).get("last_snapshot_at"),
        "events": [event.get("event_type") for event in result.get("events", [])],
        "frame": result.get("bridge", {}).get("frame", {}).get("path"),
    }))


def _is_transient_live_window_error(exc: Exception, *, source: str) -> bool:
    if source not in {"window", "hybrid"}:
        return False
    if not isinstance(exc, RuntimeError):
        return False
    return "Could not find a visible Citra window matching" in str(exc)


def _print_transient_capture_warning(channel: str, exc: Exception) -> None:
    print(json.dumps({
        "channel": channel,
        "warning": str(exc),
        "retrying": True,
    }))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokemon/Citra bridge sidecar for OpenCloset runtime channels.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="OpenCloset API base URL")
    parser.add_argument("--channel", help="Pokemon runtime channel name")
    parser.add_argument("--snapshot-source", choices=["file", "hook", "window", "hybrid"], help="Snapshot source")
    parser.add_argument("--snapshot-file", help="Path to a JSON snapshot file to relay")
    parser.add_argument("--hook-command", help="Command that emits a JSON snapshot to stdout")
    parser.add_argument("--hook-cwd", help="Working directory for the hook command")
    parser.add_argument("--window-title", default="Citra", help="Substring used to find the Citra window")
    parser.add_argument("--window-class", help="Exact window class to match when locating Citra")
    parser.add_argument("--output-dir", help="Directory for captured frame PNGs")
    parser.add_argument("--keymap-file", help="JSON file overriding Pokemon button to key mappings")
    parser.add_argument("--send-input", nargs="+", help="Send one or more input buttons to the Citra window")
    parser.add_argument("--focus-window", action="store_true", help="Try to focus the Citra window before capture or input")
    parser.add_argument("--hold-ms", type=int, default=80, help="Milliseconds to hold each input key")
    parser.add_argument("--settle-ms", type=int, default=60, help="Milliseconds between input keys")
    parser.add_argument("--once", action="store_true", help="Send one snapshot and exit")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval for live snapshot modes")
    parser.add_argument("--print-schema", action="store_true", help="Print the Pokemon bridge schema and exit")
    args = parser.parse_args(argv)

    if args.print_schema:
        _print_schema(args.api_base)
        return 0

    if not args.channel:
        parser.error("--channel is required unless --print-schema is used")

    source = _resolve_snapshot_source(args)
    if source is None and not args.send_input:
        parser.error("Choose a snapshot source or provide --send-input")

    snapshot_path = Path(args.snapshot_file).expanduser().resolve() if args.snapshot_file else None
    if snapshot_path is not None and not snapshot_path.exists():
        raise FileNotFoundError(snapshot_path)

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else _default_output_dir(args.channel)
    live_bridge = None
    if source in {"window", "hybrid"} or args.send_input:
        live_bridge = WindowsCitraBridge(
            title_hint=args.window_title,
            class_name=args.window_class,
            keymap=_load_keymap(args.keymap_file),
        )

    try:
        last_action_output_id: str | None = None
        if args.send_input:
            if live_bridge is None:
                raise RuntimeError("Live Citra bridge is required for input injection")
            input_result = live_bridge.send_input(
                list(args.send_input),
                focus_window=args.focus_window,
                hold_ms=args.hold_ms,
                settle_ms=args.settle_ms,
            )
            try:
                _post_runtime_event(
                    args.api_base,
                    args.channel,
                    event_type="pokemon.input.result",
                    payload=input_result,
                    text=f"Injected inputs: {', '.join(input_result['buttons'])}.",
                )
            except error.URLError:
                pass
            print(json.dumps(input_result, indent=2, sort_keys=True))
            if source is None:
                return 0

        if source is None:
            return 0

        if args.once:
            result = _post_snapshot(
                args.api_base,
                args.channel,
                _collect_snapshot(
                    source=source,
                    snapshot_path=snapshot_path,
                    hook_command=args.hook_command,
                    hook_cwd=args.hook_cwd,
                    live_bridge=live_bridge,
                    channel=args.channel,
                    output_dir=output_dir,
                    focus_window=args.focus_window,
                ),
            )
            last_action_output_id = _maybe_execute_runtime_action(
                api_base=args.api_base,
                channel=args.channel,
                live_bridge=live_bridge,
                focus_window=args.focus_window,
                hold_ms=args.hold_ms,
                settle_ms=args.settle_ms,
                last_output_id=last_action_output_id,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        last_mtime: float | None = None
        while True:
            should_send = True
            if source == "file" and snapshot_path is not None:
                stat = snapshot_path.stat()
                should_send = last_mtime is None or stat.st_mtime > last_mtime
                if should_send:
                    last_mtime = stat.st_mtime
            if should_send:
                try:
                    result = _post_snapshot(
                        args.api_base,
                        args.channel,
                        _collect_snapshot(
                            source=source,
                            snapshot_path=snapshot_path,
                            hook_command=args.hook_command,
                            hook_cwd=args.hook_cwd,
                            live_bridge=live_bridge,
                            channel=args.channel,
                            output_dir=output_dir,
                            focus_window=args.focus_window,
                        ),
                    )
                    last_action_output_id = _maybe_execute_runtime_action(
                        api_base=args.api_base,
                        channel=args.channel,
                        live_bridge=live_bridge,
                        focus_window=args.focus_window,
                        hold_ms=args.hold_ms,
                        settle_ms=args.settle_ms,
                        last_output_id=last_action_output_id,
                    )
                    _print_snapshot_result(args.channel, result)
                except Exception as exc:
                    if _is_transient_live_window_error(exc, source=source):
                        _print_transient_capture_warning(args.channel, exc)
                    else:
                        raise
            time.sleep(max(args.poll_seconds, 0.1))
    except KeyboardInterrupt:
        return 0
    except error.URLError as exc:
        print(f"Bridge post failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())