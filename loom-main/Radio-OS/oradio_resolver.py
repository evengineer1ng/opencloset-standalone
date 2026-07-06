"""
Radio OS — .oradio resolution ladder ("are we ready to broadcast, or do we need to tune in?").

Given a packaged .oradio, this resolves every declared requirement down the ladder:

    bundled (inside the package)  ->  machine cache (located/fetched)  ->  provisioned (LLM club)

There is NO degrade tier. Live LLM narration is the medium: if the LLM isn't provisioned, the
station is "getting tuned in", not "running as a lesser station". This module returns a readiness
report; the (future, forked) player consumes it to either launch or show the first-run Tune-In gate.

Stdlib-only and standalone-testable, in the spirit of provisioning.py / antenna_http.py:

    python oradio_resolver.py path/to/Station.oradio

It reuses provisioning.py for the LLM gate and touches no preserved runtime file.
"""

from __future__ import annotations

import json
import os
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import provisioning

VOICES_ENV = "RADIO_OS_VOICES"


# ----------------------------------------------------------------------------
# Reading the package
# ----------------------------------------------------------------------------
def load_oradio(path: Path) -> Dict[str, Any]:
    """Read the descriptor + requirements + lock from a .oradio without extracting."""
    path = Path(path)
    out: Dict[str, Any] = {"oradio": {}, "requirements": {}, "lock": {}, "names": []}
    with zipfile.ZipFile(path) as zf:
        out["names"] = zf.namelist()
        for key, name in (("oradio", "oradio.json"), ("requirements", "requirements.json"), ("lock", "requirements.lock.json")):
            if name in out["names"]:
                try:
                    out[key] = json.loads(zf.read(name) or b"{}")
                except Exception:
                    out[key] = {}
    return out


def extract_oradio(path: Path, dest: Optional[Path] = None) -> Path:
    dest = dest or Path(tempfile.mkdtemp(prefix="oradio_"))
    with zipfile.ZipFile(path) as zf:
        zf.extractall(dest)
    return dest


# ----------------------------------------------------------------------------
# Asset resolution
# ----------------------------------------------------------------------------
def _machine_voice_dirs() -> List[Path]:
    dirs: List[Path] = []
    env = os.environ.get(VOICES_ENV, "")
    if env:
        # The env var may carry several dirs (os.pathsep-joined by the player/resolver).
        dirs.extend(Path(part) for part in env.split(os.pathsep) if part)
    # Machine-level "asset club": directories the user has shown us once and we remember forever.
    try:
        dirs.extend(Path(d) for d in provisioning.get_voices_dirs())
    except Exception:
        pass
    dirs.append(Path.cwd() / "voices")
    seen: set = set()
    unique = []
    for d in dirs:
        if d.exists() and str(d) not in seen:
            seen.add(str(d))
            unique.append(d)
    return unique


def _find_in_machine_voices(ref: str) -> Optional[Path]:
    name = Path(ref).name
    for base in _machine_voice_dirs():
        cand = base / name
        if cand.is_file():
            return cand
        hits = list(base.rglob(name))
        if hits:
            return hits[0]
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_piper() -> str:
    # A Piper binary the user showed us once and we remembered machine-level (asset club).
    try:
        remembered = provisioning.get_piper_bin()
        if remembered:
            return remembered
    except Exception:
        pass
    found = shutil.which("piper")
    if found:
        return found
    names = ("piper.exe", "piper") if os.name == "nt" else ("piper",)
    for base in _machine_voice_dirs():
        for n in names:
            hits = list(base.rglob(n))
            if hits:
                return str(hits[0])
    return ""


