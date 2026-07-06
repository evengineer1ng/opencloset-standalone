#!/usr/bin/env python3
"""
loom_tape_wizard.py

Standalone Loom tape recorder.

No station manifest.
No RadioOS project wiring.
No GUI dependency.
Just:

    choose source
    choose how far back / how long forward
    choose tick interval
    record events
    output tape
    notify when done

Outputs:
- .tape.ndjson  append-only tape, one Loom row per line
- .tape.json    JSON list tape for Loom/spec compatibility

Supported sources, stdlib only:
1. RSS / Atom feed URL
2. JSON HTTP endpoint
3. Plain text file
4. Folder watcher
5. Manual stdin beats

Run:

    python loom_tape_wizard.py

Or non-interactive:

    python loom_tape_wizard.py --source rss --url https://example.com/feed.xml --duration 60 --tick 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import traceback
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import dataclasses

TAPE_VERSION = "loom.tape.v1"


# -----------------------------
# Small utilities
# -----------------------------

def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_duration_seconds(raw: str | int | float | None, default: int = 60) -> int:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (int, float)):
        return max(0, int(raw))

    text = str(raw).strip().lower()
    if text in {"0", "none", "no", "once"}:
        return 0

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?", text)
    if not m:
        raise ValueError(f"Could not parse duration: {raw!r}")

    value = float(m.group(1))
    unit = m.group(2) or "s"
    if unit.startswith("m"):
        value *= 60
    elif unit.startswith("h"):
        value *= 3600
    return max(0, int(value))


def parse_back_seconds(raw: str | int | float | None, default: int = 0) -> int:
    # Same syntax as duration. For most live sources this is best-effort only.
    return parse_duration_seconds(raw, default)


def stable_id(*parts: Any) -> str:
    joined = "\n".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(joined.encode("utf-8", errors="replace")).hexdigest()[:16]


def clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def slug_action(value: Any, fallback: str = "emit") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_ -]+", "", text)
    text = re.sub(r"[\s-]+", "_", text).strip("_")
    return text or fallback


def safe_tag_value(value: Any) -> str:
    text = clean_text(value, 180)
    # Keep the tag key:value grammar intact.
    return text.replace("\n", " ").replace("\r", " ").replace("|", "/").replace('"', "'")


def row_from_roles(
    *,
    actor: str,
    action: str,
    object: str = "",
    magnitude: Any = None,
    unit: str = "",
    valence: str = "calm",
    when: str = "",
    source: str = "",
    priority: float = 0.5,
    extra_tags: Optional[Sequence[str]] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tags = [
        f"actor:{safe_tag_value(actor)}",
        f"action:{safe_tag_value(slug_action(action))}",
    ]
    if object:
        tags.append(f"object:{safe_tag_value(object)}")
    if magnitude is not None and str(magnitude) != "":
        tags.append(f"magnitude:{safe_tag_value(magnitude)}")
    if unit:
        tags.append(f"unit:{safe_tag_value(unit)}")
    if valence:
        tags.append(f"valence:{safe_tag_value(valence)}")
    if when:
        tags.append(f"time:{safe_tag_value(when)}")
    if source:
        tags.append(f"source:{safe_tag_value(source)}")
    if extra_tags:
        tags.extend(str(t) for t in extra_tags if str(t).strip())

    row: Dict[str, Any] = {
        "tags": tags,
        "priority": max(0.0, min(1.0, float(priority))),
    }
    if raw:
        row["raw"] = raw
    return row


def infer_valence(text: str) -> str:
    lower = text.lower()
    alarm_words = ("error", "fail", "failed", "down", "crash", "fire", "loss", "lost", "alert", "warning", "urgent")
    hype_words = ("win", "wins", "won", "surge", "spike", "breakout", "record", "launch", "green", "passed", "success")
    if any(w in lower for w in alarm_words):
        return "alarm"
    if any(w in lower for w in hype_words):
        return "hype"
    return "calm"


def infer_priority(text: str, base: float = 0.5) -> float:
    lower = text.lower()
    score = base
    if any(w in lower for w in ("breaking", "urgent", "alert", "critical", "crash", "failed", "fire")):
        score += 0.3
    if any(w in lower for w in ("win", "surge", "spike", "record", "launch")):
        score += 0.15
    return max(0.0, min(1.0, score))


def write_ndjson(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_ndjson(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(item)
            except Exception:
                continue
    return out


def write_json_list(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(list(rows), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def notify_done(title: str, message: str) -> None:
    print("\a", end="", flush=True)
    print(f"\n{title}: {message}")

    # Best-effort desktop popup using stdlib tkinter.
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        pass


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "loom-tape-wizard/0.1 (+https://evengineer1ng.github.io/loom/)",
            "Accept": "application/rss+xml, application/atom+xml, application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# -----------------------------
# Sources
# -----------------------------

class Source:
    name = "source"

    def poll(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class RssSource(Source):
    name = "rss"

    def __init__(self, url: str):
        self.url = url

    def poll(self) -> List[Dict[str, Any]]:
        data = fetch_url(self.url)
        root = ET.fromstring(data)
        rows: List[Dict[str, Any]] = []

        # RSS items
        for item in root.findall(".//item"):
            title = clean_text(item.findtext("title"))
            link = clean_text(item.findtext("link"))
            pub = clean_text(item.findtext("pubDate") or item.findtext("date"))
            desc = clean_text(item.findtext("description"))
            text = title or desc or link
            if not text:
                continue
            source_actor = urllib.parse.urlparse(self.url).netloc or "rss"
            rows.append(row_from_roles(
                actor=source_actor,
                action="publish",
                object=text,
                valence=infer_valence(text),
                when=pub or iso_now(),
                source=self.url,
                priority=infer_priority(text, 0.55),
                raw={"title": title, "link": link, "published": pub},
            ))

        # Atom entries, namespace-tolerant
        for entry in root.findall(".//{*}entry"):
            title_el = entry.find("{*}title")
            link_el = entry.find("{*}link")
            updated_el = entry.find("{*}updated") or entry.find("{*}published")
            title = clean_text(title_el.text if title_el is not None else "")
            href = ""
            if link_el is not None:
                href = clean_text(link_el.attrib.get("href", "") or link_el.text or "")
            updated = clean_text(updated_el.text if updated_el is not None else "")
            text = title or href
            if not text:
                continue
            source_actor = urllib.parse.urlparse(self.url).netloc or "atom"
            rows.append(row_from_roles(
                actor=source_actor,
                action="publish",
                object=text,
                valence=infer_valence(text),
                when=updated or iso_now(),
                source=self.url,
                priority=infer_priority(text, 0.55),
                raw={"title": title, "link": href, "published": updated},
            ))

        return rows


class JsonHttpSource(Source):
    name = "json_http"

    def __init__(self, url: str):
        self.url = url

    def poll(self) -> List[Dict[str, Any]]:
        data = json.loads(fetch_url(self.url).decode("utf-8", errors="replace"))
        events = self._extract_events(data)
        rows = []
        actor_default = urllib.parse.urlparse(self.url).netloc or "json_endpoint"

        for event in events:
            if not isinstance(event, dict):
                event = {"value": event}

            actor = event.get("actor") or event.get("symbol") or event.get("source") or event.get("name") or actor_default
            action = event.get("action") or event.get("event") or event.get("type") or event.get("kind") or "emit"
            obj = event.get("object") or event.get("title") or event.get("message") or event.get("body") or event.get("value") or ""
            magnitude = event.get("magnitude") or event.get("amount") or event.get("price") or event.get("change")
            unit = event.get("unit") or event.get("currency") or ""
            when = event.get("time") or event.get("timestamp") or event.get("created_at") or iso_now()
            valence = event.get("valence") or infer_valence(json.dumps(event, ensure_ascii=False))
            priority = event.get("priority")
            if priority is None:
                priority = infer_priority(json.dumps(event, ensure_ascii=False), 0.5)

            rows.append(row_from_roles(
                actor=str(actor),
                action=str(action),
                object=str(obj),
                magnitude=magnitude,
                unit=str(unit),
                valence=str(valence),
                when=str(when),
                source=self.url,
                priority=float(priority),
                raw=event,
            ))

        return rows

    def _extract_events(self, data: Any) -> List[Any]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("events", "items", "data", "results", "feed"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return [{"value": data}]


class TextFileSource(Source):
    name = "text_file"

    def __init__(self, path: Path):
        self.path = path

    def poll(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for idx, line in enumerate(self.path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            text = clean_text(line)
            if not text:
                continue
            rows.append(row_from_roles(
                actor=self.path.name,
                action="say",
                object=text,
                valence=infer_valence(text),
                when=iso_now(),
                source=str(self.path),
                priority=infer_priority(text, 0.45),
                extra_tags=[f"line:{idx}"],
                raw={"line": idx, "text": text},
            ))
        return rows


class FolderSource(Source):
    name = "folder"

    def __init__(self, path: Path):
        self.path = path

    def poll(self) -> List[Dict[str, Any]]:
        rows = []
        if not self.path.exists():
            return []
        for child in sorted(self.path.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
            if not child.is_file():
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            rows.append(row_from_roles(
                actor=child.name,
                action="exist",
                object=f"{stat.st_size} bytes",
                magnitude=stat.st_size,
                unit="bytes",
                valence="calm",
                when=dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                source=str(self.path),
                priority=0.35,
                raw={"path": str(child), "size": stat.st_size, "mtime": stat.st_mtime},
            ))
        return rows


class ManualSource(Source):
    name = "manual"

    def poll(self) -> List[Dict[str, Any]]:
        print("Paste one or more lines. End with a blank line.")
        lines = []
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line)
        rows = []
        for idx, line in enumerate(lines, start=1):
            text = clean_text(line)
            rows.append(row_from_roles(
                actor="manual",
                action="say",
                object=text,
                valence=infer_valence(text),
                when=iso_now(),
                source="stdin",
                priority=infer_priority(text, 0.5),
                extra_tags=[f"beat:{idx}"],
                raw={"text": text},
            ))
        return rows


# -----------------------------
# Recorder
# -----------------------------

def row_key(row: Dict[str, Any]) -> str:
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    raw = row.get("raw", {})
    return stable_id(tags, raw)


@dataclasses.dataclass
class RecorderConfig:
    source_type: str
    url: str = ""
    path: str = ""
    tick_sec: int = 30
    duration_sec: int = 60
    back_sec: int = 0
    out_ndjson: Path = Path("loom_recording.tape.ndjson")
    out_json: Path = Path("loom_recording.tape.json")
    write_json: bool = True
    print_rows: bool = False


def make_source(cfg: RecorderConfig) -> Source:
    st = cfg.source_type.lower().strip()
    if st in {"rss", "atom", "feed"}:
        if not cfg.url:
            raise ValueError("RSS source needs --url")
        return RssSource(cfg.url)
    if st in {"json", "json_http", "http"}:
        if not cfg.url:
            raise ValueError("JSON HTTP source needs --url")
        return JsonHttpSource(cfg.url)
    if st in {"text", "file", "text_file"}:
        if not cfg.path:
            raise ValueError("Text file source needs --path")
        return TextFileSource(Path(cfg.path))
    if st in {"folder", "dir", "directory"}:
        if not cfg.path:
            raise ValueError("Folder source needs --path")
        return FolderSource(Path(cfg.path))
    if st in {"manual", "stdin", "paste"}:
        return ManualSource()
    raise ValueError(f"Unknown source type: {cfg.source_type}")


def apply_back_window(rows: List[Dict[str, Any]], back_sec: int) -> List[Dict[str, Any]]:
    # Best-effort: only filters rows that have parseable time tags.
    # If the source does not expose timestamps, keep the row.
    if back_sec <= 0:
        return rows

    cutoff = utc_now() - dt.timedelta(seconds=back_sec)
    out = []

    for row in rows:
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        time_value = ""
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("time:"):
                time_value = tag[5:]
                break

        if not time_value:
            out.append(row)
            continue

        parsed = parse_loose_time(time_value)
        if parsed is None or parsed >= cutoff:
            out.append(row)

    return out


def parse_loose_time(value: str) -> Optional[dt.datetime]:
    text = value.strip()
    if not text:
        return None

    # ISO
    try:
        iso = text.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        pass

    # RFC-ish RSS dates
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def record(cfg: RecorderConfig) -> Tuple[int, int]:
    source = make_source(cfg)
    seen: set[str] = set()

    # Avoid duplicate emission if appending onto an existing tape.
    for existing in read_ndjson(cfg.out_ndjson):
        seen.add(row_key(existing))

    started = time.time()
    deadline = started + cfg.duration_sec if cfg.duration_sec > 0 else started
    ticks = 0
    emitted = 0

    print(f"Recording source={source.name} tick={cfg.tick_sec}s duration={cfg.duration_sec}s")
    print(f"NDJSON tape: {cfg.out_ndjson}")
    if cfg.write_json:
        print(f"JSON tape:   {cfg.out_json}")
    print("Press Ctrl+C to stop early.\n")

    try:
        while True:
            ticks += 1
            tick_started = time.time()
            try:
                rows = source.poll()
                rows = apply_back_window(rows, cfg.back_sec)
            except Exception as exc:
                rows = [row_from_roles(
                    actor=source.name,
                    action="error",
                    object=str(exc),
                    valence="alarm",
                    when=iso_now(),
                    source=source.name,
                    priority=0.9,
                    raw={"traceback": traceback.format_exc(limit=5)},
                )]

            fresh = []
            for row in rows:
                key = row_key(row)
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(row)

            if fresh:
                write_ndjson(cfg.out_ndjson, fresh)
                emitted += len(fresh)
                if cfg.print_rows:
                    for row in fresh:
                        print(json.dumps(row, ensure_ascii=False))

            print(f"[{iso_now()}] tick {ticks}: +{len(fresh)} rows ({emitted} total)")

            if cfg.duration_sec == 0 or time.time() >= deadline:
                break

            sleep_for = max(0.0, cfg.tick_sec - (time.time() - tick_started))
            remaining = max(0.0, deadline - time.time())
            time.sleep(min(sleep_for, remaining))

    except KeyboardInterrupt:
        print("\nStopped early by user.")

    all_rows = read_ndjson(cfg.out_ndjson)
    if cfg.write_json:
        write_json_list(cfg.out_json, all_rows)

    notify_done("Loom tape complete", f"Recorded {emitted} new rows over {ticks} tick(s).")
    return emitted, ticks


# -----------------------------
# Wizard
# -----------------------------

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else default


def wizard() -> RecorderConfig:
    print("\nMake a Loom tape")
    print("================")
    print("Sources:")
    print("  1. RSS / Atom feed URL")
    print("  2. JSON HTTP endpoint")
    print("  3. Plain text file")
    print("  4. Folder watcher")
    print("  5. Manual paste / stdin")

    choice = ask("What source do you want to listen to?", "1")
    mapping = {
        "1": "rss",
        "2": "json_http",
        "3": "text_file",
        "4": "folder",
        "5": "manual",
        "rss": "rss",
        "atom": "rss",
        "json": "json_http",
        "http": "json_http",
        "file": "text_file",
        "text": "text_file",
        "folder": "folder",
        "manual": "manual",
        "stdin": "manual",
    }
    source_type = mapping.get(choice.lower(), choice.lower())

    url = ""
    path = ""

    if source_type in {"rss", "json_http"}:
        url = ask("URL")
    elif source_type in {"text_file", "folder"}:
        path = ask("Path")

    back_raw = ask("How far back should the tape include? Best-effort for live sources. Use 0/none, 30m, 2h", "0")
    duration_raw = ask("How long should it record forward? Use 0/once, 60s, 10m, 2h", "60s")
    tick_raw = ask("Tick interval", "30s")

    default_base = f"loom_{source_type}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_ndjson = Path(ask("Output NDJSON tape path", f"{default_base}.tape.ndjson"))
    out_json = Path(ask("Output JSON tape path", f"{default_base}.tape.json"))

    print_rows = ask("Print rows as they arrive? y/n", "n").lower().startswith("y")

    return RecorderConfig(
        source_type=source_type,
        url=url,
        path=path,
        back_sec=parse_back_seconds(back_raw, 0),
        duration_sec=parse_duration_seconds(duration_raw, 60),
        tick_sec=max(1, parse_duration_seconds(tick_raw, 30)),
        out_ndjson=out_ndjson,
        out_json=out_json,
        write_json=True,
        print_rows=print_rows,
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Standalone Loom tape recorder wizard.")
    p.add_argument("--source", choices=["rss", "json_http", "json", "text_file", "file", "folder", "manual"], help="Source type.")
    p.add_argument("--url", help="URL for rss/json_http sources.")
    p.add_argument("--path", help="Path for text_file/folder sources.")
    p.add_argument("--back", default="0", help="How far back to include, best-effort. Examples: 0, 30m, 2h.")
    p.add_argument("--duration", default="60s", help="How long to record forward. Examples: 0, once, 60s, 10m, 2h.")
    p.add_argument("--tick", default="30s", help="Tick interval. Examples: 5s, 30s, 1m.")
    p.add_argument("--out", default="", help="Output NDJSON path.")
    p.add_argument("--json-out", default="", help="Output JSON list path.")
    p.add_argument("--no-json", action="store_true", help="Do not write JSON list tape.")
    p.add_argument("--print", dest="print_rows", action="store_true", help="Print emitted rows.")
    p.add_argument("--once", action="store_true", help="Poll once and exit.")
    return p.parse_args(argv)


def config_from_args(ns: argparse.Namespace) -> Optional[RecorderConfig]:
    if not ns.source:
        return None

    source_type = ns.source
    if source_type == "json":
        source_type = "json_http"
    if source_type == "file":
        source_type = "text_file"

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"loom_{source_type}_{stamp}"
    out_ndjson = Path(ns.out or f"{base}.tape.ndjson")
    out_json = Path(ns.json_out or f"{base}.tape.json")

    duration = 0 if ns.once else parse_duration_seconds(ns.duration, 60)

    return RecorderConfig(
        source_type=source_type,
        url=ns.url or "",
        path=ns.path or "",
        tick_sec=max(1, parse_duration_seconds(ns.tick, 30)),
        duration_sec=duration,
        back_sec=parse_back_seconds(ns.back, 0),
        out_ndjson=out_ndjson,
        out_json=out_json,
        write_json=not ns.no_json,
        print_rows=bool(ns.print_rows),
    )


def main(argv: Sequence[str] = sys.argv[1:]) -> int:
    ns = parse_args(argv)
    cfg = config_from_args(ns) or wizard()
    record(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
