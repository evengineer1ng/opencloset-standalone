from __future__ import annotations

import calendar
import hashlib
import json
import os
import time
from fnmatch import fnmatch
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import feedparser
import requests


USER_AGENT = "OpenClaw-BoothBaker/1.0"
SPORTSDB_V1_BASE = "https://www.thesportsdb.com/api/v1/json/123"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def clean_html(text: str) -> str:
    compact = str(text or "")
    compact = compact.replace("<![CDATA[", "").replace("]]>", "")
    out = []
    in_tag = False
    for ch in compact:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            out.append(" ")
            continue
        if not in_tag:
            out.append(ch)
    return " ".join("".join(out).replace("&nbsp;", " ").replace("&amp;", "&").split())


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def slugify_label(text: str) -> str:
    compact = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text or ""))
    while "__" in compact:
        compact = compact.replace("__", "_")
    return compact.strip("_") or "source"


def parse_timestamp_from_entry(entry: dict[str, Any]) -> tuple[int, str]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            ts = calendar.timegm(parsed)
            return ts, datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    iso = str(entry.get("published") or entry.get("updated") or "").strip()
    if iso:
        return int(time.time()), iso
    ts = int(time.time())
    return ts, datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def infer_valence(*parts: str) -> str:
    text = " ".join(str(part or "").lower() for part in parts)
    if any(word in text for word in ("breaking", "wins", "win", "launch", "surge", "record", "beats", "raises")):
        return "hype"
    if any(word in text for word in ("outage", "dies", "death", "warn", "warning", "crash", "panic", "cuts", "drops")):
        return "alarm"
    return "calm"


@dataclass
class FeedItem:
    feed_key: str
    plugin: str
    item_id: str
    actor: str
    action: str
    object: str
    title: str
    body: str
    url: str
    published_at: str
    published_ts: int
    valence: str
    priority: float
    meta: dict[str, Any]

    def to_booth_event(self, lap: int) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "action": self.action,
            "object": self.object,
            "valence": self.valence,
            "priority": self.priority,
            "lap": lap,
            "raw_title": self.title,
            "body": self.body,
            "url": self.url,
            "feed_key": self.feed_key,
            "plugin": self.plugin,
            "published_at": self.published_at,
            "meta": dict(self.meta),
        }


def _fixture_items(feed_key: str, cfg: dict[str, Any]) -> list[FeedItem]:
    fixtures = cfg.get("fixture_items")
    if not isinstance(fixtures, list):
        return []
    plugin = str(cfg.get("plugin") or "").strip().lower()
    out: list[FeedItem] = []
    for index, item in enumerate(fixtures):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = clean_html(str(item.get("body") or item.get("summary") or ""))
        url = str(item.get("url") or "")
        published_at = str(item.get("published_at") or item.get("published") or now_iso())
        published_ts = int(item.get("published_ts") or (index + 1))
        actor = str(item.get("actor") or item.get("source") or domain_of(url) or feed_key)
        if plugin == "reddit" and not item.get("actor"):
            subreddit = str(item.get("subreddit") or "").strip()
            if subreddit:
                actor = f"r/{subreddit}"
        action = str(item.get("action") or ("post" if plugin == "reddit" else "publish"))
        obj = str(item.get("object") or title or body[:120] or f"{plugin} item")
        out.append(
            FeedItem(
                feed_key=feed_key,
                plugin=plugin,
                item_id=str(item.get("id") or sha1_text(f"{feed_key}|{title}|{url}|{published_at}")),
                actor=actor,
                action=action,
                object=obj,
                title=title or obj,
                body=body,
                url=url,
                published_at=published_at,
                published_ts=published_ts,
                valence=str(item.get("valence") or infer_valence(title, body)),
                priority=float(item.get("priority") or 0.8),
                meta={k: v for k, v in item.items() if k not in {"id", "title", "body", "summary", "url", "published_at", "published_ts", "actor", "action", "object", "valence", "priority"}},
            )
        )
    return out