def resolve_voices(lock: Dict[str, Any], extract_dir: Optional[Path]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for v in lock.get("voices", []) if isinstance(lock.get("voices"), list) else []:
        role = v.get("role", "")
        ref = v.get("ref", "")
        entry = {"role": role, "ref": ref, "source": None, "path": None, "ok": False, "note": ""}
        if v.get("bundled") and v.get("arcname"):
            # Tier 1: bundled in the package.
            p = (extract_dir / v["arcname"]) if extract_dir else None
            if p and p.is_file():
                entry.update(source="bundled", path=str(p), ok=True)
                expected_bytes = v.get("bytes")
                expected_sha = v.get("sha256")
                if expected_bytes is not None and p.stat().st_size != int(expected_bytes):
                    entry.update(ok=False, note="bundled file size does not match requirements.lock.json")
                elif expected_sha and _sha256(p) != expected_sha:
                    entry.update(ok=False, note="bundled file sha256 does not match requirements.lock.json")
                elif p.stat().st_size == 0:
                    entry.update(ok=False, note="bundled file is 0 bytes (re-fetch the source voice)")
            else:
                entry["note"] = "declared bundled but missing from package"
        else:
            # Tier 2: machine cache.
            hit = _find_in_machine_voices(ref)
            if hit:
                entry.update(source="machine-cache", path=str(hit), ok=True)
            else:
                entry.update(source="needs-fetch", note="voice not bundled and not in machine cache")
        results.append(entry)
    return results


def resolve_sfx(lock: Dict[str, Any], extract_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """Resolve declared SFX. Production seasoning — integrity is checked but never blocks
    broadcast (a missing/altered stinger degrades polish, not the medium)."""
    results: List[Dict[str, Any]] = []
    for s in lock.get("sfx", []) if isinstance(lock.get("sfx"), list) else []:
        tag = s.get("tag", "")
        ref = s.get("ref", "")
        entry = {"tag": tag, "ref": ref, "source": None, "path": None, "ok": False, "note": ""}
        if s.get("bundled") and s.get("arcname"):
            p = (extract_dir / s["arcname"]) if extract_dir else None
            if p and p.is_file():
                entry.update(source="bundled", path=str(p), ok=True)
                expected_bytes = s.get("bytes")
                expected_sha = s.get("sha256")
                if expected_bytes is not None and p.stat().st_size != int(expected_bytes):
                    entry.update(ok=False, note="bundled sfx size does not match requirements.lock.json")
                elif expected_sha and _sha256(p) != expected_sha:
                    entry.update(ok=False, note="bundled sfx sha256 does not match requirements.lock.json")
            else:
                entry["note"] = "declared bundled but missing from package"
        else:
            entry.update(source="needs-fetch", note="sfx sourced/fetched on target (not bundled)")
        results.append(entry)
    return results


# ----------------------------------------------------------------------------
# Full station resolution
# ----------------------------------------------------------------------------
def resolve_station(path: Path, *, extract_dir: Optional[Path] = None, check_llm: bool = True) -> Dict[str, Any]:
    path = Path(path)
    pkg = load_oradio(path)
    lock = pkg["lock"] or {}
    if extract_dir is None:
        extract_dir = extract_oradio(path)

    voices = resolve_voices(lock, extract_dir)
    blocking: List[str] = []

    for v in voices:
        if not v["ok"]:
            blocking.append(f"voice '{v['role']}' unavailable ({v.get('note') or v['source']})")

    piper_req = lock.get("piper", {}) if isinstance(lock.get("piper"), dict) else {}
    piper_bin = ""
    if piper_req.get("needed"):
        piper_bin = _detect_piper()
        if not piper_bin:
            blocking.append("piper binary not found on this machine (machine-cache resolve)")

    llm_status: Dict[str, Any] = {}
    llm_req = lock.get("llm", {}) if isinstance(lock.get("llm"), dict) else {}
    if check_llm and llm_req.get("required", True):
        llm_status = provisioning.provisioning_status()
        if not llm_status.get("ready"):
            blocking.append(
                f"LLM not provisioned ({llm_status.get('provider')}: {llm_status.get('error')}) "
                f"— run first-run Tune-In"
            )

    # SFX is production seasoning: report integrity as warnings, never block.
    sfx = resolve_sfx(lock, extract_dir)
    warnings: List[str] = [f"sfx '{s['tag']}': {s['note']}" for s in sfx if not s["ok"] and s.get("note")]

    # Runtime env the (forked) player would launch with: prefer the bundled voices/sfx.
    env: Dict[str, str] = {}
    bundled_voice_dir = extract_dir / "assets" / "voices"
    if bundled_voice_dir.exists():
        existing = os.environ.get(VOICES_ENV, "")
        env[VOICES_ENV] = os.pathsep.join([str(bundled_voice_dir)] + ([existing] if existing else []))
    bundled_sfx_dir = extract_dir / "assets" / "sfx"
    if bundled_sfx_dir.exists():
        existing_sfx = os.environ.get("RADIO_OS_SFX", "")
        env["RADIO_OS_SFX"] = os.pathsep.join([str(bundled_sfx_dir)] + ([existing_sfx] if existing_sfx else []))

    return {
        "station": pkg["oradio"].get("station_name") or pkg["oradio"].get("station_id") or path.stem,
        "extract_dir": str(extract_dir),
        "narration": lock.get("narration", "live_llm"),
        "ready": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "voices": voices,
        "sfx": sfx,
        "piper": {"needed": bool(piper_req.get("needed")), "bin": piper_bin},
        "llm": llm_status or {"required": llm_req.get("required", True), "checked": check_llm},
        "env": env,
    }


def readiness_report(res: Dict[str, Any]) -> str:
    lines = [f"Station: {res['station']}   ({res['narration']})"]
    lines.append("READY TO BROADCAST ✓" if res["ready"] else "NOT READY — getting tuned in:")
    for b in res["blocking"]:
        lines.append(f"  ✗ {b}")
    for v in res["voices"]:
        mark = "✓" if v["ok"] else "✗"
        note = f"  [{v['note']}]" if v.get("note") else ""
        lines.append(f"  {mark} voice {v['role']}: {v['source']}{note}")
    if res["piper"]["needed"]:
        lines.append(f"  {'✓' if res['piper']['bin'] else '✗'} piper: {res['piper']['bin'] or 'not found'}")
    llm = res["llm"]
    if "ready" in llm:
        lines.append(f"  {'✓' if llm['ready'] else '✗'} llm: {llm.get('provider')} {'ready' if llm.get('ready') else llm.get('error')}")
    for s in res.get("sfx", []):
        mark = "✓" if s["ok"] else "•"
        note = f"  [{s['note']}]" if s.get("note") else ""
        lines.append(f"  {mark} sfx {s['tag']}: {s['source']}{note}")
    for w in res.get("warnings", []):
        lines.append(f"  • warning: {w}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("usage: python oradio_resolver.py path/to/Station.oradio")
        raise SystemExit(2)
    res = resolve_station(Path(sys.argv[1]))
    print(readiness_report(res))
    print("\nlaunch env:", res["env"])
