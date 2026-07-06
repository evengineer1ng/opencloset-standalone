"""Bake an RSS feed into a thin-wire tape — a proper feed type, pure stdlib (urllib + xml).

    python -m tools.bake_rss --url https://www.autosport.com/rss/feed/f1 --name f1news --out data/rss_f1news.json

Headlines carry no roles, so the booth speaks them verbatim (a news lane in the mix). One baker
per feed type; they all feed the same antenna.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET


def _clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--priority", type=float, default=0.6)
    args = ap.parse_args()

    req = urllib.request.Request(args.url, headers={"User-Agent": "Mozilla/5.0 (oradio)"})
    xml = urllib.request.urlopen(req, timeout=20).read()
    root = ET.fromstring(xml)

    rows = []
    for item in root.findall(".//item")[:args.limit]:
        title = _clean(item.findtext("title") or "")
        if not title:
            continue
        rows.append({"title": title, "body": title, "type": "news",
                     "priority": args.priority, "tags": ["news", "rss", args.name]})

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"baked {len(rows)} headlines from {args.name} -> {args.out}")


if __name__ == "__main__":
    main()
