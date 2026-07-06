"""Radio OS — antenna target resolution (the antenna club).

A station's antenna points at something on THIS machine or network — a game folder, a log file, an
HTTP service, an RSS feed, a UDP port, a command. The club's job here mirrors what it does for voices:

    1. Try, in good faith, to FIND the target automatically (most are sane to track down).
    2. If we can't, ask once and REMEMBER where the user points us (paths vary by machine), so a
       re-shared .oradio "just works" forever after one pointing — keyed by a stable antenna key.

Crucially, **every antenna shape resolves and FAILS differently**, so this is per-kind:

    folder   — a directory (e.g. a game install). Auto: remembered → explicit → env → Steam library
               → registry/common roots → by-name. Worst case: needs_target (ask once).
    file     — a specific file (e.g. a log). Same ladder, file-shaped.
    http     — a URL. Auto: reachability check. Not found ≠ missing file; it usually means the service
               isn't running (a process/launch problem, not a path problem).
    rss      — a feed URL. Not found means the feed moved/died — a content problem, not your machine.
    port     — a UDP/TCP port. "Nothing there yet" is often fine (we may bind it ourselves).
    command  — an executable on PATH.

Stdlib only, standalone-testable; persistence rides provisioning.py (the same global club config).
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import provisioning


# ---------------------------------------------------------------------------
# Antenna identity + target inference
# ---------------------------------------------------------------------------
def antenna_key(name: str, cfg: Dict[str, Any]) -> str:
    """A stable key to remember a target under. Explicit `target.key` wins; else plugin+name."""
    target = cfg.get("target") if isinstance(cfg.get("target"), dict) else {}
    if target.get("key"):
        return str(target["key"])
    plugin = str(cfg.get("plugin") or "feed")
    return f"{plugin}:{name}"


def infer_target(name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Work out what an antenna is pointing at. An explicit `target` block wins; otherwise we infer
    the shape from the feed config / plugin so existing stations resolve without re-authoring."""
    explicit = cfg.get("target") if isinstance(cfg.get("target"), dict) else None
    if explicit and explicit.get("kind"):
        return dict(explicit)

    plugin = str(cfg.get("plugin") or "").lower()
    # URL-shaped
    url = cfg.get("url") or cfg.get("base_url") or cfg.get("endpoint")
    if url and ("rss" in plugin or "feed" in plugin):
        return {"kind": "rss", "url": str(url)}
    if url:
        return {"kind": "http", "url": str(url)}
    if "rss" in plugin and (cfg.get("feeds") or cfg.get("feed")):
        feed = cfg.get("feed") or (cfg.get("feeds") or [None])[0]
        return {"kind": "rss", "url": str(feed)}
    # files-shaped (e.g. a document watcher watching specific files)
    files = cfg.get("files")
    if isinstance(files, list) and files:
        return {"kind": "files", "files": [str(f) for f in files], "source_key": "files"}
    # port-shaped
    port = cfg.get("port") or cfg.get("udp_port")
    if port:
        return {"kind": "port", "port": int(port)}
    # command-shaped
    if cfg.get("command") or cfg.get("cmd"):
        return {"kind": "command", "command": str(cfg.get("command") or cfg.get("cmd"))}
    # path-shaped (folder or file) — scout/bridge/file_watch/log_tail. `source_key` records WHICH
    # config key the target lives under, so a resolved/remembered path can be written back there.
    for k in ("bridge_dir", "scope", "watch_dir", "folder", "path"):
        raw = cfg.get(k)
        if raw:
            p = Path(str(raw))
            kind = "file" if (p.suffix and not p.is_dir()) else "folder"
            return {"kind": kind, "path": str(raw), "names": [p.name] if p.name else [], "source_key": k}
    # credential feeds (social/APIs): not a path to FIND — a one-time key/login (the key club's job)
    cred_keys = {"password", "access_token", "api_key", "apikey", "token", "secret", "identifier", "page_id"}
    if cred_keys & set(cfg.keys()):
        return {"kind": "credential"}
    # everything else (hashtags / symbols / keywords / internal feeds): no machine-local target
    return {"kind": "no_target"}


