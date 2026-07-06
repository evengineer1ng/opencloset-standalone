"""
Radio OS — `.oradio` format validator (v1).

Answers one question: **is this cloth well-woven?** — i.e. is the package structurally a valid
`.oradio`, and is it internally consistent (does it reference things that actually exist inside it)?

This is deliberately SEPARATE from `oradio_resolver.py`:
  * `oradio_validate` — *format validity* (is the artifact well-formed and self-consistent?).
  * `oradio_resolver` — *machine readiness* (can THIS machine play it — club LLM, voices, piper?).

Stdlib + PyYAML, polyglot-tolerant (a JPEG-prefixed `.oradio` still validates), standalone:

    python oradio_validate.py path/to/Station.oradio

Exit code 0 if valid (no errors), 1 if invalid. Warnings never fail validation — they flag
portability or polish risks, not malformed packages.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── the v1 contract ──────────────────────────────────────────────────────────
REQUIRED_MEMBERS = ("oradio.json", "manifest.yaml", "requirements.json", "requirements.lock.json")
OPTIONAL_MEMBERS = ("signature.json", "meta_plugin_spec.json", "cover.jpg", "broadcast_grammar.py")
KNOWN_FORMAT_VERSIONS = {"0.1", "1.0"}
EXPECTED_NARRATION = "live_llm"


def _read_json(zf: zipfile.ZipFile, name: str, names: set) -> Optional[Any]:
    if name not in names:
        return None
    try:
        return json.loads(zf.read(name) or b"{}")
    except Exception:
        return "__INVALID__"  # sentinel: present but unparseable


def _read_yaml(zf: zipfile.ZipFile, name: str, names: set) -> Optional[Any]:
    if name not in names:
        return None
    try:
        import yaml
        return yaml.safe_load(zf.read(name))
    except Exception:
        return "__INVALID__"


def _is_polyglot(path: Path) -> bool:
    """A plain zip starts with 'PK'; anything else (e.g. a JPEG 'FFD8' prefix) is a polyglot."""
    try:
        with open(path, "rb") as f:
            return f.read(2) != b"PK"
    except OSError:
        return False


def validate_oradio(path: Any) -> Dict[str, Any]:
    """Validate a `.oradio` package. Returns {ok, errors[], warnings[], info{}}."""
    path = Path(path)
    report: Dict[str, Any] = {"ok": False, "path": str(path), "errors": [], "warnings": [], "info": {}}
    E = report["errors"].append
    W = report["warnings"].append
    info = report["info"]

    if not path.is_file():
        E(f"no such file: {path}")
        return report

    try:
        zf = zipfile.ZipFile(path)
    except Exception as exc:
        E(f"not a readable .oradio package (zip): {exc}")
        return report

    with zf:
        names = set(zf.namelist())
        info["member_count"] = len(names)
        info["is_polyglot"] = _is_polyglot(path)
        info["has_cover"] = "cover.jpg" in names

        # 1) required members
        for req in REQUIRED_MEMBERS:
            if req not in names:
                E(f"missing required file: {req}")

        # 2) oradio.json (the descriptor)
        desc = _read_json(zf, "oradio.json", names)
        if desc == "__INVALID__":
            E("oradio.json is not valid JSON")
            desc = None
        if isinstance(desc, dict):
            if desc.get("format") != "oradio":
                E(f"oradio.json: format must be 'oradio' (got {desc.get('format')!r})")
            fv = str(desc.get("format_version") or "")
            info["format_version"] = fv or "(unset)"
            if fv and fv not in KNOWN_FORMAT_VERSIONS:
                W(f"oradio.json: unrecognized format_version {fv!r} (known: {sorted(KNOWN_FORMAT_VERSIONS)})")
            elif not fv:
                W("oradio.json: missing format_version")
            if not str(desc.get("station_id") or "").strip():
                W("oradio.json: missing station_id")
            info["station"] = desc.get("station_name") or desc.get("station_id") or "(unnamed)"
            entry = desc.get("entry") if isinstance(desc.get("entry"), dict) else {}
            entry_manifest = str(entry.get("manifest") or "manifest.yaml")
            if entry_manifest and entry_manifest not in names:
                E(f"oradio.json: entry.manifest points to missing member '{entry_manifest}'")
            meta = desc.get("metadata") if isinstance(desc.get("metadata"), dict) else {}
            artwork = str(meta.get("artwork") or "").strip()
            if artwork and artwork not in names:
                W(f"oradio.json: metadata.artwork '{artwork}' declared but not bundled")
            info["title"] = meta.get("title") if meta else None

        # 3) manifest.yaml (the station)
        man = _read_yaml(zf, "manifest.yaml", names)
        if man == "__INVALID__":
            E("manifest.yaml is not valid YAML")
            man = None
        if isinstance(man, dict):
            station = man.get("station") if isinstance(man.get("station"), dict) else {}
            if not str(station.get("id") or "").strip():
                W("manifest.yaml: missing station.id")
            if not str(station.get("name") or "").strip():
                W("manifest.yaml: missing station.name")
            mp = str(man.get("meta_plugin") or "").strip()
            if mp and f"plugins/meta/{mp}.py" not in names:
                W(f"manifest: meta_plugin '{mp}' not bundled (plugins/meta/{mp}.py) — relies on host install")
            feeds = man.get("feeds") if isinstance(man.get("feeds"), dict) else {}
            for fname, fcfg in feeds.items():
                if not isinstance(fcfg, dict) or fcfg.get("enabled") is False:
                    continue
                plug = str(fcfg.get("plugin") or "").strip()
                if plug and f"plugins/{plug}.py" not in names:
                    W(f"manifest: feed '{fname}' plugin '{plug}' not bundled (plugins/{plug}.py) — relies on host install")
        elif man is None and "manifest.yaml" in names:
            E("manifest.yaml is empty or not a mapping")

        # 4) requirements.json (the capability contract)
        req = _read_json(zf, "requirements.json", names)
        if req == "__INVALID__":
            E("requirements.json is not valid JSON")
            req = None
        if isinstance(req, dict):
            if req.get("narration") != EXPECTED_NARRATION:
                W(f"requirements: narration is {req.get('narration')!r}, expected '{EXPECTED_NARRATION}' (live LLM is the medium)")
            for sec in ("llm", "voices", "piper"):
                if sec not in req:
                    W(f"requirements.json: missing section '{sec}'")

        # 5) requirements.lock.json (resolved at export) — internal consistency is the heart of validity
        lock = _read_json(zf, "requirements.lock.json", names)
        if lock == "__INVALID__":
            E("requirements.lock.json is not valid JSON")
            lock = None
        if isinstance(lock, dict):
            if not str(lock.get("lock_version") or "").strip():
                W("lock: missing lock_version")
            bundled_voices = 0
            for sec in ("voices", "sfx", "art"):
                rows = lock.get(sec) if isinstance(lock.get(sec), list) else []
                for item in rows:
                    if not isinstance(item, dict) or not item.get("bundled"):
                        continue
                    arc = str(item.get("arcname") or "")
                    if not arc or arc not in names:
                        E(f"lock: {sec} entry claims bundled but member '{arc or '(no arcname)'}' is missing from package")
                    else:
                        if sec == "voices":
                            bundled_voices += 1
                        if not (item.get("sha256") or item.get("bytes")):
                            W(f"lock: bundled {sec} '{arc}' has no integrity (sha256/bytes)")
            info["bundled_voices"] = bundled_voices
            unresolved = lock.get("unresolved") if isinstance(lock.get("unresolved"), list) else []
            if unresolved:
                info["unresolved"] = list(unresolved)

    report["ok"] = not report["errors"]
    return report


def format_report(report: Dict[str, Any]) -> str:
    lines = [f"Validating: {report['path']}"]
    info = report.get("info", {})
    if info.get("station"):
        poly = " · polyglot(image)" if info.get("is_polyglot") else ""
        cover = " · cover" if info.get("has_cover") else ""
        lines.append(f"  {info['station']}  (format {info.get('format_version', '?')}, "
                     f"{info.get('member_count', '?')} files, {info.get('bundled_voices', 0)} bundled voices){cover}{poly}")
    lines.append("VALID ✓" if report["ok"] else "INVALID ✗")
    for e in report["errors"]:
        lines.append(f"  ✗ {e}")
    for w in report["warnings"]:
        lines.append(f"  • warning: {w}")
    for u in info.get("unresolved", []):
        lines.append(f"  · note (resolves on target): {u}")
    return "\n".join(lines)


def main(argv) -> int:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not argv:
        print("usage: python oradio_validate.py path/to/Station.oradio")
        return 2
    report = validate_oradio(argv[0])
    print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
