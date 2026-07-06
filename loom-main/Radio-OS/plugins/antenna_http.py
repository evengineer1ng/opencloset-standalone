"""
Antenna (Generic HTTP-JSON) — Radio OS feed plugin.

The first half of the Loop: point a station at ANY localhost / HTTP JSON API, PROFILE its
signature (loud and proud about what it is reading and where it would bucket each field), then
emit normalized candidates into the runtime. Foreign-source friendly by design: endpoints that
error are skipped; arbitrary nesting is searched for the most "item-like" array; field → narration
bucket is heuristic with optional manifest overrides.

Used as a feed plugin: a manifest feed sets `plugin: antenna_http` and points `base_url` +
`endpoints` at the foreign system (e.g. ATL on http://127.0.0.1:8000). Dependency-free (stdlib
urllib) so it runs anywhere and is unit-testable standalone (see profile_endpoint / map_item_to_candidate).
"""
import json
import os
import time
import hashlib
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_NAME = "antenna_http"
PLUGIN_DESC = "Generic HTTP-JSON antenna — adapt any foreign JSON API into a station feed."
IS_FEED = True
FEED_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "plugin": "antenna_http",
    "base_url": "http://127.0.0.1:8000",
    "endpoints": ["/"],          # list of paths, or {path, source} dicts
    "poll_sec": 45,
    "priority": 70,
    "max_items_per_poll": 12,
    "field_map": {},             # per-source overrides: {source: {title, body, id, priority, actor, ts}}
    "write_signature": True,     # write signature.json on first successful poll
}

# field-name → narration bucket, in preference order
_BUCKET_HINTS: Dict[str, Tuple[str, ...]] = {
    "id":       ("id", "slug", "key", "uuid", "_id", "pk"),
    "title":    ("title", "name", "headline", "question", "label", "subject"),
    "body":     ("body", "description", "rationale", "hypothesis", "notes", "strategy_notes",
                 "text", "content", "detail", "blurb", "summary", "reason"),
    "priority": ("priority", "score", "heur", "rank", "weight", "conviction", "importance"),
    "ts":       ("ts", "timestamp", "created_at", "updated_at", "created", "updated", "date", "time"),
    "actor":    ("actor", "team", "team_name", "author", "host", "owner", "strategy", "family", "name"),
}


def _now_ts() -> float:
    return time.time()


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:16]


def _fetch_json(url: str, timeout: int = 8) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "RadioOS-Antenna/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (localhost antenna)
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _is_textful(d: Any) -> bool:
    return isinstance(d, dict) and any(isinstance(v, str) and len(v.strip()) > 1 for v in d.values())


def _find_item_lists(obj: Any, path: str = "$") -> List[Tuple[str, List[dict]]]:
    """Every array-of-dicts found anywhere in the JSON, with its path."""
    out: List[Tuple[str, List[dict]]] = []
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts and len(dicts) >= max(1, len(obj) // 2):
            out.append((path, dicts))
        for i, x in enumerate(obj[:3]):
            out.extend(_find_item_lists(x, f"{path}[{i}]"))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_find_item_lists(v, f"{path}.{k}"))
    return out


def _best_item_list(obj: Any) -> Tuple[str, List[dict]]:
    """Pick the most item-like array in the response; fall back to treating the object as one item."""
    lists = _find_item_lists(obj)
    if not lists:
        return ("$", [obj]) if isinstance(obj, dict) else ("$", [])
    lists.sort(key=lambda pl: (sum(1 for d in pl[1] if _is_textful(d)), len(pl[1])), reverse=True)
    return lists[0]


def _guess_bucket(field: str) -> Optional[str]:
    f = field.lower()
    for bucket, hints in _BUCKET_HINTS.items():
        if any(h == f for h in hints):
            return bucket
    for bucket, hints in _BUCKET_HINTS.items():
        if any(h in f for h in hints):
            return bucket
    return None


def profile_items(items: List[dict]) -> Dict[str, Any]:
    """field → {type, sample, bucket} across a list of item dicts."""
    fields: Dict[str, Any] = {}
    for it in items[:25]:
        for k, v in it.items():
            if k in fields:
                continue
            sample = type(v).__name__ if isinstance(v, (dict, list)) else str(v)[:80]
            fields[k] = {"type": type(v).__name__, "sample": sample, "bucket": _guess_bucket(k)}
    return fields