# ---------------------------------------------------------------------------
# Folder/game auto-discovery (the Fallout case)
# ---------------------------------------------------------------------------
def _steam_path() -> Optional[Path]:
    if os.name == "nt":
        try:
            import winreg
            probes = (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            )
            for hive, key, val_name in probes:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        val, _ = winreg.QueryValueEx(k, val_name)
                        if val and Path(val).exists():
                            return Path(val)
                except OSError:
                    continue
        except Exception:
            pass
    for c in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
              os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/Library/Application Support/Steam")):
        if Path(c).exists():
            return Path(c)
    return None


def steam_libraries() -> List[Path]:
    sp = _steam_path()
    libs: List[Path] = []
    if not sp:
        return libs
    base = sp / "steamapps"
    if base.exists():
        libs.append(base)
    vdf = base / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                p = Path(m.group(1).replace("\\\\", "\\")) / "steamapps"
                if p.exists() and p not in libs:
                    libs.append(p)
        except Exception:
            pass
    return libs


def find_game_folder(names: List[str], appid: Optional[str] = None) -> Optional[Path]:
    """Look for a game install by Steam appmanifest installdir / common/<name>, then common roots."""
    names = [n for n in names if n]
    for lib in steam_libraries():
        if appid:
            acf = lib / f"appmanifest_{appid}.acf"
            if acf.is_file():
                try:
                    m = re.search(r'"installdir"\s*"([^"]+)"', acf.read_text(encoding="utf-8", errors="ignore"))
                    if m:
                        cand = lib / "common" / m.group(1)
                        if cand.exists():
                            return cand
                except Exception:
                    pass
        for nm in names:
            cand = lib / "common" / nm
            if cand.exists():
                return cand
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
             Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
    for root in roots:
        for nm in names:
            cand = root / nm
            if cand.exists():
                return cand
    return None


def _ok(target_kind: str, status: str, message: str, **extra) -> Dict[str, Any]:
    out = {"kind": target_kind, "status": status, "message": message}
    out.update(extra)
    return out


def resolve_folder(target: Dict[str, Any], key: str, *, want_file: bool = False) -> Dict[str, Any]:
    kind = "file" if want_file else "folder"
    remembered = provisioning.get_antenna_target(key)
    if remembered:
        return _ok(kind, "found", "Remembered from a previous machine pointing.", path=remembered, source="remembered")
    explicit = target.get("path")
    if explicit and Path(explicit).exists():
        return _ok(kind, "found", "Found at the declared path.", path=str(Path(explicit)), source="declared")
    env_var = target.get("env")
    if env_var and os.environ.get(env_var) and Path(os.environ[env_var]).exists():
        return _ok(kind, "found", f"Found via ${env_var}.", path=os.environ[env_var], source="env")
    if not want_file:
        names = target.get("names") or ([Path(explicit).name] if explicit else [])
        hit = find_game_folder(list(names), appid=str(target.get("steam_appid")) if target.get("steam_appid") else None)
        if hit:
            return _ok(kind, "found", "Auto-discovered (Steam library / common roots).", path=str(hit), source="auto")
    return _ok(kind, "needs_target",
               f"Couldn't find this {kind} automatically — point me once and I'll remember it.",
               hint=target.get("path") or (target.get("names") or [""])[0], key=key)


def resolve_http(target: Dict[str, Any]) -> Dict[str, Any]:
    url = str(target.get("url") or "")
    if not url:
        return _ok("http", "unknown", "No URL declared for this antenna.")
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "RadioOS-antenna"})
        with urllib.request.urlopen(req, timeout=4) as resp:  # noqa: S310
            return _ok("http", "found", f"Reachable (HTTP {resp.status}).", url=url)
    except Exception as exc:
        # Not a path problem — the service is likely down / not launched.
        return _ok("http", "unreachable",
                   "The endpoint isn't responding. Is the app/service that serves it running?",
                   url=url, detail=str(exc))