def fetch_rss_items(
    feed_key: str,
    cfg: dict[str, Any],
    *,
    parse_feed: Callable[[str], Any] | None = None,
) -> list[FeedItem]:
    fixtures = _fixture_items(feed_key, cfg)
    if fixtures:
        return fixtures
    parse_feed = parse_feed or feedparser.parse
    urls = [str(url).strip() for url in cfg.get("urls", []) if str(url).strip()]
    emit_limit = max(1, int(cfg.get("emit_limit") or cfg.get("limit") or 5))
    items: list[FeedItem] = []
    for url in urls:
        feed = parse_feed(url)
        entries = list(getattr(feed, "entries", []) or [])[:emit_limit]
        for entry in entries:
            title = clean_html(str(entry.get("title") or ""))
            body = clean_html(str(entry.get("summary") or entry.get("description") or ""))
            link = str(entry.get("link") or "")
            published_ts, published_at = parse_timestamp_from_entry(entry)
            actor = domain_of(link) or domain_of(url) or feed_key
            items.append(
                FeedItem(
                    feed_key=feed_key,
                    plugin="rss",
                    item_id=str(entry.get("id") or entry.get("link") or sha1_text(f"{feed_key}|{title}|{published_at}")),
                    actor=actor,
                    action="publish",
                    object=title or body[:120] or "headline",
                    title=title or "untitled feed item",
                    body=body,
                    url=link,
                    published_at=published_at,
                    published_ts=published_ts,
                    valence=infer_valence(title, body),
                    priority=0.75 if "breaking" not in title.lower() else 0.9,
                    meta={"feed_url": url},
                )
            )
    return items


def fetch_reddit_items(
    feed_key: str,
    cfg: dict[str, Any],
    *,
    http_get: Callable[..., Any] | None = None,
) -> list[FeedItem]:
    fixtures = _fixture_items(feed_key, cfg)
    if fixtures:
        return fixtures
    http_get = http_get or requests.get
    subreddits = [str(sub).strip() for sub in cfg.get("subreddits", []) if str(sub).strip()]
    listing = str(cfg.get("listing") or "").strip().lower()
    limit = max(1, int(cfg.get("limit") or 6))
    modes = cfg.get("modes") or ["new", "hot"]
    mode_limit = max(1, int(limit / max(1, len(modes))) + 1)
    items: list[FeedItem] = []
    if listing == "popular":
        response = http_get(
            "https://www.reddit.com/r/popular.json",
            headers={"User-Agent": USER_AGENT},
            params={"limit": limit, "raw_json": 1},
            timeout=float(cfg.get("timeout_sec") or 8),
        )
        if getattr(response, "status_code", 200) == 200:
            payload = response.json()
            for child in payload.get("data", {}).get("children", []):
                data = child.get("data", {})
                created_ts = int(data.get("created_utc") or time.time())
                subreddit = str(data.get("subreddit") or "popular").strip()
                title = str(data.get("title") or "").strip()
                body = clean_html(str(data.get("selftext") or ""))
                score = int(data.get("score") or 0)
                comments = int(data.get("num_comments") or 0)
                priority = 0.78
                if score >= 5000 or comments >= 500:
                    priority = 0.94
                items.append(
                    FeedItem(
                        feed_key=feed_key,
                        plugin="reddit",
                        item_id=str(data.get("id") or sha1_text(f"{feed_key}|popular|{subreddit}|{title}|{created_ts}")),
                        actor=f"r/{subreddit}",
                        action="trend",
                        object=title or "popular post",
                        title=title or "untitled reddit post",
                        body=body,
                        url="https://www.reddit.com" + str(data.get("permalink") or ""),
                        published_at=datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        published_ts=created_ts,
                        valence=infer_valence(title, body),
                        priority=priority,
                        meta={"listing": "popular", "subreddit": subreddit, "score": score, "comments": comments, "author": data.get("author") or ""},
                    )
                )
        return items
    for sub in subreddits:
        for mode in modes:
            response = http_get(
                f"https://old.reddit.com/r/{sub}/{mode}.json",
                headers={"User-Agent": USER_AGENT},
                params={"limit": mode_limit},
                timeout=float(cfg.get("timeout_sec") or 8),
            )
            if getattr(response, "status_code", 200) != 200:
                continue
            payload = response.json()
            children = payload.get("data", {}).get("children", [])
            for child in children:
                data = child.get("data", {})
                created_ts = int(data.get("created_utc") or time.time())
                title = str(data.get("title") or "").strip()
                body = clean_html(str(data.get("selftext") or ""))
                subreddit = str(data.get("subreddit") or sub).strip()
                score = int(data.get("score") or 0)
                comments = int(data.get("num_comments") or 0)
                priority = 0.7
                if score >= 1000 or comments >= 150:
                    priority = 0.92
                elif score >= 100 or comments >= 40:
                    priority = 0.82
                items.append(
                    FeedItem(
                        feed_key=feed_key,
                        plugin="reddit",
                        item_id=str(data.get("id") or sha1_text(f"{feed_key}|{subreddit}|{title}|{created_ts}")),
                        actor=f"r/{subreddit}",
                        action="post",
                        object=title or "post",
                        title=title or "untitled reddit post",
                        body=body,
                        url="https://old.reddit.com" + str(data.get("permalink") or ""),
                        published_at=datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        published_ts=created_ts,
                        valence=infer_valence(title, body),
                        priority=priority,
                        meta={"subreddit": subreddit, "score": score, "comments": comments, "mode": mode, "author": data.get("author") or ""},
                    )
                )
    return items


