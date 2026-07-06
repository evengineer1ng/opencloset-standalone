from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pokemon_xy_memory_scan


class _FakeMemoryClient:
    def __init__(self, memory: dict[int, bytes]) -> None:
        self.memory = dict(memory)

    def read_memory(self, address: int, size: int) -> bytes:
        payload = self.memory.get(address)
        if payload is None:
            raise AssertionError(f"unexpected read at 0x{address:08X}")
        assert len(payload) == size
        return payload

    def close(self) -> None:
        return None


def test_search_text_finds_utf16_match():
    scanner = pokemon_xy_memory_scan.MemoryScanner(
        _FakeMemoryClient({0x08000000: "Vaniville Town".encode("utf-16le")})
    )

    matches = scanner.search_text(
        "Vaniville Town",
        ranges=[(0x08000000, 0x0800001B)],
        chunk_size=0x1C,
        encoding="utf16le",
    )

    assert matches == [{"address": "0x08000000", "encoding": "utf16le", "text": "Vaniville Town"}]


def test_read_ranges_preserves_blocks_with_delay_parameter():
    scanner = pokemon_xy_memory_scan.MemoryScanner(
        _FakeMemoryClient({0x08000000: b"ABCD", 0x08000004: b"EFGH"})
    )

    blocks = scanner.read_ranges([(0x08000000, 0x08000007)], chunk_size=4, delay_seconds=0.0)

    assert blocks == [
        {"address": 0x08000000, "size": 4, "data": b"ABCD"},
        {"address": 0x08000004, "size": 4, "data": b"EFGH"},
    ]


def test_diff_snapshots_reports_changed_values():
    scanner = pokemon_xy_memory_scan.MemoryScanner(_FakeMemoryClient({}))
    before = [{"address": 0x08000000, "data": bytes.fromhex("010002000300") }]
    after = [{"address": 0x08000000, "data": bytes.fromhex("010004000100") }]

    diff = scanner.diff_snapshots(before, after, unit=2, limit=10)

    assert diff == [
        {"address": "0x08000002", "before": 2, "after": 4, "delta": 2, "size": 2},
        {"address": "0x08000004", "before": 3, "after": 1, "delta": -2, "size": 2},
    ]


def test_parse_ranges_defaults_and_custom_values():
    assert pokemon_xy_memory_scan._parse_ranges(None) == pokemon_xy_memory_scan.DEFAULT_RANGES
    assert pokemon_xy_memory_scan._parse_ranges(["0x1000:0x10ff"]) == [(0x1000, 0x10FF)]