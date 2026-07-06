"""MK1 power-on CLI:

    python -m oradio_engine open <file.oradio> [--steps N]   # resolve deps + run, print summary
    python -m oradio_engine club                              # show machine membership status

This is the "walk out of the cave" entry point: a tiny `.oradio` file in, a living simulation out.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from oradio_engine.bootstrap import ensure_repo_plugin_paths
from oradio_engine.club import Club
from oradio_engine.loader import open_oradio


def _cmd_open(args: argparse.Namespace) -> int:
    result = open_oradio(args.file, club=Club())
    print(f"oradio: {result.name}")
    print(f"club:   {result.report.summary()}")
    if result.report.asks:
        print("  asks (configure once — only because new/changed):")
        for a in result.report.asks:
            print(f"    - [{a.reason}] {a.capability}: {a.prompt}")
    if not result.ok:
        print("NOT READY — a required dependency is unresolved. (Optional asks never block.)")
        return 1

    eng = result.engine
    eng.run(steps=args.steps)
    print(f"\nran {args.steps} ticks → bus={len(eng.bus)} beats")
    by_source = dict(Counter(c.source for c in eng.bus))
    print(f"  beats per node: {by_source}")
    if eng.evidence is not None:
        card = eng.evidence.scorecard()
        if card["open"] + card["resolved"]:
            print(f"  evidence: {card['open']} open / {card['resolved']} resolved claims")
    print("\ntop beats:")
    for c in sorted(eng.bus, key=lambda c: c.priority, reverse=True)[:6]:
        body = (c.body or "")[:50]
        print(f"  [{c.priority:.2f}] {c.source} ({c.type}) {body}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Open an .oradio and ADVERTISE what it would consume — WITHOUT running or touching anything."""
    from oradio_engine.descriptor import OradioDescriptor
    from oradio_engine.loader import _read_descriptor_file
    desc = OradioDescriptor.from_dict(_read_descriptor_file(args.file))
    club = Club()
    print(f"oradio: {desc.name}")
    print(f"worlds:   {[w.organ for w in desc.worlds] or '(none — emerges from telemetry)'}")
    print(f"surfaces: {desc.surfaces}   club: {desc.club}")
    manifest = club.telemetry_manifest(desc)
    if not manifest:
        print("telemetry: (none — touches nothing)")
        return 0
    print("telemetry it will try to consume (NOTHING is touched until you consent):")
    for r in manifest:
        flag = "SENSITIVE" if r.sensitive else "benign"
        mark = "✓ allowed" if r.consented else ("⚠ NEEDS CONSENT" if r.sensitive else "ok")
        print(f"  - {r.name:6} [{flag:9}] {mark:16} reads: {r.reads or '(simulated)'}")
    return 0


def _cmd_club(args: argparse.Namespace) -> int:
    club = Club()
    print(f"club store: {club._store_path}")
    if not club._store:
        print("  (empty — nothing remembered yet; the club asks once and persists)")
    for key, entry in club._store.items():
        print(f"  {key}: {entry.get('value')}")
    from oradio_engine.club import DEFAULT_THEME, DEFAULT_THEME_PACKS
    print(f"built-in theme packs: {list(DEFAULT_THEME_PACKS)} (default: {DEFAULT_THEME})")
    return 0


def main(argv=None) -> int:
    ensure_repo_plugin_paths()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="oradio_engine", description="Open and run .oradio files.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="resolve deps + run an .oradio")
    p_open.add_argument("file", help="path to a .oradio (YAML/JSON) descriptor")
    p_open.add_argument("--steps", type=int, default=8, help="ticks to run")
    p_open.set_defaults(func=_cmd_open)

    p_inspect = sub.add_parser("inspect", help="advertise what an .oradio would consume (no run)")
    p_inspect.add_argument("file", help="path to a .oradio descriptor")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_club = sub.add_parser("club", help="show machine membership status")
    p_club.set_defaults(func=_cmd_club)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