def fetch_document_repo_items(feed_key: str, cfg: dict[str, Any]) -> list[FeedItem]:
    fixtures = _fixture_items(feed_key, cfg)
    if fixtures:
        return fixtures
    repo_path = Path(str(cfg.get("repo_path") or cfg.get("path") or "")).expanduser()
    if not repo_path.exists():
        raise ValueError(f"document_repo path does not exist: {repo_path}")
    include_globs = [str(item) for item in cfg.get("include_globs", ["*.txt", "*.md", "*.lrc"]) if str(item).strip()]
    exclude_globs = [str(item) for item in cfg.get("exclude_globs", [".git/*", "node_modules/*", "dist/*", "build/*"]) if str(item).strip()]
    max_files = max(1, int(cfg.get("max_files") or 25))
    max_chars = max(80, int(cfg.get("max_chars_per_file") or 900))
    actor = str(cfg.get("actor") or repo_path.name or feed_key)
    action = str(cfg.get("action") or "write")
    files: list[Path] = []
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_path).as_posix()
        if include_globs and not any(fnmatch(rel, pattern) or fnmatch(path.name, pattern) for pattern in include_globs):
            continue
        if exclude_globs and any(fnmatch(rel, pattern) for pattern in exclude_globs):
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    items: list[FeedItem] = []
    for index, path in enumerate(files):
        rel = path.relative_to(repo_path).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        compact = " ".join(text.split())
        if not compact:
            continue
        snippet = compact[:max_chars]
        title = str(cfg.get("title_prefix") or "lyric file") + ": " + rel
        ts = int(path.stat().st_mtime)
        items.append(
            FeedItem(
                feed_key=feed_key,
                plugin="document_repo",
                item_id=sha1_text(f"{repo_path}|{rel}|{ts}|{len(snippet)}"),
                actor=actor,
                action=action,
                object=rel,
                title=title,
                body=snippet,
                url=str(path),
                published_at=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                published_ts=ts,
                valence=str(cfg.get("valence") or infer_valence(rel, snippet)),
                priority=float(cfg.get("priority") or 0.74),
                meta={"repo_path": str(repo_path), "relative_path": rel, "char_count": len(compact), "rank": index + 1},
            )
        )
    return items


