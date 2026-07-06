from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pokemon_xy_hook


class _FakeMemoryClient:
    def __init__(self, memory: dict[int, bytes]) -> None:
        self.memory = dict(memory)

    def read_memory(self, address: int, size: int) -> bytes:
        payload = self.memory.get(address)
        if payload is None:
            raise AssertionError(f"unexpected read at 0x{address:08X}")
        assert len(payload) == size
        return payload


def _u8(value: int) -> bytes:
    return int(value).to_bytes(1, "little", signed=False)


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=False)


def _u32(value: int) -> bytes:
    return int(value).to_bytes(4, "little", signed=False)


def _utf16(text: str, length: int) -> bytes:
    return text.encode("utf-16le") + (b"\x00" * ((length * 2) - (len(text.encode("utf-16le")))))


def test_profile_decoder_reads_team_route_battle_and_encounter_state():
    profile = {
        "profile": "pokemon_xy",
        "lookups": {
            "species": {"656": "Froakie", "283": "Surskit"},
            "battle_phase": {"1": "turn_decision"},
            "status": {"3": "burn"},
        },
        "trainer": {
            "name": {"address": "0x1000", "type": "utf16le", "length": 12},
            "badges": {"address": "0x1100", "type": "u8"},
            "pokedex_seen": {"address": "0x1102", "type": "u16"},
            "pokedex_caught": {"address": "0x1104", "type": "u16"},
            "money": {"address": "0x1108", "type": "u32"},
        },
        "route": {
            "map_name": {"address": "0x1200", "type": "utf16le", "length": 16},
            "route": {"address": "0x1240", "type": "utf16le", "length": 8},
        },
        "team": {
            "active_slot": {"address": "0x1300", "type": "u8"},
            "party_count": {"address": "0x1301", "type": "u8"},
            "party": {
                "base_address": "0x2000",
                "slot_stride": 64,
                "count_field": "party_count",
                "fields": {
                    "species": {"offset": 0, "type": "u16", "lookup": "species"},
                    "nickname": {"offset": 2, "type": "utf16le", "length": 12},
                    "level": {"offset": 26, "type": "u8"},
                    "hp": {"offset": 28, "type": "u16"},
                    "max_hp": {"offset": 30, "type": "u16"},
                    "status": {"offset": 32, "type": "u8", "lookup": "status"},
                },
            },
        },
        "battle": {
            "phase": {"address": "0x1400", "type": "u8", "lookup": "battle_phase"},
            "turn_index": {"address": "0x1402", "type": "u16"},
            "decision_required": {"address": "0x1404", "type": "bool"},
            "active_pokemon": {
                "base_address": "0x3000",
                "fields": {
                    "species": {"offset": 0, "type": "u16", "lookup": "species"},
                    "level": {"offset": 2, "type": "u8"},
                },
            },
            "opponent": {
                "base_address": "0x3040",
                "fields": {
                    "species": {"offset": 0, "type": "u16", "lookup": "species"},
                    "level": {"offset": 2, "type": "u8"},
                },
            },
        },
        "encounter": {
            "species": {"address": "0x1500", "type": "u16", "lookup": "species"},
            "level": {"address": "0x1502", "type": "u8"},
            "catch_opportunity": {"address": "0x1503", "type": "bool"},
        },
    }
    memory = {
        0x1000: _utf16("Serena", 12),
        0x1100: _u8(1),
        0x1102: _u16(24),
        0x1104: _u16(11),
        0x1108: _u32(4200),
        0x1200: _utf16("Kalos Route 3", 16),
        0x1240: _utf16("Route 3", 8),
        0x1300: _u8(1),
        0x1301: _u8(2),
        0x2000: _u16(656),
        0x2002: _utf16("Froakie", 12),
        0x201A: _u8(13),
        0x201C: _u16(31),
        0x201E: _u16(31),
        0x2020: _u8(0),
        0x2040: _u16(283),
        0x2042: _utf16("Surskit", 12),
        0x205A: _u8(10),
        0x205C: _u16(22),
        0x205E: _u16(22),
        0x2060: _u8(3),
        0x1400: _u8(1),
        0x1402: _u16(3),
        0x1404: _u8(1),
        0x3000: _u16(656),
        0x3002: _u8(13),
        0x3040: _u16(283),
        0x3042: _u8(10),
        0x1500: _u16(283),
        0x1502: _u8(10),
        0x1503: _u8(1),
    }

    hook = pokemon_xy_hook.PokemonXYMemoryHook(_FakeMemoryClient(memory), profile=profile)
    snapshot = hook.build_snapshot()

    assert snapshot["emulator"]["name"] == "Citra"
    assert snapshot["trainer"]["name"] == "Serena"
    assert snapshot["route"]["route"] == "Route 3"
    assert snapshot["team"]["party"][0]["species"] == "Froakie"
    assert snapshot["team"]["party"][1]["status"] == "burn"
    assert snapshot["battle"]["phase"] == "turn_decision"
    assert snapshot["battle"]["opponent"]["species"] == "Surskit"
    assert snapshot["encounter"]["catch_opportunity"] is True


def test_probe_surfaces_memory_bytes_as_hex():
    hook = pokemon_xy_hook.PokemonXYMemoryHook(
        _FakeMemoryClient({0x100000: bytes.fromhex("070000eb")}),
        profile={"profile": "pokemon_xy"},
    )

    payload = hook.probe(address=0x100000, size=4)

    assert payload["ok"] is True
    assert payload["hex"] == "070000eb"