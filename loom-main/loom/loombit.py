"""loombit - canonical packed bitstream for .loom / .oradio declarations.

The floor is now split cleanly:

- `.loom` / `.oradio` = authored truth
- `.ldict`            = shared external string dictionary
- `.loombit`          = canonical opcode payload, optionally string-free on disk

This module supports:

1. embedded dictionary artifacts (self-contained)
2. external dictionary artifacts (strict ID references only)
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


MAGIC = b"LBIT"
VERSION = 3
LDICT_MAGIC = b"LDT1"
LDICT_VERSION = 1

MODE_GENERIC = 0
MODE_LOOM = 1
MODE_ORADIO = 2

DICT_MODE_EMBEDDED = 0
DICT_MODE_EXTERNAL = 1

OP_NULL = 0x00
OP_FALSE = 0x01
OP_TRUE = 0x02
OP_INT = 0x03
OP_FLOAT64 = 0x04
OP_STRING = 0x05
OP_LIST = 0x06
OP_MAP = 0x07

MODE_NAMES = {
    MODE_GENERIC: "generic",
    MODE_LOOM: "loom",
    MODE_ORADIO: "oradio",
}
DICT_MODE_NAMES = {
    DICT_MODE_EMBEDDED: "embedded",
    DICT_MODE_EXTERNAL: "external",
}
INDEX_KIND = "loombit_index"


@dataclass(frozen=True)
class ExternalDictionary:
    entries: List[str]
    checksum: int


def _zigzag_encode(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _zigzag_decode(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _encode_uvarint(value: int) -> bytes:
    n = int(value)
    if n < 0:
        raise ValueError(f"uvarint cannot encode negative value {value!r}")
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _decode_uvarint(data: bytes, offset: int) -> Tuple[int, int]:
    shift = 0
    result = 0
    pos = int(offset)
    while True:
        if pos >= len(data):
            raise ValueError("unexpected EOF while decoding uvarint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("uvarint too large")


def _mode_for_path(path: str | Path) -> int:
    suffix = Path(path).suffix.lower()
    if suffix == ".loom":
        return MODE_LOOM
    if suffix == ".oradio":
        return MODE_ORADIO
    return MODE_GENERIC


def _json_scalar(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda item: str(item)):
            out[str(key)] = _canonicalize(value[key])
        return out
    return str(value)


def _collect_strings(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.add(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_strings(item, out)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            out.add(str(key))
            _collect_strings(item, out)


def _build_dictionary(value: Any) -> List[str]:
    found: set[str] = set()
    _collect_strings(value, found)
    return sorted(found)


def _dictionary_bytes(entries: List[str]) -> bytes:
    out = bytearray()
    for text in entries:
        encoded = text.encode("utf-8")
        out.extend(_encode_uvarint(len(encoded)))
        out.extend(encoded)
    return bytes(out)


def build_external_dictionary_from_objects(objects: List[Dict[str, Any]]) -> ExternalDictionary:
    found: set[str] = set()
    for obj in objects:
        _collect_strings(_canonicalize(obj), found)
    entries = sorted(found)
    body = _dictionary_bytes(entries)
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return ExternalDictionary(entries=entries, checksum=checksum)


def write_external_dictionary(entries: List[str], path: str | Path) -> Path:
    entries = sorted(set(entries))
    body = _dictionary_bytes(entries)
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    packed = zlib.compress(body, level=9)
    out = bytearray()
    out.extend(LDICT_MAGIC)
    out.append(LDICT_VERSION)
    out.extend(_encode_uvarint(len(entries)))
    out.extend(_encode_uvarint(len(body)))
    out.extend(_encode_uvarint(len(packed)))
    out.extend(packed)
    out.extend(struct.pack("<I", checksum))
    target = Path(path)
    target.write_bytes(bytes(out))
    return target


def load_external_dictionary(path: str | Path) -> ExternalDictionary:
    blob = Path(path).read_bytes()
    if len(blob) < 10 or blob[:4] != LDICT_MAGIC:
        raise ValueError("not a loombit dictionary artifact")
    version = blob[4]
    if version != LDICT_VERSION:
        raise ValueError(f"unsupported loombit dictionary version {version}")
    pos = 5
    count, pos = _decode_uvarint(blob, pos)
    raw_len, pos = _decode_uvarint(blob, pos)
    packed_len, pos = _decode_uvarint(blob, pos)
    packed_end = pos + packed_len
    if packed_end + 4 != len(blob):
        raise ValueError("loombit dictionary length mismatch")
    raw = zlib.decompress(blob[pos:packed_end])
    if len(raw) != raw_len:
        raise ValueError("loombit dictionary raw length mismatch")
    checksum = struct.unpack("<I", blob[packed_end:packed_end + 4])[0]
    if checksum != (zlib.crc32(raw) & 0xFFFFFFFF):
        raise ValueError("loombit dictionary checksum mismatch")
    entries: List[str] = []
    cur = 0
    for _ in range(count):
        item_len, cur = _decode_uvarint(raw, cur)
        item_end = cur + item_len
        entries.append(raw[cur:item_end].decode("utf-8"))
        cur = item_end
    if cur != len(raw):
        raise ValueError("loombit dictionary decode mismatch")
    return ExternalDictionary(entries=entries, checksum=checksum)


def _encode_value(value: Any, string_ids: Dict[str, int]) -> bytes:
    if value is None:
        return bytes([OP_NULL])
    if value is False:
        return bytes([OP_FALSE])
    if value is True:
        return bytes([OP_TRUE])
    if isinstance(value, int) and not isinstance(value, bool):
        return bytes([OP_INT]) + _encode_uvarint(_zigzag_encode(value))
    if isinstance(value, float):
        return bytes([OP_FLOAT64]) + struct.pack("<d", value)
    if isinstance(value, str):
        return bytes([OP_STRING]) + _encode_uvarint(string_ids[value])
    if isinstance(value, list):
        out = bytearray([OP_LIST])
        out.extend(_encode_uvarint(len(value)))
        for item in value:
            out.extend(_encode_value(item, string_ids))
        return bytes(out)
    if isinstance(value, dict):
        items = sorted(((str(key), item) for key, item in value.items()), key=lambda pair: pair[0])
        out = bytearray([OP_MAP])
        out.extend(_encode_uvarint(len(items)))
        for key, item in items:
            out.extend(_encode_uvarint(string_ids[key]))
            out.extend(_encode_value(item, string_ids))
        return bytes(out)
    return _encode_value(_json_scalar(value), string_ids)


def _decode_value(data: bytes, offset: int, dictionary: List[str]) -> Tuple[Any, int]:
    if offset >= len(data):
        raise ValueError("unexpected EOF while decoding value")
    opcode = data[offset]
    pos = offset + 1
    if opcode == OP_NULL:
        return None, pos
    if opcode == OP_FALSE:
        return False, pos
    if opcode == OP_TRUE:
        return True, pos
    if opcode == OP_INT:
        encoded, pos = _decode_uvarint(data, pos)
        return _zigzag_decode(encoded), pos
    if opcode == OP_FLOAT64:
        end = pos + 8
        if end > len(data):
            raise ValueError("unexpected EOF while decoding float64")
        return struct.unpack("<d", data[pos:end])[0], end
    if opcode == OP_STRING:
        idx, pos = _decode_uvarint(data, pos)
        return dictionary[idx], pos
    if opcode == OP_LIST:
        count, pos = _decode_uvarint(data, pos)
        out: List[Any] = []
        for _ in range(count):
            item, pos = _decode_value(data, pos, dictionary)
            out.append(item)
        return out, pos
    if opcode == OP_MAP:
        count, pos = _decode_uvarint(data, pos)
        out: Dict[str, Any] = {}
        for _ in range(count):
            key_idx, pos = _decode_uvarint(data, pos)
            key = dictionary[key_idx]
            item, pos = _decode_value(data, pos, dictionary)
            out[key] = item
        return out, pos
    raise ValueError(f"unknown loombit opcode {opcode}")


def compile_object(
    data: Dict[str, Any],
    *,
    mode: int = MODE_GENERIC,
    external_dictionary: ExternalDictionary | None = None,
    strict_external: bool = False,
) -> bytes:
    canonical = _canonicalize(data)
    needed = _build_dictionary(canonical)
    if external_dictionary is not None:
        dictionary = list(external_dictionary.entries)
        missing = [text for text in needed if text not in set(dictionary)]
        if missing:
            if strict_external:
                raise ValueError(
                    "external loombit dictionary is missing required strings: "
                    + ", ".join(repr(item) for item in missing[:8])
                )
            dictionary = sorted(set(dictionary).union(needed))
            dict_mode = DICT_MODE_EMBEDDED
            dict_checksum = zlib.crc32(_dictionary_bytes(dictionary)) & 0xFFFFFFFF
        else:
            dict_mode = DICT_MODE_EXTERNAL
            dict_checksum = external_dictionary.checksum
    else:
        dictionary = needed
        dict_mode = DICT_MODE_EMBEDDED
        dict_checksum = zlib.crc32(_dictionary_bytes(dictionary)) & 0xFFFFFFFF

    string_ids = {text: idx for idx, text in enumerate(dictionary)}
    payload = _encode_value(canonical, string_ids)
    header = bytearray()
    header.extend(MAGIC)
    header.append(VERSION)
    header.append(mode)
    header.append(dict_mode)
    header.extend(struct.pack("<I", dict_checksum))

    if dict_mode == DICT_MODE_EMBEDDED:
        dict_bytes = _dictionary_bytes(dictionary)
        raw_sections = dict_bytes + payload
        packed = zlib.compress(raw_sections, level=9)
        header.extend(_encode_uvarint(len(dictionary)))
        header.extend(_encode_uvarint(len(dict_bytes)))
        header.extend(_encode_uvarint(len(payload)))
        header.extend(_encode_uvarint(len(packed)))
        body = bytes(header) + packed
    else:
        packed = zlib.compress(payload, level=9)
        header.extend(_encode_uvarint(len(dictionary)))
        header.extend(_encode_uvarint(len(payload)))
        header.extend(_encode_uvarint(len(packed)))
        body = bytes(header) + packed

    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack("<I", checksum)


def decode_loombit(blob: bytes, *, external_dictionary: ExternalDictionary | None = None) -> Dict[str, Any]:
    if len(blob) < 12 or blob[:4] != MAGIC:
        raise ValueError("not a loombit artifact")
    version = blob[4]
    if version != VERSION:
        raise ValueError(f"unsupported loombit version {version}")
    mode = blob[5]
    dict_mode = blob[6]
    dict_checksum = struct.unpack("<I", blob[7:11])[0]
    pos = 11

    if dict_mode == DICT_MODE_EMBEDDED:
        dict_count, pos = _decode_uvarint(blob, pos)
        dict_len, pos = _decode_uvarint(blob, pos)
        payload_len, pos = _decode_uvarint(blob, pos)
        packed_len, pos = _decode_uvarint(blob, pos)
        packed_end = pos + packed_len
        payload_end = packed_end
        checksum_end = payload_end + 4
        if checksum_end != len(blob):
            raise ValueError("loombit length mismatch")
        raw_sections = zlib.decompress(blob[pos:packed_end])
        if len(raw_sections) != dict_len + payload_len:
            raise ValueError("embedded loombit raw section length mismatch")
        if (zlib.crc32(raw_sections[:dict_len]) & 0xFFFFFFFF) != dict_checksum:
            raise ValueError("embedded loombit dictionary checksum mismatch")
        dictionary: List[str] = []
        cur = 0
        for _ in range(dict_count):
            item_len, cur = _decode_uvarint(raw_sections, cur)
            item_end = cur + item_len
            dictionary.append(raw_sections[cur:item_end].decode("utf-8"))
            cur = item_end
        payload = raw_sections[dict_len:dict_len + payload_len]
    elif dict_mode == DICT_MODE_EXTERNAL:
        dict_count, pos = _decode_uvarint(blob, pos)
        payload_len, pos = _decode_uvarint(blob, pos)
        packed_len, pos = _decode_uvarint(blob, pos)
        packed_end = pos + packed_len
        payload_end = packed_end
        checksum_end = payload_end + 4
        if checksum_end != len(blob):
            raise ValueError("loombit length mismatch")
        if external_dictionary is None:
            raise ValueError("external dictionary required to decode this loombit artifact")
        if external_dictionary.checksum != dict_checksum:
            raise ValueError("wrong external dictionary for this loombit artifact")
        dictionary = list(external_dictionary.entries)
        if len(dictionary) != dict_count:
            raise ValueError("external dictionary entry count mismatch")
        payload = zlib.decompress(blob[pos:packed_end])
        if len(payload) != payload_len:
            raise ValueError("external loombit payload length mismatch")
    else:
        raise ValueError(f"unknown loombit dictionary mode {dict_mode}")

    expected_checksum = zlib.crc32(blob[:payload_end]) & 0xFFFFFFFF
    checksum = struct.unpack("<I", blob[payload_end:checksum_end])[0]
    data, final_pos = _decode_value(payload, 0, dictionary)
    if final_pos != len(payload):
        raise ValueError("payload did not decode cleanly")
    return {
        "version": version,
        "mode": mode,
        "mode_name": MODE_NAMES.get(mode, "generic"),
        "dictionary_mode": DICT_MODE_NAMES.get(dict_mode, "unknown"),
        "dictionary_entries": len(dictionary),
        "dictionary_checksum": dict_checksum,
        "dictionary": dictionary if dict_mode == DICT_MODE_EMBEDDED else [],
        "payload": data,
        "checksum": checksum,
        "checksum_ok": checksum == expected_checksum,
    }


def read_source_file(path: str | Path) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


def build_dictionary_from_files(paths: List[str | Path]) -> ExternalDictionary:
    objects: List[Dict[str, Any]] = []
    for path in paths:
        source = read_source_file(path)
        if not isinstance(source, dict):
            raise ValueError(f"loombit source must decode to a mapping, got {type(source).__name__}")
        objects.append(source)
    return build_external_dictionary_from_objects(objects)


def _short_checksum(blob: bytes) -> str:
    return f"{zlib.crc32(blob) & 0xFFFFFFFF:08x}"


def _class_for_path(path: str) -> str:
    lowered = str(path or "").lower()
    if lowered.endswith(".lbpack"):
        return "lbpack"
    if lowered.endswith(".idx"):
        return "lbidx"
    if lowered.endswith(".loombit"):
        return "loombit"
    if lowered.endswith(".oradio"):
        return "oradio"
    if lowered.endswith(".loom"):
        return "loom"
    return "generic"


def build_index_payload(
    items: List[Dict[str, Any]],
    *,
    title: str = "",
    branching: int = 0,
    level: int = 0,
) -> Dict[str, Any]:
    """Build a recursive loombit index payload.

    Each item should already be small and routing-oriented. The point is to let
    a loombit point to other loombits without inventing another container format.
    """
    entries: List[Dict[str, Any]] = []
    classes: Dict[str, int] = {}
    for item in items:
        path = str(item.get("path") or item.get("artifact") or "")
        node_class = str(item.get("class") or _class_for_path(path))
        classes[node_class] = classes.get(node_class, 0) + 1
        entries.append(
            {
                "id": str(item.get("id") or Path(path).stem or f"node_{len(entries)}"),
                "path": path,
                "class": node_class,
                "topic": str(item.get("topic") or ""),
                "summary": str(item.get("summary") or ""),
                "bucket": str(item.get("bucket") or ""),
                "gradient": str(item.get("gradient") or ""),
                "tags": list(item.get("tags") or []),
                "checksum": str(item.get("checksum") or ""),
                "children": list(item.get("children") or []),
            }
        )
    return {
        "kind": INDEX_KIND,
        "title": str(title or "loombit index"),
        "level": int(level),
        "branching": int(branching or 0),
        "entry_count": len(entries),
        "class_counts": dict(sorted(classes.items())),
        "entries": entries,
    }


def build_index_payload_from_files(
    artifact_paths: List[str | Path],
    *,
    title: str = "",
    branching: int = 0,
    level: int = 0,
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for raw_path in artifact_paths:
        path = Path(raw_path)
        blob = path.read_bytes()
        node_class = _class_for_path(path.name)
        if node_class == "loombit":
            try:
                decoded = decode_loombit(blob)
                node_class = str(decoded.get("payload", {}).get("kind") or decoded.get("mode_name") or "loombit")
            except Exception:
                node_class = "loombit"
        entry = {
            "id": path.stem,
            "path": path.name,
            "class": node_class,
            "checksum": _short_checksum(blob),
            "summary": f"{path.suffix} {len(blob)} bytes",
        }
        entries.append(entry)
    return build_index_payload(entries, title=title or "loombit artifact index", branching=branching, level=level)


def compile_file(
    path: str | Path,
    *,
    external_dictionary: ExternalDictionary | None = None,
    strict_external: bool = False,
) -> bytes:
    source = read_source_file(path)
    if not isinstance(source, dict):
        raise ValueError(f"loombit source must decode to a mapping, got {type(source).__name__}")
    return compile_object(
        source,
        mode=_mode_for_path(path),
        external_dictionary=external_dictionary,
        strict_external=strict_external,
    )


def write_loombit(
    source_path: str | Path,
    output_path: str | Path | None = None,
    *,
    external_dictionary: ExternalDictionary | None = None,
    strict_external: bool = False,
) -> Path:
    source_path = Path(source_path)
    output = Path(output_path) if output_path else source_path.with_suffix(source_path.suffix + ".loombit")
    output.write_bytes(
        compile_file(source_path, external_dictionary=external_dictionary, strict_external=strict_external)
    )
    return output


def quadrant_cells_from_bytes(blob: bytes) -> List[Dict[str, int]]:
    nibbles: List[int] = []
    for byte in blob:
        nibbles.append((byte >> 4) & 0x0F)
        nibbles.append(byte & 0x0F)
    cells: List[Dict[str, int]] = []
    for i in range(0, len(nibbles), 3):
        a = nibbles[i]
        b = nibbles[i + 1] if i + 1 < len(nibbles) else 0
        c = nibbles[i + 2] if i + 2 < len(nibbles) else 0
        ecc = (a ^ b ^ c) & 0x0F
        cells.append({"nw": a, "ne": b, "sw": c, "se": ecc})
    return cells


def color_cells_from_bytes(blob: bytes) -> List[Dict[str, int]]:
    """Project canonical loombit bytes into four deterministic visual channels.

    R = routing / index emphasis
    G = semantic / dictionary emphasis
    B = payload / detail emphasis
    K = parity / alignment
    """

    cells: List[Dict[str, int]] = []
    for i in range(0, len(blob), 3):
        a = blob[i]
        b = blob[i + 1] if i + 1 < len(blob) else 0
        c = blob[i + 2] if i + 2 < len(blob) else 0
        r = ((a >> 4) ^ (b & 0x0F)) & 0x0F
        g = ((b >> 4) ^ (c & 0x0F)) & 0x0F
        blue = ((c >> 4) ^ (a & 0x0F)) & 0x0F
        k = (r ^ g ^ blue) & 0x0F
        cells.append({"r": r, "g": g, "b": blue, "k": k})
    return cells


def lens_cells_from_color_cells(cells: List[Dict[str, int]], lens: str) -> List[int]:
    lens_key = str(lens or "").lower()
    key = "r" if lens_key in {"r", "red", "routing", "route"} else \
        "g" if lens_key in {"g", "green", "semantic", "class"} else \
        "b" if lens_key in {"b", "blue", "detail", "payload"} else \
        "k"
    return [int(cell.get(key, 0)) for cell in cells]


def color_grid_dimensions(cell_count: int) -> Tuple[int, int]:
    width = max(1, int(math.ceil(math.sqrt(max(1, cell_count)))))
    height = max(1, int(math.ceil(cell_count / width)))
    return width, height


def inspect_blob(blob: bytes, *, external_dictionary: ExternalDictionary | None = None) -> Dict[str, Any]:
    decoded = decode_loombit(blob, external_dictionary=external_dictionary)
    cells = quadrant_cells_from_bytes(blob)
    color_cells = color_cells_from_bytes(blob)
    return {
        "mode": decoded["mode_name"],
        "dictionary_mode": decoded["dictionary_mode"],
        "dictionary_checksum": decoded["dictionary_checksum"],
        "checksum_ok": decoded["checksum_ok"],
        "dictionary_entries": decoded["dictionary_entries"],
        "bytes": len(blob),
        "quadrant_cells": len(cells),
        "quadrant_preview": cells[:8],
        "color_cells": len(color_cells),
        "color_preview": color_cells[:8],
        "color_grid": {"width": color_grid_dimensions(len(color_cells))[0], "height": color_grid_dimensions(len(color_cells))[1]},
        "red_lens_preview": lens_cells_from_color_cells(color_cells[:12], "red"),
        "green_lens_preview": lens_cells_from_color_cells(color_cells[:12], "green"),
        "blue_lens_preview": lens_cells_from_color_cells(color_cells[:12], "blue"),
        "payload": decoded["payload"],
    }


def inspect_file(path: str | Path, *, external_dictionary: ExternalDictionary | None = None) -> Dict[str, Any]:
    return inspect_blob(Path(path).read_bytes(), external_dictionary=external_dictionary)