def fetch_sports_api_items(
    feed_key: str,
    cfg: dict[str, Any],
    *,
    http_get: Callable[..., Any] | None = None,
) -> list[FeedItem]:
    fixtures = _fixture_items(feed_key, cfg)
    if fixtures:
        return fixtures
    http_get = http_get or requests.get
    provider = str(cfg.get("provider") or "thesportsdb").strip().lower()
    if provider != "thesportsdb":
        raise ValueError(f"Unsupported sports_api provider: {provider}")
    endpoint = str(cfg.get("endpoint") or "eventspastleague").strip()
    params = dict(cfg.get("params") or {})
    url = f"{SPORTSDB_V1_BASE}/{endpoint}.php"
    response = http_get(url, headers={"User-Agent": USER_AGENT}, params=params, timeout=float(cfg.get("timeout_sec") or 10))
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("events") or payload.get("results") or []
    items: list[FeedItem] = []
    for row in rows[: max(1, int(cfg.get("limit") or 8))]:
        if not isinstance(row, dict):
            continue
        home = str(row.get("strHomeTeam") or "").strip()
        away = str(row.get("strAwayTeam") or "").strip()
        league = str(row.get("strLeague") or cfg.get("actor") or "sports desk").strip()
        home_score = row.get("intHomeScore")
        away_score = row.get("intAwayScore")
        status = str(row.get("strStatus") or "").strip()
        title = f"{home} vs {away}".strip(" vs ")
        if home and away and home_score not in (None, "") and away_score not in (None, ""):
            obj = f"{home} {home_score}-{away_score} {away}"
            action = "finish" if status.upper() in {"FT", "FINAL", "AOT", "FT_PEN"} else "stage"
        else:
            obj = title or str(row.get("strEvent") or "sports event")
            action = "schedule"
        date_iso = str(row.get("strTimestamp") or row.get("dateEvent") or now_iso())
        ts = int(time.time())
        items.append(
            FeedItem(
                feed_key=feed_key,
                plugin="sports_api",
                item_id=str(row.get("idEvent") or sha1_text(f"{feed_key}|{title}|{date_iso}")),
                actor=league or "sports desk",
                action=action,
                object=obj,
                title=str(row.get("strEvent") or title or "sports event"),
                body=" ".join(
                    part for part in [
                        f"Venue: {row.get('strVenue')}" if row.get("strVenue") else "",
                        f"Status: {status}" if status else "",
                        f"Season: {row.get('strSeason')}" if row.get("strSeason") else "",
                    ] if part
                ),
                url=str(row.get("strVideo") or ""),
                published_at=date_iso,
                published_ts=ts,
                valence=infer_valence(title, obj, status),
                priority=float(cfg.get("priority") or 0.84),
                meta={
                    "provider": provider,
                    "endpoint": endpoint,
                    "league": league,
                    "home_team": home,
                    "away_team": away,
                    "home_score": home_score,
                    "away_score": away_score,
                    "status": status,
                    "season": row.get("strSeason"),
                    "event_id": row.get("idEvent"),
                },
            )
        )
    return items


def fetch_items_for_feed(
    feed_key: str,
    cfg: dict[str, Any],
    *,
    http_get: Callable[..., Any] | None = None,
    parse_feed: Callable[[str], Any] | None = None,
) -> list[FeedItem]:
    plugin = str(cfg.get("plugin") or "").strip().lower()
    if not plugin or not bool(cfg.get("enabled", True)):
        return []
    if plugin == "rss":
        return fetch_rss_items(feed_key, cfg, parse_feed=parse_feed)
    if plugin == "reddit":
        return fetch_reddit_items(feed_key, cfg, http_get=http_get)
    if plugin == "document_repo":
        return fetch_document_repo_items(feed_key, cfg)
    if plugin == "sports_api":
        return fetch_sports_api_items(feed_key, cfg, http_get=http_get)
    raise ValueError(f"Unsupported booth feed plugin: {plugin}")


def sort_feed_items(items: list[FeedItem]) -> list[FeedItem]:
    return sorted(
        items,
        key=lambda item: (
            item.published_ts,
            item.feed_key,
            item.plugin,
            item.item_id,
        ),
    )


def derive_schema_hints(items: list[FeedItem]) -> dict[str, Any]:
    actors = sorted({item.actor for item in items})
    plugins = sorted({item.plugin for item in items})
    fields = {
        "base": ["actor", "action", "object", "valence", "lap", "priority", "published_at", "url"],
        "reddit": ["score", "comments", "subreddit", "mode", "author"] if "reddit" in plugins else [],
        "rss": ["feed_url", "domain"] if "rss" in plugins else [],
        "document_repo": ["repo_path", "relative_path", "char_count"] if "document_repo" in plugins else [],
        "sports_api": ["league", "home_team", "away_team", "home_score", "away_score", "status", "season"] if "sports_api" in plugins else [],
    }
    query_aliases = {
        "count": ["how many", "count", "number of"],
        "first": ["first", "earliest"],
        "last": ["last", "latest"],
        "next": ["after", "what happened next"],
        "overview": ["summary", "overview", "what is this about"],
        "source": ["source", "site", "domain", "subreddit", "feed"],
        "engagement": ["score", "karma", "comments"] if "reddit" in plugins else [],
        "result": ["score", "final", "result", "who won", "winner", "home score", "away score"] if "sports_api" in plugins else [],
    }
    return {
        "kind": "feed_digest",
        "plugins": plugins,
        "actors": actors,
        "fields": fields,
        "query_aliases": query_aliases,
        "notes": [
            "Live feed determinism comes from snapshot capture: same fetched snapshot yields the same baked tape.",
            "This artifact is upstream of booth runtime; booth itself remains one-file and deterministic against the baked snapshot.",
        ],
    }


def render_inline_js(tape_key: str, events: list[dict[str, Any]]) -> str:
    payload = json.dumps(events, indent=2, ensure_ascii=False)
    return f'// paste into booth-presets.html\\nTAPES[{json.dumps(tape_key)}] = {payload};\\n'