def derive_field_map(fields: Dict[str, Any], override: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Choose a concrete field for each narration bucket (hint order wins), honoring overrides."""
    fm: Dict[str, str] = {}
    lower = {name.lower(): name for name in fields}
    for bucket, hints in _BUCKET_HINTS.items():
        chosen = next((lower[h] for h in hints if h in lower), None)
        if not chosen:
            chosen = next((name for name, meta in fields.items() if meta.get("bucket") == bucket), None)
        if chosen:
            fm[bucket] = chosen
    if override:
        fm.update({k: v for k, v in override.items() if v})
    return fm


def profile_endpoint(base_url: str, path: str, source: str, timeout: int = 8) -> Dict[str, Any]:
    """Fetch one endpoint and produce its Signature Profile entry (loud and proud)."""
    data = _fetch_json(base_url.rstrip("/") + path, timeout=timeout)
    if data is None:
        return {"source": source, "path": path, "ok": False, "reason": "no JSON / fetch failed"}
    item_path, items = _best_item_list(data)
    fields = profile_items(items)
    return {"source": source, "path": path, "ok": True, "item_path": item_path,
            "count": len(items), "fields": fields, "field_map": derive_field_map(fields)}


def map_item_to_candidate(item: dict, fm: Dict[str, str], source: str, default_priority: float) -> Dict[str, Any]:
    """Foreign item dict → a Radio OS candidate, using the discovered field map."""
    def g(bucket: str, default: Any = "") -> Any:
        f = fm.get(bucket)
        return item.get(f, default) if f else default

    title = str(g("title") or g("id") or "Update").strip()
    body = str(g("body") or "").strip()
    actor = g("actor")
    if actor and str(actor).strip() and str(actor) not in title:
        title = f"{actor}: {title}"
    raw_id = g("id") or _sha1(source + "|" + title + "|" + body[:120])
    try:
        pr = float(g("priority")) if g("priority") not in (None, "") else default_priority
    except (ValueError, TypeError):
        pr = default_priority
    return {
        "post_id": f"{source}:{raw_id}", "source": source,
        "title": title[:300], "body": body[:2000],
        "priority": pr, "ts": _now_ts(), "type": "item", "tags": [source],
    }


def endpoints_from_cfg(cfg: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for e in (cfg.get("endpoints") or ["/"]):
        if isinstance(e, dict):
            path = e.get("path", "/")
            source = (e.get("source") or path).strip("/").replace("/", "_") or "feed"
        else:
            path = str(e)
            source = path.strip("/").replace("/", "_") or "root"
        out.append((path, source))
    return out


def build_signature(base_url: str, endpoints: List[Tuple[str, str]],
                    overrides: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """Profile every endpoint into one Signature Profile (the antenna's 'here's what I read')."""
    overrides = overrides or {}
    sig: Dict[str, Any] = {"base_url": base_url, "profiled_at": _now_ts(), "endpoints": {}}
    for path, source in endpoints:
        prof = profile_endpoint(base_url, path, source)
        if prof.get("ok"):
            prof["field_map"] = {**prof["field_map"], **overrides.get(source, {})}
        sig["endpoints"][source] = prof
    return sig


def _write_signature(runtime: Dict[str, Any], signature: Dict[str, Any]) -> None:
    try:
        sdir = os.environ.get("STATION_DIR") or "."
        p = os.path.join(sdir, "signature.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(signature, f, indent=2)
        runtime.get("log", lambda *a: None)("antenna", f"📝 wrote signature → {p}")
    except Exception as e:  # noqa: BLE001
        runtime.get("log", lambda *a: None)("antenna", f"signature write failed: {e}")


def feed_worker(stop_event, mem, payload, runtime) -> None:
    """Radio OS feed entry point: poll the foreign API, profile once, emit candidates forever."""
    log = runtime.get("log", lambda *a: None)
    emit = runtime.get("emit_candidate", lambda c: None)
    cfg = {**FEED_DEFAULTS, **(payload or {})}
    base = cfg["base_url"]
    poll = max(1, int(cfg.get("poll_sec", 45)))
    default_pr = float(cfg.get("priority", 70))
    max_items = int(cfg.get("max_items_per_poll", 12))
    overrides = cfg.get("field_map") or {}
    eps = endpoints_from_cfg(cfg)
    seen: set = set()
    profiled = False
    log("antenna", f"📡 Antenna online → {base} :: {len(eps)} endpoint(s)")

    while not stop_event.is_set():
        signature = build_signature(base, eps, overrides)
        for source, prof in signature["endpoints"].items():
            if not prof.get("ok"):
                if not profiled:
                    log("antenna", f"  ✗ {prof.get('path')}: {prof.get('reason')}")
                continue
            fm = prof["field_map"]
            if not profiled:
                log("antenna", f"  ✓ {prof['path']} → '{source}': {prof['count']} item(s) "
                               f"at {prof['item_path']}; buckets {fm}")
            data = _fetch_json(base.rstrip("/") + prof["path"])
            if data is None:
                continue
            _, items = _best_item_list(data)
            n = 0
            for it in items[:max_items]:
                cand = map_item_to_candidate(it, fm, source, default_pr)
                if cand["post_id"] in seen:
                    continue
                seen.add(cand["post_id"])
                emit(cand)
                n += 1
            if n:
                log("antenna", f"  → emitted {n} new candidate(s) from {source}")
        if not profiled and cfg.get("write_signature", True):
            _write_signature(runtime, signature)
        profiled = True
        for _ in range(poll):
            if stop_event.is_set():
                break
            time.sleep(1)
