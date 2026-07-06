from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from pokemon_xy_hook import CitraMemoryClient


DEFAULT_RANGES = [
    (0x08000000, 0x08FFFFFF),
    (0x14000000, 0x143FFFFF),
]


def _parse_int(value: str | int) -> int:
    if isinstance(value, int):
        return value
    raw = str(value).strip().lower()
    return int(raw, 16) if raw.startswith("0x") else int(raw)


def _parse_ranges(values: list[str] | None) -> list[tuple[int, int]]:
    if not values:
        return list(DEFAULT_RANGES)
    ranges: list[tuple[int, int]] = []
    for value in values:
        raw = str(value).strip()
        if not raw or ":" not in raw:
            raise ValueError(f"Invalid range: {value!r}")
        start_raw, end_raw = raw.split(":", 1)
        start = _parse_int(start_raw)
        end = _parse_int(end_raw)
        if end < start:
            raise ValueError(f"Range end before start: {value!r}")
        ranges.append((start, end))
    return ranges


def _chunk_iter(start: int, end: int, chunk_size: int):
    cursor = int(start)
    final = int(end)
    while cursor <= final:
        current_size = min(chunk_size, (final - cursor) + 1)
        yield cursor, current_size
        cursor += current_size


class MemoryScanner:
    def __init__(self, client: Any) -> None:
        self.client = client

    def read_ranges(self, ranges: list[tuple[int, int]], *, chunk_size: int, delay_seconds: float = 0.0) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for start, end in ranges:
            for address, size in _chunk_iter(start, end, chunk_size):
                payload = self.client.read_memory(address, size)
                snapshots.append({"address": address, "size": size, "data": payload})
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
        return snapshots

    def search_text(self, needle: str, *, ranges: list[tuple[int, int]], chunk_size: int, encoding: str) -> list[dict[str, Any]]:
        if encoding == "utf16le":
            target = needle.encode("utf-16le")
        else:
            target = needle.encode("utf-8")
        matches: list[dict[str, Any]] = []
        for block in self.read_ranges(ranges, chunk_size=chunk_size):
            haystack = block["data"]
            base = int(block["address"])
            offset = 0
            while True:
                hit = haystack.find(target, offset)
                if hit < 0:
                    break
                matches.append({
                    "address": f"0x{base + hit:08X}",
                    "encoding": encoding,
                    "text": needle,
                })
                offset = hit + 1
        return matches

    def diff_snapshots(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        *,
        unit: int,
        limit: int,
        changed_only: bool = True,
    ) -> list[dict[str, Any]]:
        before_map = {int(block["address"]): bytes(block["data"]) for block in before}
        after_map = {int(block["address"]): bytes(block["data"]) for block in after}
        candidates: list[dict[str, Any]] = []
        for base, left in before_map.items():
            right = after_map.get(base)
            if right is None or len(left) != len(right):
                continue
            for offset in range(0, len(left) - unit + 1, unit):
                left_chunk = left[offset : offset + unit]
                right_chunk = right[offset : offset + unit]
                if changed_only and left_chunk == right_chunk:
                    continue
                candidates.append(
                    {
                        "address": f"0x{base + offset:08X}",
                        "before": int.from_bytes(left_chunk, "little", signed=False),
                        "after": int.from_bytes(right_chunk, "little", signed=False),
                        "delta": int.from_bytes(right_chunk, "little", signed=False) - int.from_bytes(left_chunk, "little", signed=False),
                        "size": unit,
                    }
                )
        candidates.sort(key=lambda item: (abs(int(item["delta"])), item["address"]))
        return candidates[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan Citra guest memory for Pokemon X/Y address discovery.")
    parser.add_argument("--citra-address", default="127.0.0.1", help="Citra UDP scripting host")
    parser.add_argument("--citra-port", type=int, default=45987, help="Citra UDP scripting port")
    parser.add_argument("--timeout-seconds", type=float, default=1.0, help="Socket timeout")
    parser.add_argument("--retries", type=int, default=2, help="Retry count for failed UDP memory reads")
    parser.add_argument("--retry-backoff-seconds", type=float, default=0.1, help="Backoff delay between UDP memory read retries")
    parser.add_argument("--range", dest="ranges", action="append", help="Address range as start:end, e.g. 0x08000000:0x0800FFFF")
    parser.add_argument("--chunk-size", type=int, default=0x1000, help="Bytes per memory read")
    parser.add_argument("--delay-ms", type=float, default=0.0, help="Delay between chunk reads in milliseconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search-text", help="Search memory for a text string")
    search_parser.add_argument("needle", help="Text to search for")
    search_parser.add_argument("--encoding", choices=["utf8", "utf16le"], default="utf16le")

    diff_parser = subparsers.add_parser("diff", help="Capture two snapshots and diff them")
    diff_parser.add_argument("--after-file", required=True, help="JSON file created by a second capture")
    diff_parser.add_argument("--before-file", help="Optional JSON file for the first capture; omit to capture now")
    diff_parser.add_argument("--unit", type=int, choices=[1, 2, 4], default=2, help="Numeric unit size when diffing")
    diff_parser.add_argument("--limit", type=int, default=200, help="Maximum number of diff candidates to return")
    diff_parser.add_argument("--output", help="Optional path to write the first capture JSON")

    capture_parser = subparsers.add_parser("capture", help="Capture raw memory blocks to a JSON file")
    capture_parser.add_argument("--output", required=True, help="Path to write the capture JSON")

    args = parser.parse_args(argv)
    ranges = _parse_ranges(args.ranges)
    client = CitraMemoryClient(
        address=args.citra_address,
        port=args.citra_port,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )
    scanner = MemoryScanner(client)
    delay_seconds = max(0.0, float(args.delay_ms)) / 1000.0
    try:
        if args.command == "search-text":
            payload = scanner.search_text(args.needle, ranges=ranges, chunk_size=args.chunk_size, encoding=args.encoding)
            print(json.dumps(payload, indent=2))
            return 0

        if args.command == "capture":
            snapshot = scanner.read_ranges(ranges, chunk_size=args.chunk_size, delay_seconds=delay_seconds)
            serializable = [{"address": item["address"], "size": item["size"], "data_hex": item["data"].hex()} for item in snapshot]
            Path(args.output).write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(Path(args.output).resolve()), "blocks": len(serializable)}, indent=2))
            return 0

        before_file = Path(args.before_file).resolve() if args.before_file else None
        if before_file is None:
            snapshot = scanner.read_ranges(ranges, chunk_size=args.chunk_size, delay_seconds=delay_seconds)
            serializable = [{"address": item["address"], "size": item["size"], "data_hex": item["data"].hex()} for item in snapshot]
            output_path = Path(args.output).resolve() if args.output else Path("pokemon_xy_memory_before.json").resolve()
            output_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
            print(json.dumps({"status": "captured", "output": str(output_path), "blocks": len(serializable)}, indent=2))
            return 0

        before_payload = json.loads(before_file.read_text(encoding="utf-8"))
        after_payload = json.loads(Path(args.after_file).resolve().read_text(encoding="utf-8"))
        before = [{"address": _parse_int(item["address"]), "data": bytes.fromhex(item["data_hex"])} for item in before_payload]
        after = [{"address": _parse_int(item["address"]), "data": bytes.fromhex(item["data_hex"])} for item in after_payload]
        diff = scanner.diff_snapshots(before, after, unit=args.unit, limit=args.limit)
        print(json.dumps(diff, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())