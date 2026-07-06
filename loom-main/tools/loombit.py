from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loom.loombit import (  # noqa: E402
    build_index_payload_from_files,
    build_dictionary_from_files,
    compile_object,
    decode_loombit,
    inspect_file,
    load_external_dictionary,
    write_external_dictionary,
    write_loombit,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Compile and inspect canonical .loombit artifacts.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="Compile a .loom/.oradio/.json/.yaml file to .loombit")
    c.add_argument("source")
    c.add_argument("-o", "--out", default="")
    c.add_argument("--dict", default="", help="Path to external .ldict dictionary")
    c.add_argument("--strict-dict", action="store_true", help="Require all strings to exist in the external dictionary")

    d = sub.add_parser("decode", help="Decode a .loombit file to JSON")
    d.add_argument("source")
    d.add_argument("--dict", default="", help="Path to external .ldict dictionary")

    i = sub.add_parser("inspect", help="Inspect a .loombit file")
    i.add_argument("source")
    i.add_argument("--dict", default="", help="Path to external .ldict dictionary")

    b = sub.add_parser("build-dict", help="Build a shared external .ldict from source files")
    b.add_argument("sources", nargs="+")
    b.add_argument("-o", "--out", required=True)

    x = sub.add_parser("build-index", help="Build an index-loombit that points to other loombits")
    x.add_argument("artifacts", nargs="+")
    x.add_argument("-o", "--out", required=True)
    x.add_argument("--title", default="loombit index")
    x.add_argument("--branching", type=int, default=0)
    x.add_argument("--level", type=int, default=0)
    x.add_argument("--dict", default="", help="Optional external .ldict to compile against")
    x.add_argument("--strict-dict", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "compile":
        external = load_external_dictionary(args.dict) if args.dict else None
        out = write_loombit(
            args.source,
            args.out or None,
            external_dictionary=external,
            strict_external=bool(args.strict_dict),
        )
        blob = Path(out).read_bytes()
        print(f"wrote {out}")
        print(f"bytes={len(blob)}")
        return 0
    if args.cmd == "decode":
        external = load_external_dictionary(args.dict) if args.dict else None
        decoded = decode_loombit(Path(args.source).read_bytes(), external_dictionary=external)
        print(json.dumps(decoded, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "inspect":
        external = load_external_dictionary(args.dict) if args.dict else None
        print(json.dumps(inspect_file(args.source, external_dictionary=external), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "build-dict":
        ext = build_dictionary_from_files(args.sources)
        out = write_external_dictionary(ext.entries, args.out)
        print(f"wrote {out}")
        print(f"entries={len(ext.entries)}")
        print(f"checksum={ext.checksum}")
        return 0
    if args.cmd == "build-index":
        external = load_external_dictionary(args.dict) if args.dict else None
        payload = build_index_payload_from_files(
            args.artifacts,
            title=args.title,
            branching=args.branching,
            level=args.level,
        )
        blob = compile_object(
            payload,
            external_dictionary=external,
            strict_external=bool(args.strict_dict),
        )
        out = Path(args.out)
        out.write_bytes(blob)
        print(f"wrote {out}")
        print(f"bytes={len(blob)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
