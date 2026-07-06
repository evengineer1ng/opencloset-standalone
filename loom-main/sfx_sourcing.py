"""
Radio OS — SFX sourcing (asset acquisition is just another antenna-shaped problem).

Declared remote SFX (e.g. `{tag: rain, ref: "rain ambience", source: freesound}`) are resolved here:
search a royalty-free provider, download a preview-quality clip into a cache, and hand the local
files back so the exporter can bundle them into the `.oradio` (keeping the shipped artifact
self-contained). Stdlib-only and standalone-testable, like provisioning.py / antenna_http.py.

The Freesound API key is read from the shared global config under `asset_sources.freesound.api_key`
(or the FREESOUND_API_KEY env var). No key → graceful "declared, not fetched" (never fatal — SFX is
seasoning, not the medium).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import provisioning  # shared global config


def freesound_api_key(config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config if config is not None else provisioning.read_global_config()
    sources = cfg.get("asset_sources", {}) if isinstance(cfg.get("asset_sources"), dict) else {}
    fs = sources.get("freesound", {}) if isinstance(sources.get("freesound"), dict) else {}
    return str(fs.get("api_key") or os.environ.get("FREESOUND_API_KEY", "")).strip()


def freesound_search(query: str, key: str, *, max_results: int = 3, timeout: float = 10.0) -> Dict[str, Any]:
    if not key:
        return {"ok": False, "error": "no Freesound API key", "results": []}
    params = urllib.parse.urlencode({
        "query": query, "fields": "id,name,previews", "page_size": max(1, max_results), "token": key,
    })
    url = f"https://freesound.org/apiv2/search/text/?{params}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read() or b"{}")
        results: List[Dict[str, Any]] = []
        for r in data.get("results", []):
            previews = r.get("previews", {}) if isinstance(r.get("previews"), dict) else {}
            preview_url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
            if preview_url:
                results.append({"id": r.get("id"), "name": r.get("name", ""), "preview_url": preview_url})
        return {"ok": True, "error": None, "results": results}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "results": []}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


def download_to(url: str, dest: Path, *, key: str = "", timeout: float = 30.0) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    full = url
    if key and "token=" not in url:
        full = f"{url}{'&' if '?' in url else '?'}token={key}"
    with urllib.request.urlopen(urllib.request.Request(full), timeout=timeout) as resp:  # noqa: S310
        dest.write_bytes(resp.read())
    return dest


def _safe_slug(text: str) -> str:
    slug = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (text or "sfx")).strip("_")
    return slug[:40] or "sfx"


def source_sfx_entry(query: str, key: str, dest_dir: Path, *, slug: str = "sfx") -> Dict[str, Any]:
    """Search + download the best preview for one query into dest_dir."""
    res = freesound_search(query, key, max_results=1)
    if not res["ok"] or not res["results"]:
        return {"ok": False, "error": res.get("error") or "no results", "path": None}
    top = res["results"][0]
    dest = Path(dest_dir) / f"{_safe_slug(slug)}_{top['id']}.mp3"
    try:
        download_to(top["preview_url"], dest, key=key)
        return {"ok": True, "error": None, "path": dest, "source_id": top["id"], "name": top["name"]}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": None}


def source_declared_sfx(
    declared: List[Dict[str, str]], cache_dir: Path, *, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Fetch every declared non-local SFX into cache_dir. Returns {sourced: {tag: Path}, report, key_present}.
    `declared` is the list from radio_os_studio.declared_sfx(manifest)."""
    key = freesound_api_key(config)
    sourced: Dict[str, Path] = {}
    report: List[Dict[str, Any]] = []
    for item in declared:
        if str(item.get("source") or "local") == "local":
            continue
        tag = str(item.get("tag") or "")
        ref = str(item.get("ref") or "")
        result = source_sfx_entry(ref, key, cache_dir, slug=tag or ref)
        report.append({"tag": tag, "ref": ref, "ok": result["ok"], "error": result.get("error"),
                       "path": str(result["path"]) if result.get("path") else None})
        if result["ok"] and result.get("path"):
            sourced[tag] = result["path"]
    return {"sourced": sourced, "report": report, "key_present": bool(key)}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    key = freesound_api_key()
    print("Radio OS — SFX sourcing (Freesound)")
    print("  api key present:", bool(key))
    if key and len(sys.argv) > 1:
        out = source_sfx_entry(" ".join(sys.argv[1:]), key, Path("sfx") / "_sourced", slug=sys.argv[1])
        print("  result:", out)
    else:
        print("  usage: set asset_sources.freesound.api_key (or FREESOUND_API_KEY), then: python sfx_sourcing.py <query>")