def resolve_rss(target: Dict[str, Any]) -> Dict[str, Any]:
    url = str(target.get("url") or "")
    if not url:
        return _ok("rss", "unknown", "No feed URL declared.")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "RadioOS-antenna"}), timeout=5) as resp:  # noqa: S310
            head = resp.read(512).lower()
        looks_feed = b"<rss" in head or b"<feed" in head or b"<?xml" in head
        return _ok("rss", "found" if looks_feed else "found_unverified",
                   "Feed reachable." if looks_feed else "URL reachable but didn't look like a feed.", url=url)
    except Exception as exc:
        return _ok("rss", "feed_unreachable",
                   "This feed didn't respond — it may have moved or been retired (a content issue, not your machine).",
                   url=url, detail=str(exc))


def resolve_port(target: Dict[str, Any]) -> Dict[str, Any]:
    port = int(target.get("port") or 0)
    if not port:
        return _ok("port", "unknown", "No port declared.")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        listening = s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()
    if listening:
        return _ok("port", "found", f"Something is already serving 127.0.0.1:{port}.", port=port)
    return _ok("port", "port_free", f"Nothing on :{port} yet — fine if the antenna binds it itself.", port=port)


def resolve_command(target: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(target.get("command") or "")
    exe = cmd.split()[0] if cmd else ""
    found = shutil.which(exe) if exe else ""
    if found:
        return _ok("command", "found", "Command is on PATH.", command=cmd, path=found)
    return _ok("command", "missing_command", f"'{exe}' isn't on PATH.", command=cmd)


def resolve_files(target: Dict[str, Any], key: str) -> Dict[str, Any]:
    files = [str(f) for f in (target.get("files") or [])]
    existing = [f for f in files if Path(f).exists()]
    if files and len(existing) == len(files):
        return _ok("files", "found", f"All {len(files)} watched file(s) present.", paths=existing)
    if existing:
        return _ok("files", "partial", f"{len(existing)}/{len(files)} watched files found.",
                   paths=existing, missing=[f for f in files if f not in existing], key=key)
    return _ok("files", "needs_target", "None of the watched files are here — point me to them.", key=key, files=files)


# Statuses that are fine (✓) vs. genuinely need attention (✗) vs. not-applicable (·).
OK_STATUSES = {"found", "found_unverified", "port_free", "partial"}
NA_STATUSES = {"credential", "no_target"}
PROBLEM_STATUSES = {"needs_target", "unreachable", "feed_unreachable", "missing_command", "unknown"}


def resolve_target(target: Dict[str, Any], key: str) -> Dict[str, Any]:
    kind = str(target.get("kind") or "unknown")
    if kind == "folder":
        return resolve_folder(target, key)
    if kind == "file":
        return resolve_folder(target, key, want_file=True)
    if kind == "files":
        return resolve_files(target, key)
    if kind == "http":
        return resolve_http(target)
    if kind == "rss":
        return resolve_rss(target)
    if kind == "port":
        return resolve_port(target)
    if kind == "command":
        return resolve_command(target)
    if kind == "credential":
        return _ok("credential", "credential",
                   "Signs in with an API key/login — a one-time club key setup, not a path to find.")
    if kind == "no_target":
        return _ok("no_target", "no_target", "No machine-local target to locate (a query or internal feed).")
    return _ok("unknown", "unknown", "This antenna's target shape isn't recognized yet.")


def resolve_station_antennas(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-antenna readiness for a station manifest. Each result carries its key so the gate can
    offer 'point me' and persist via remember_antenna_target()."""
    feeds = manifest.get("feeds") if isinstance(manifest.get("feeds"), dict) else {}
    out: List[Dict[str, Any]] = []
    for name, cfg in feeds.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue
        key = antenna_key(name, cfg)
        target = infer_target(name, cfg)
        result = resolve_target(target, key)
        result["antenna"] = name
        result["key"] = key
        out.append(result)
    return out


def remember_antenna_target(key: str, path: Any) -> Dict[str, Any]:
    """Persist where an antenna's target lives on this machine (the antenna club)."""
    return provisioning.save_antenna_target(key, path)


def apply_resolved_targets(manifest: Dict[str, Any]) -> tuple:
    """Resolution → use. Return (patched_manifest, applied) where each path-shaped antenna whose
    target we found/remembered has its config key rewritten to the resolved path — so the RUNNING
    antenna watches the right place instead of the (possibly dead) path baked into the artifact.

    Only folder/file antennas are patched (the ones that move per machine). http/rss/port/command/
    credential/no_target are left alone. Pure: deep-copies, never mutates the input."""
    import copy
    patched = copy.deepcopy(manifest)
    feeds = patched.get("feeds") if isinstance(patched.get("feeds"), dict) else {}
    applied: List[str] = []
    for name, cfg in feeds.items():
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        target = infer_target(name, cfg)
        source_key = target.get("source_key")
        if target.get("kind") not in ("folder", "file") or not source_key:
            continue
        result = resolve_target(target, antenna_key(name, cfg))
        if result.get("status") == "found" and result.get("path"):
            if str(cfg.get(source_key)) != str(result["path"]):
                cfg[source_key] = result["path"]
                applied.append(f"{name} → {result['path']}")
    return patched, applied


def _manifest_from(path: Path) -> Dict[str, Any]:
    import yaml
    if path.is_dir():
        mp = path / "manifest.yaml"
        return (yaml.safe_load(mp.read_text(encoding="utf-8")) or {}) if mp.is_file() else {}
    import zipfile
    with zipfile.ZipFile(path) as zf:  # zipfile tolerates the polyglot JPEG prefix
        if "manifest.yaml" in zf.namelist():
            return yaml.safe_load(zf.read("manifest.yaml")) or {}
    return {}


def _main(argv: List[str]) -> int:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if argv and argv[0] == "--remember" and len(argv) >= 3:
        res = remember_antenna_target(argv[1], argv[2])
        if not res.get("ok"):
            print(f"Could not remember target: {res.get('error')}", file=sys.stderr)
            return 2
        print(f"Remembered antenna target '{argv[1]}' → {res['path']}. Future stations reuse it.")
        return 0

    if not argv or argv[0] == "--demo":
        print("Radio OS — antenna resolver")
        print("  steam libraries:", [str(p) for p in steam_libraries()] or "none found")
        demo = {"feeds": {
            "game": {"plugin": "antenna_bridge", "target": {"kind": "folder", "names": ["Fallout4", "Fallout 4"], "steam_appid": "377160", "key": "game:fallout4"}},
            "weather": {"plugin": "antenna_http", "url": "http://127.0.0.1:9/nope"},
        }}
        for r in resolve_station_antennas(demo):
            print(f"  · {r['antenna']} [{r['kind']}] → {r['status']}: {r['message']}")
        print("\nUsage: python antenna_resolver.py <station.oradio|station_dir>")
        print("       python antenna_resolver.py --remember <key> <path>")
        return 0

    target = Path(argv[0])
    manifest = _manifest_from(target)
    results = resolve_station_antennas(manifest)
    if not results:
        print("No antennas declared in this station.")
        return 0
    print(f"Antenna readiness for {target.name}:")
    needs = 0
    for r in results:
        mark = "✓" if r["status"] in OK_STATUSES else ("·" if r["status"] in NA_STATUSES else "✗")
        print(f"  {mark} {r['antenna']} [{r['kind']}] → {r['status']}: {r['message']}")
        if r.get("path"):
            print(f"      → {r['path']}")
        if r["status"] == "needs_target":
            needs += 1
            print(f"      point once:  python antenna_resolver.py --remember \"{r['key']}\" \"<path on this machine>\"")
    print(f"\n{sum(1 for r in results if r['status'] in OK_STATUSES)} ready · "
          f"{sum(1 for r in results if r['status'] in NA_STATUSES)} n/a · {needs} need pointing")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
