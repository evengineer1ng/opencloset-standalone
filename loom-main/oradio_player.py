#!/usr/bin/env python3
"""
Radio OS .oradio bootstrapper.

This is the first standalone player path: it consumes a .oradio package, runs the
resolver ladder, and launches the current bookmark.py kernel against the extracted
station. Studio and shell_bookmark.py are not involved.
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

import app_paths
import descriptor_club_gate
import oradio_resolver
import provisioning


BASE_DIR = app_paths.bundle_root()
# The .oradio plays in the themed GUI runtime fork (monokai via radio_os_theme), NOT headless
# bookmark.py. bookmark.py stays preserved as the frozen ancestor; oradio_runtime.py is the player.
RUNTIME_PATH = BASE_DIR / "oradio_runtime.py"
PLAYER_PATH = BASE_DIR / "oradio_player.py"
ORADIO_EXT = ".oradio"
ORADIO_PROG_ID = "RadioOS.Station"
ORADIO_DESCRIPTION = "Radio OS Station"


def read_manifest(extract_dir: Path) -> Dict[str, Any]:
    path = extract_dir / "manifest.yaml"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def station_path(extract_dir: Path, value: Any, default_name: str) -> Path:
    raw = str(value or default_name).strip() or default_name
    path = Path(raw)
    if path.is_absolute():
        return path
    return extract_dir / path


def quote_windows_arg(value: Path | str) -> str:
    return f'"{str(value)}"'


def windows_association_plan(
    *,
    executable_path: Optional[Path] = None,
    python_exe: Optional[Path] = None,
    player_path: Optional[Path] = None,
    shell: bool = False,
) -> Dict[str, str]:
    shell_arg = " --shell" if shell else ""
    default_icon_target: Path
    if executable_path is not None or app_paths.is_frozen():
        exe = Path(executable_path or sys.executable)
        open_command = f"{quote_windows_arg(exe)}{shell_arg} \"%1\""
        default_icon_target = exe
    else:
        python_exe = Path(python_exe or sys.executable)
        player_path = Path(player_path or PLAYER_PATH)
        open_command = f"{quote_windows_arg(python_exe)} {quote_windows_arg(player_path)}{shell_arg} \"%1\""
        default_icon_target = player_path
    return {
        "extension": ORADIO_EXT,
        "prog_id": ORADIO_PROG_ID,
        "description": ORADIO_DESCRIPTION,
        "extension_key": rf"Software\Classes\{ORADIO_EXT}",
        "prog_id_key": rf"Software\Classes\{ORADIO_PROG_ID}",
        "open_command_key": rf"Software\Classes\{ORADIO_PROG_ID}\shell\open\command",
        "open_command": open_command,
        "default_icon": f"{quote_windows_arg(default_icon_target)},0",
    }


def install_windows_file_association(
    *,
    python_exe: Optional[Path] = None,
    player_path: Optional[Path] = None,
) -> Dict[str, str]:
    if sys.platform != "win32":
        raise RuntimeError(".oradio file association install is only supported on Windows.")
    try:
        import ctypes
        import winreg
    except Exception as exc:  # pragma: no cover - platform defensive
        raise RuntimeError(f"Windows registry APIs are unavailable: {exc}") from exc

    plan = windows_association_plan(python_exe=python_exe, player_path=player_path)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, plan["extension_key"]) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, plan["prog_id"])
        winreg.SetValueEx(key, "Content Type", 0, winreg.REG_SZ, "application/x-oradio")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, plan["prog_id_key"]) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, plan["description"])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{plan['prog_id_key']}\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, plan["default_icon"])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, plan["open_command_key"]) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, plan["open_command"])

    # SHCNE_ASSOCCHANGED notifies Explorer without requiring a reboot.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    return plan


def format_association_plan(plan: Dict[str, str]) -> str:
    return "\n".join([
        ".oradio Windows association plan",
        f"  extension: {plan['extension']} -> {plan['prog_id']}",
        f"  description: {plan['description']}",
        f"  open command: {plan['open_command']}",
        f"  registry: HKCU\\{plan['extension_key']}",
        f"  registry: HKCU\\{plan['open_command_key']}",
    ])


def _saved_default_models() -> Dict[str, Any]:
    cfg = provisioning.read_global_config()
    return cfg.get("default_models", {}) if isinstance(cfg.get("default_models"), dict) else {}


def tune_in_membership(
    *,
    provider: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    pull_model: bool = False,
    validate: bool = True,
) -> Dict[str, Any]:
    defaults = _saved_default_models()
    provider = (provider or defaults.get("provider") or "ollama").lower()
    endpoint = endpoint or defaults.get("llm_endpoint") or "http://127.0.0.1:11434/api/generate"
    model = model or defaults.get("host_model") or defaults.get("producer_model") or ""
    key_field = provisioning.KEY_FIELD.get(provider, "")
    api_key = api_key if api_key is not None else defaults.get(key_field, "")

    if pull_model:
        if provider != "ollama":
            return {"ok": False, "error": "--pull-model only applies to the Ollama provider"}
        if not model:
            return {"ok": False, "error": "--pull-model needs --model"}
        pull = provisioning.pull_ollama_model(endpoint, model, on_progress=lambda line: print(f"pull: {line}"))
        if not pull.get("ok"):
            return {"ok": False, "error": f"model pull failed: {pull.get('error')}"}

    validation: Dict[str, Any] = {"ok": True, "error": None}
    if validate:
        validation = provisioning.validate_provider(provider, endpoint=endpoint, key=api_key or "", model=model)
        if not validation.get("ok"):
            return {
                "ok": False,
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "error": validation.get("error") or "provider validation failed",
                "needs_pull": bool(validation.get("needs_pull")),
            }

    provisioning.save_llm_membership(
        provider,
        endpoint=endpoint,
        key=api_key or None,
        host_model=model or None,
        producer_model=model or None,
    )
    return {
        "ok": True,
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "validation": validation,
        "config": str(provisioning.global_config_path()),
    }


def tune_in_hint(readiness: Dict[str, Any]) -> str:
    llm = readiness.get("llm", {}) if isinstance(readiness.get("llm"), dict) else {}
    provider = llm.get("provider") or "ollama"
    model = llm.get("model") or ""
    pieces = app_paths.opener_command(extra_args=["--tune-in", "--provider", str(provider)])
    if model:
        pieces.extend(["--model", str(model)])
    if provider == "ollama":
        pieces.append("--pull-model")
    return " ".join(quote_windows_arg(p) if " " in str(p) else str(p) for p in pieces)


def build_launch_env(
    extract_dir: Path,
    readiness: Dict[str, Any],
    *,
    headless: bool = False,
    local_audio: bool = False,
) -> Dict[str, str]:
    manifest = read_manifest(extract_dir)
    paths = manifest.get("paths") if isinstance(manifest.get("paths"), dict) else {}
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in readiness.get("env", {}).items() if v is not None})

    env["STATION_DIR"] = str(extract_dir)
    env["STATION_DB_PATH"] = str(station_path(extract_dir, paths.get("db"), "station.sqlite"))
    env["STATION_MEMORY_PATH"] = str(station_path(extract_dir, paths.get("memory"), "station_memory.json"))
    env["RADIO_OS_ROOT"] = str(BASE_DIR)

    package_plugins = extract_dir / "plugins"
    env["RADIO_OS_PLUGINS"] = str(package_plugins if package_plugins.exists() else BASE_DIR / "plugins")

    package_voices = extract_dir / "assets" / "voices"
    if package_voices.exists() and not env.get("RADIO_OS_VOICES"):
        env["RADIO_OS_VOICES"] = str(package_voices)
    else:
        env.setdefault("RADIO_OS_VOICES", str(BASE_DIR / "voices"))

    if readiness.get("piper", {}).get("bin"):
        env["PIPER_BIN"] = str(readiness["piper"]["bin"])

    if headless:
        env["RADIO_OS_HEADLESS"] = "1"
    else:
        env.pop("RADIO_OS_HEADLESS", None)
    if local_audio:
        env["RADIO_OS_LOCAL_AUDIO"] = "1"

    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def launch_loom_authoring() -> int:
    from loom import loom_studio

    loom_studio.main()
    return 0


def run_runtime_inprocess(
    extract_dir: Path,
    readiness: Dict[str, Any],
    *,
    headless: bool = False,
    local_audio: bool = False,
) -> int:
    env = build_launch_env(extract_dir, readiness, headless=headless, local_audio=local_audio)
    old_env = os.environ.copy()
    old_cwd = os.getcwd()
    try:
        os.environ.clear()
        os.environ.update(env)
        os.chdir(str(BASE_DIR))
        mod = importlib.import_module("oradio_runtime")
        mod = importlib.reload(mod)
        mod.main()
        return 0
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


def launch_descriptor_oradio(
    package_path: Path,
    *,
    gui_gate: bool = True,
    descriptor: Optional[Dict[str, Any]] = None,
) -> int:
    import loom_player_ui

    package_path = Path(package_path)
    descriptor = descriptor or loom_player_ui.read_descriptor(package_path)
    if gui_gate and not descriptor_club_gate.show_descriptor_club_gate(
        package_path,
        descriptor=descriptor,
    ):
        print("Descriptor playback deferred until the club is ready.", file=sys.stderr)
        return 3
    return loom_player_ui.main([str(package_path)])


def launch_oradio(
    package_path: Path,
    *,
    headless: bool = False,
    local_audio: bool = False,
    check_llm: bool = True,
    wait: bool = True,
    extract_dir: Optional[Path] = None,
    gui_gate: bool = False,
) -> int:
    # A descriptor-style .oradio (the Loom authoring path) is a plain YAML file, not a
    # zip package. Route those through the descriptor-first club gate and player path.
    if package_path.is_file() and not zipfile.is_zipfile(package_path):
        return launch_descriptor_oradio(package_path, gui_gate=gui_gate)

    if not RUNTIME_PATH.exists():
        print(f"Playback kernel not found: {RUNTIME_PATH}", file=sys.stderr)
        return 2

    package_path = Path(package_path)
    if extract_dir is None:
        extract_dir = Path(tempfile.mkdtemp(prefix="oradio_player_"))
    extract_dir.mkdir(parents=True, exist_ok=True)

    oradio_resolver.extract_oradio(package_path, extract_dir)
    readiness = oradio_resolver.resolve_station(package_path, extract_dir=extract_dir, check_llm=check_llm)
    print(oradio_resolver.readiness_report(readiness))
    if gui_gate and not headless:
        # Earnest first-run moment: ask once for whatever's missing (LLM/voices/piper AND antenna
        # targets), remember it, then re-check. The gate self-noops if nothing's needed; defensive —
        # no GUI ⇒ skipped, falling back to the CLI hints below.
        try:
            import club_gate
            if club_gate.show_club_gate(readiness, package_path):
                readiness = oradio_resolver.resolve_station(package_path, extract_dir=extract_dir, check_llm=check_llm)
        except Exception:
            pass

    if not readiness.get("ready"):
        blocking = readiness.get("blocking", [])
        print("\nThis station is getting tuned in — just a one-time setup, then every station reuses it.", file=sys.stderr)
        if any("llm" in str(b).lower() for b in blocking):
            print(f"  · LLM: {tune_in_hint(readiness)}", file=sys.stderr)
        if any("voice" in str(b).lower() for b in blocking):
            print("  · Voices: I couldn't find your voice models. Show me once and I'll remember:", file=sys.stderr)
            print(f"      {' '.join(app_paths.opener_command(extra_args=['--remember-voices', '\"<folder with your .onnx voices>\"']))}", file=sys.stderr)
        if any("piper" in str(b).lower() for b in blocking):
            print(f"      {' '.join(app_paths.opener_command(extra_args=['--remember-piper', '\"<path to piper>\"']))}", file=sys.stderr)
        return 3

    # Antennas: surface what the station listens to (non-blocking — silence is valid; only the LLM
    # hard-blocks), then POINT the running antennas at resolved/remembered targets (resolution → use).
    try:
        import antenna_resolver
        _manifest = read_manifest(extract_dir)
        _ant = antenna_resolver.resolve_station_antennas(_manifest)
        if _ant:
            _okn = sum(1 for r in _ant if r["status"] in antenna_resolver.OK_STATUSES)
            _na = sum(1 for r in _ant if r["status"] in antenna_resolver.NA_STATUSES)
            _needs = [r for r in _ant if r["status"] in antenna_resolver.PROBLEM_STATUSES]
            print(f"\nAntennas: {_okn} ready · {_na} n/a · {len(_needs)} need pointing")
            for r in _needs:
                print(f"  · {r['antenna']} [{r['kind']}]: {r['message']}")
                if r["status"] == "needs_target":
                    print(f"      point once: python antenna_resolver.py --remember \"{r['key']}\" \"<path on this machine>\"")
        # Rewrite the extracted manifest so the runtime watches the resolved path, not the baked one.
        _patched, _applied = antenna_resolver.apply_resolved_targets(_manifest)
        if _applied:
            (extract_dir / "manifest.yaml").write_text(
                yaml.safe_dump(_patched, sort_keys=False, allow_unicode=True), encoding="utf-8")
            print("  → pointed antenna(s) at resolved targets: " + "; ".join(_applied))
    except Exception:
        pass

    if app_paths.is_frozen() and not headless and wait:
        print(f"\nLaunching packaged runtime in-process from {extract_dir}")
        return run_runtime_inprocess(extract_dir, readiness, headless=headless, local_audio=local_audio)

    env = build_launch_env(extract_dir, readiness, headless=headless, local_audio=local_audio)
    cmd = [sys.executable, "-u", str(RUNTIME_PATH)]
    kwargs: Dict[str, Any] = {
        "cwd": str(BASE_DIR),
        "env": env,
    }
    if headless and wait:
        kwargs.update({
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        })
    elif headless:
        kwargs.update({
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        })
    elif sys.platform == "win32" and not os.environ.get("RADIO_OS_SHOW_CONSOLE"):
        try:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        except Exception:
            pass

    proc = subprocess.Popen(cmd, **kwargs)
    print(f"\nLaunched .oradio station from {extract_dir}")
    print(f"PID: {proc.pid}")

    if not wait:
        return 0

    try:
        if headless and proc.stdout:
            for line in proc.stdout:
                print(line, end="")
            return proc.wait()
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            return proc.wait(timeout=5)
        except Exception:
            proc.kill()
            return proc.wait()


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252; club summaries use ✓/✗
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Open a Radio OS .oradio station package.")
    parser.add_argument("package", type=Path, nargs="?", help="Path to a .oradio package")
    parser.add_argument("--headless", action="store_true", help="Run bookmark.py in headless mode")
    parser.add_argument("--local-audio", action="store_true", help="Enable local audio watcher in headless mode")
    parser.add_argument("--shell", action="store_true", help="Open the ambient Tk listener shell")
    parser.add_argument("--no-wait", action="store_true", help="Launch and return immediately")
    parser.add_argument("--skip-llm-check", action="store_true", help="Skip provisioning check for development smoke tests")
    parser.add_argument("--extract-dir", type=Path, help="Optional extraction directory")
    parser.add_argument("--print-windows-association", action="store_true", help="Print the per-user Windows .oradio association plan")
    parser.add_argument("--install-windows-association", action="store_true", help="Install per-user Windows .oradio double-click association")
    parser.add_argument("--tune-in", action="store_true", help="Validate and save machine-level LLM membership")
    parser.add_argument("--provider", choices=["ollama", "openai", "anthropic", "google"], help="Tune-In provider")
    parser.add_argument("--endpoint", help="Tune-In provider endpoint")
    parser.add_argument("--api-key", help="Tune-In hosted provider API key")
    parser.add_argument("--model", help="Tune-In host/producer model name")
    parser.add_argument("--pull-model", action="store_true", help="Pull the Ollama model before validating Tune-In")
    parser.add_argument("--save-without-validation", action="store_true", help="Save Tune-In settings without a live provider check")
    parser.add_argument("--remember-voices", metavar="DIR", help="Remember a voice-models folder machine-level (asset club) so every future .oradio finds it")
    parser.add_argument("--remember-piper", metavar="PATH", help="Remember a Piper binary machine-level (asset club)")
    parser.add_argument("--club-status", action="store_true", help="Show machine-level club status (LLM membership + remembered voices)")
    args = parser.parse_args(argv)

    if args.club_status:
        print("Radio OS — club status")
        print("  config:", provisioning.global_config_path())
        print(" ", provisioning.membership_summary())
        print(" ", provisioning.assets_summary())
        return 0

    if args.remember_voices:
        res = provisioning.save_voices_dir(args.remember_voices)
        if not res.get("ok"):
            print(f"Could not remember voices folder: {res.get('error')}", file=sys.stderr)
            return 2
        print(f"Remembered voices folder ({res.get('voice_files', 0)} model file(s)). Future stations will use it.")
        return 0

    if args.remember_piper:
        res = provisioning.save_piper_bin(args.remember_piper)
        if not res.get("ok"):
            print(f"Could not remember Piper binary: {res.get('error')}", file=sys.stderr)
            return 2
        print("Remembered Piper binary. Future stations will use it.")
        return 0

    if args.print_windows_association:
        print(format_association_plan(windows_association_plan()))
        return 0

    if args.install_windows_association:
        try:
            plan = install_windows_file_association()
        except Exception as exc:
            print(f"Could not install .oradio association: {exc}", file=sys.stderr)
            return 2
        print("Installed .oradio association for this Windows user.")
        print(format_association_plan(plan))
        return 0

    if args.tune_in:
        result = tune_in_membership(
            provider=args.provider,
            endpoint=args.endpoint,
            api_key=args.api_key,
            model=args.model,
            pull_model=args.pull_model,
            validate=not args.save_without_validation,
        )
        if not result.get("ok"):
            print(f"Tune-In failed: {result.get('error')}", file=sys.stderr)
            if result.get("needs_pull"):
                print("Hint: add --pull-model for Ollama, or pull the model manually.", file=sys.stderr)
            return 3
        print("Tune-In saved. You are in the club.")
        print(f"  provider: {result['provider']}")
        print(f"  model: {result.get('model') or '(not set)'}")
        print(f"  config: {result['config']}")
        return 0

    if args.shell:
        if args.package is None:
            parser.error("package is required for --shell")
        import oradio_player_ui
        return oradio_player_ui.main([str(args.package)])

    if args.package is None:
        return launch_loom_authoring()

    return launch_oradio(
        args.package,
        headless=args.headless,
        local_audio=args.local_audio,
        check_llm=not args.skip_llm_check,
        wait=not args.no_wait,
        extract_dir=args.extract_dir,
        gui_gate=not args.headless,  # interactive open ⇒ show the earnest club gate when not ready
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        if app_paths.is_frozen():
            app_paths.append_packaged_error(traceback.format_exc())
        raise
