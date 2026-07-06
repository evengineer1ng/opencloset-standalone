from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import socket
import struct
import time
from typing import Any


CURRENT_REQUEST_VERSION = 1
MAX_REQUEST_DATA_SIZE = 32
MAX_PACKET_SIZE = 48
DEFAULT_CITRA_PORT = 45987
DEFAULT_TIMEOUT_SECONDS = 1.0
DEFAULT_PROBE_ADDRESS = 0x00100000
DEFAULT_PROBE_SIZE = 4
DEFAULT_PROFILE_PATH = Path(__file__).resolve().with_name("pokemon_xy_hook_profile.sample.json")


class CitraMemoryError(RuntimeError):
    pass


class CitraMemoryClient:
    def __init__(
        self,
        *,
        address: str = "127.0.0.1",
        port: int = DEFAULT_CITRA_PORT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = 2,
        retry_backoff_seconds: float = 0.1,
    ) -> None:
        self.address = address
        self.port = int(port)
        self.timeout_seconds = float(timeout_seconds)
        self.retries = max(0, int(retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.socket = self._open_socket()

    def _open_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout_seconds)
        return sock

    def _reset_socket(self) -> None:
        self.close()
        self.socket = self._open_socket()

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass

    def read_memory(self, address: int, size: int) -> bytes:
        remaining = int(size)
        cursor = int(address)
        payload = bytearray()
        while remaining > 0:
            chunk_size = min(remaining, MAX_REQUEST_DATA_SIZE)
            request_data = struct.pack("II", cursor, chunk_size)
            raw_reply = None
            last_error: Exception | None = None
            for attempt in range(self.retries + 1):
                request_id = int(time.time_ns() & 0xFFFFFFFF)
                request_header = struct.pack("IIII", CURRENT_REQUEST_VERSION, request_id, 1, len(request_data))
                try:
                    self.socket.sendto(request_header + request_data, (self.address, self.port))
                    raw_reply = self.socket.recv(MAX_PACKET_SIZE)
                    break
                except (ConnectionResetError, TimeoutError, OSError, socket.timeout) as exc:
                    last_error = exc
                    if attempt >= self.retries:
                        break
                    self._reset_socket()
                    if self.retry_backoff_seconds:
                        time.sleep(self.retry_backoff_seconds)

            if raw_reply is None:
                raise CitraMemoryError(
                    f"Citra scripting memory endpoint is unavailable on udp://{self.address}:{self.port}. "
                    "Open a running game and ensure the emulator build exposes the UDP scripting API before using the Pokemon hook producer."
                ) from last_error

            if len(raw_reply) < 16:
                raise CitraMemoryError("Citra scripting reply was truncated")

            version, reply_id, reply_type, reply_size = struct.unpack("IIII", raw_reply[:16])
            body = raw_reply[16:]
            if version != CURRENT_REQUEST_VERSION or reply_id != request_id or reply_type != 1 or reply_size != len(body):
                raise CitraMemoryError("Citra scripting reply header validation failed")

            payload.extend(body)
            cursor += len(body)
            remaining -= len(body)
            if len(body) == 0:
                raise CitraMemoryError("Citra scripting reply returned zero bytes")
        return bytes(payload)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000000Z"


def _load_profile(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Hook profile must contain a JSON object")
    return payload


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            return None
        return int(raw, 16) if raw.startswith("0x") else int(raw)
    raise TypeError(f"Unsupported integer value: {value!r}")


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return True


def _lookup_value(lookups: dict[str, Any], lookup_name: str | None, value: Any) -> Any:
    if not lookup_name:
        return value
    table = dict(lookups.get(lookup_name) or {})
    if not table:
        return value
    return table.get(str(value), value)


class PokemonXYMemoryHook:
    def __init__(self, client: Any, *, profile: dict[str, Any]) -> None:
        self.client = client
        self.profile = dict(profile or {})
        self.lookups = dict(self.profile.get("lookups") or {})

    def build_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "captured_at": _now_iso(),
            "emulator": {
                "name": "Citra",
                "connected": True,
                "profile": str(self.profile.get("profile") or "pokemon_xy").strip() or "pokemon_xy",
                "game": str(self.profile.get("game") or "Pokemon X/Y").strip() or "Pokemon X/Y",
            },
        }
        for section_name in ("trainer", "team", "route", "objective", "battle", "encounter"):
            section_spec = dict(self.profile.get(section_name) or {})
            if not section_spec:
                continue
            section_payload = self._read_object(section_spec)
            if _has_value(section_payload):
                snapshot[section_name] = section_payload
        return snapshot

    def probe(self, *, address: int = DEFAULT_PROBE_ADDRESS, size: int = DEFAULT_PROBE_SIZE) -> dict[str, Any]:
        payload = self.client.read_memory(address, size)
        return {
            "ok": True,
            "address": f"0x{address:08X}",
            "size": int(size),
            "hex": payload.hex(),
        }

    def _read_object(self, spec: dict[str, Any], *, base_address: int | None = None) -> dict[str, Any]:
        effective_base = self._resolve_base_address(spec, base_address)
        result: dict[str, Any] = {}
        for key, child_spec in spec.items():
            if key in {"base_address", "fields", "slot_stride", "count", "count_field", "max_slots"}:
                continue
            if key == "party" and isinstance(child_spec, dict):
                result[key] = self._read_array(child_spec, base_address=effective_base, context=result)
                continue
            if isinstance(child_spec, dict) and "fields" in child_spec:
                nested = self._read_object(dict(child_spec.get("fields") or {}), base_address=self._resolve_base_address(child_spec, effective_base))
                if _has_value(nested):
                    result[key] = nested
                continue
            if isinstance(child_spec, list):
                values = [self._read_field(item, base_address=effective_base) for item in child_spec]
                values = [item for item in values if _has_value(item)]
                if values:
                    result[key] = values
                continue
            value = self._read_field(child_spec, base_address=effective_base)
            if value is not None or child_spec.get("emit_if_empty"):
                if _has_value(value) or isinstance(value, (bool, int, float)):
                    result[key] = value
        return result

    def _read_array(self, spec: dict[str, Any], *, base_address: int | None, context: dict[str, Any]) -> list[dict[str, Any]]:
        array_base = self._resolve_base_address(spec, base_address)
        stride = _parse_int(spec.get("slot_stride")) or 0
        max_slots = _parse_int(spec.get("max_slots")) or 6
        count = _parse_int(spec.get("count"))
        if count is None:
            count_field = str(spec.get("count_field") or "").strip()
            if count_field:
                count = _parse_int(context.get(count_field))
        slot_count = max(0, min(count if count is not None else max_slots, max_slots))
        if array_base is None or stride <= 0 or slot_count <= 0:
            return []
        field_spec = dict(spec.get("fields") or {})
        entries: list[dict[str, Any]] = []
        for index in range(slot_count):
            slot_base = array_base + (index * stride)
            entry = self._read_object(field_spec, base_address=slot_base)
            if _has_value(entry):
                entries.append(entry)
        return entries

    def _read_field(self, spec: Any, *, base_address: int | None = None) -> Any:
        if not isinstance(spec, dict):
            return spec
        address = self._resolve_field_address(spec, base_address)
        if address is None:
            return None
        field_type = str(spec.get("type") or "u32").strip().lower()
        lookup_name = str(spec.get("lookup") or "").strip() or None
        if field_type in {"u8", "u16", "u32", "s8", "s16", "s32", "bool"}:
            raw_value = self._read_integer(address, field_type)
            mask = _parse_int(spec.get("mask"))
            if mask is not None:
                raw_value = int(raw_value) & int(mask)
            if field_type == "bool":
                value = bool(raw_value)
            else:
                value = _lookup_value(self.lookups, lookup_name, raw_value)
            return value
        if field_type in {"utf8", "utf-8", "utf16le", "utf-16le"}:
            text = self._read_text(address, field_type, spec)
            return _lookup_value(self.lookups, lookup_name, text)
        if field_type == "bytes":
            byte_length = _parse_int(spec.get("byte_length") or spec.get("length")) or 0
            if byte_length <= 0:
                return None
            return self.client.read_memory(address, byte_length).hex()
        raise RuntimeError(f"Unsupported hook field type: {field_type}")

    def _read_integer(self, address: int, field_type: str) -> int:
        type_map = {
            "u8": (1, "<B"),
            "u16": (2, "<H"),
            "u32": (4, "<I"),
            "s8": (1, "<b"),
            "s16": (2, "<h"),
            "s32": (4, "<i"),
            "bool": (1, "<B"),
        }
        byte_length, fmt = type_map[field_type]
        payload = self.client.read_memory(address, byte_length)
        return int(struct.unpack(fmt, payload)[0])

    def _read_text(self, address: int, field_type: str, spec: dict[str, Any]) -> str:
        char_length = _parse_int(spec.get("length")) or 0
        if char_length <= 0:
            return ""
        if field_type in {"utf16le", "utf-16le"}:
            payload = self.client.read_memory(address, char_length * 2)
            text = payload.decode("utf-16le", errors="ignore")
        else:
            payload = self.client.read_memory(address, char_length)
            text = payload.decode("utf-8", errors="ignore")
        return text.split("\x00", 1)[0].strip()

    def _resolve_base_address(self, spec: dict[str, Any], parent_base: int | None) -> int | None:
        if "base_address" in spec:
            return _parse_int(spec.get("base_address"))
        return parent_base

    def _resolve_field_address(self, spec: dict[str, Any], base_address: int | None) -> int | None:
        if "address" in spec:
            return _parse_int(spec.get("address"))
        offset = _parse_int(spec.get("offset"))
        if offset is None or base_address is None:
            return None
        return int(base_address) + int(offset)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read Pokemon X/Y state from Citra's native UDP memory scripting API.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH), help="Path to a JSON memory profile for Pokemon X/Y")
    parser.add_argument("--citra-address", default="127.0.0.1", help="Citra UDP scripting host")
    parser.add_argument("--citra-port", type=int, default=DEFAULT_CITRA_PORT, help="Citra UDP scripting port")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Socket timeout for Citra memory reads")
    parser.add_argument("--retries", type=int, default=2, help="Retry count for failed UDP memory reads")
    parser.add_argument("--retry-backoff-seconds", type=float, default=0.1, help="Backoff delay between UDP memory read retries")
    parser.add_argument("--probe", action="store_true", help="Probe the Citra scripting endpoint instead of emitting a full snapshot")
    parser.add_argument("--probe-address", default=f"0x{DEFAULT_PROBE_ADDRESS:08X}", help="Guest memory address used for --probe")
    parser.add_argument("--probe-size", type=int, default=DEFAULT_PROBE_SIZE, help="Byte count used for --probe")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the resulting JSON")
    args = parser.parse_args(argv)

    profile_path = Path(args.profile).expanduser().resolve()
    if not profile_path.exists():
        raise FileNotFoundError(profile_path)

    client = CitraMemoryClient(
        address=args.citra_address,
        port=args.citra_port,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )
    try:
        hook = PokemonXYMemoryHook(client, profile=_load_profile(profile_path))
        if args.probe:
            payload = hook.probe(address=_parse_int(args.probe_address) or DEFAULT_PROBE_ADDRESS, size=args.probe_size)
        else:
            payload = hook.build_snapshot()
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())