def build_booth_artifact(
    spec: dict[str, Any],
    *,
    name: str | None = None,
    http_get: Callable[..., Any] | None = None,
    parse_feed: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    feeds = spec.get("feeds") or {}
    if not isinstance(feeds, dict) or not feeds:
        raise ValueError("Booth feed spec must contain a non-empty feeds object.")
    all_items: list[FeedItem] = []
    for feed_key, cfg in feeds.items():
        if not isinstance(cfg, dict):
            continue
        all_items.extend(fetch_items_for_feed(str(feed_key), cfg, http_get=http_get, parse_feed=parse_feed))
    ordered_items = sort_feed_items(all_items)
    tape_key = name or str(spec.get("station") or spec.get("name") or "Feed Digest")
    events = [item.to_booth_event(index + 1) for index, item in enumerate(ordered_items)]
    schema_hints = derive_schema_hints(ordered_items)
    items_payload = [asdict(item) for item in ordered_items]
    inline_js = render_inline_js(tape_key, events)
    return {
        "spec_version": "booth-feed-bake-v1",
        "generated_at": now_iso(),
        "tape_key": tape_key,
        "manifest": spec,
        "snapshot": {
            "feed_count": len(feeds),
            "item_count": len(ordered_items),
            "plugins": schema_hints["plugins"],
        },
        "items": items_payload,
        "tape": {
            "name": tape_key,
            "events": events,
        },
        "schema_hints": schema_hints,
        "booth_lines": [f"{event['actor']} | {event['action']} | {event['object']}" for event in events],
        "inline_js": inline_js,
    }


def bake_spec_file(
    spec_path: str | Path,
    *,
    out_path: str | Path,
    inline_js_out: str | Path | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    spec = read_json(spec_path)
    artifact = build_booth_artifact(spec, name=name)
    artifact["source_spec_path"] = str(Path(spec_path))
    write_json(out_path, artifact)
    if inline_js_out:
        write_text(inline_js_out, artifact["inline_js"])
    return artifact


def process_spec_queue(
    inbox_dir: str | Path,
    *,
    out_dir: str | Path,
    archive_dir: str | Path | None = None,
    write_inline_js: bool = True,
) -> dict[str, Any]:
    inbox = Path(inbox_dir)
    out_root = Path(out_dir)
    archive_root = Path(archive_dir) if archive_dir else None
    processed: list[dict[str, Any]] = []
    for spec_path in sorted(inbox.glob("*.json")):
        spec = read_json(spec_path)
        digest = sha1_text(json.dumps(spec, sort_keys=True, ensure_ascii=False))
        stem = spec_path.stem
        out_path = out_root / f"{stem}-{digest[:10]}.artifact.json"
        inline_path = out_root / f"{stem}-{digest[:10]}.inline.js"
        artifact = build_booth_artifact(spec, name=str(spec.get("station") or spec.get("name") or stem))
        artifact["source_spec_path"] = str(spec_path)
        write_json(out_path, artifact)
        if write_inline_js:
            write_text(inline_path, artifact["inline_js"])
        if archive_root:
            archive_root.mkdir(parents=True, exist_ok=True)
            archive_target = archive_root / spec_path.name
            archive_target.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
        processed.append(
            {
                "spec": str(spec_path),
                "artifact": str(out_path),
                "inline_js": str(inline_path) if write_inline_js else None,
                "item_count": artifact["snapshot"]["item_count"],
                "plugins": artifact["snapshot"]["plugins"],
            }
        )
    return {
        "inbox": str(inbox),
        "out_dir": str(out_root),
        "archive_dir": str(archive_root) if archive_root else None,
        "processed_count": len(processed),
        "processed": processed,
    }


def build_agent_event_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "booth.feed_baked",
        "source": "booth_feed_baker",
        "text": f"Baked booth feed artifact for {artifact.get('tape_key')}",
        "payload": {
            "tape_key": artifact.get("tape_key"),
            "snapshot": artifact.get("snapshot"),
            "schema_hints": artifact.get("schema_hints"),
            "source_spec_path": artifact.get("source_spec_path"),
        },
    }


def write_agent_event_payload(artifact_path: str | Path, out_path: str | Path) -> dict[str, Any]:
    artifact = read_json(artifact_path)
    payload = build_agent_event_payload(artifact)
    write_json(out_path, payload)
    return payload
