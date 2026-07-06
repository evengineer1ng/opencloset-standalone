"""Wikipedia multistream index -> loombit routing substrate compiler."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple

from .loombit import (
    _decode_uvarint,
    _encode_uvarint,
    _short_checksum,
    color_cells_from_bytes,
    compile_object,
    lens_cells_from_color_cells,
    load_external_dictionary,
    quadrant_cells_from_bytes,
    write_external_dictionary,
)


WIKIPEDIA_INDEX_KIND = "wikipedia_index_entry"
LBPACK_MAGIC = b"LBPK"
LBPACK_VERSION = 1
LBIDX_MAGIC = b"LIDX"
LBIDX_VERSION = 1
ROOT_INDEX_KIND = "loombit_index"

MEDIAWIKI_NAMESPACES = {
    "media",
    "special",
    "talk",
    "user",
    "user talk",
    "wikipedia",
    "wikipedia talk",
    "file",
    "file talk",
    "mediawiki",
    "mediawiki talk",
    "template",
    "template talk",
    "help",
    "help talk",
    "category",
    "category talk",
    "portal",
    "portal talk",
    "book",
    "book talk",
    "draft",
    "draft talk",
    "education program",
    "education program talk",
    "timedtext",
    "timedtext talk",
    "module",
    "module talk",
    "gadget",
    "gadget talk",
    "gadget definition",
    "gadget definition talk",
    "topic",
}


@dataclass
class IndexRow:
    offset: int
    page_id: int
    title: str


def _readable_snapshot(snapshot: str) -> str:
    raw = str(snapshot or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def infer_snapshot_from_path(path: str | Path) -> str:
    text = str(path)
    match = re.search(r"enwiki-(\d{8})-pages-articles-multistream-index\.txt", text)
    if match:
        return _readable_snapshot(match.group(1))
    return "unknown-snapshot"


def normalize_title(title: str) -> str:
    lowered = str(title or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "untitled"


def detect_namespace(title: str) -> str:
    text = str(title or "")
    if ":" not in text:
        return ""
    prefix = text.split(":", 1)[0].strip().replace("_", " ")
    lowered = prefix.lower()
    if lowered in MEDIAWIKI_NAMESPACES:
        return prefix
    return ""


def shard_for_normalized_title(normalized_title: str) -> str:
    alnum = "".join(ch for ch in str(normalized_title or "") if ch.isalnum())
    if not alnum:
        return "title-sym"
    first = alnum[0]
    if first.isdigit():
        return "title-num"
    if first.isalpha():
        second = alnum[1] if len(alnum) > 1 and alnum[1].isalpha() else "_"
        return f"title-{first}{second}"
    return "title-sym"


def leaf_slug(normalized_title: str, page_id: int) -> str:
    return f"{normalized_title}~{int(page_id)}"


def stable_leaf_id(snapshot: str, shard: str, normalized_title: str, page_id: int) -> str:
    return f"loomkb:v1:wikipedia:index:{snapshot}:{shard}:{leaf_slug(normalized_title, page_id)}"


def parse_index_line(line: str, *, snapshot: str, source_ref: str) -> Dict[str, Any]:
    raw = str(line or "").rstrip("\n")
    offset_text, page_text, title = raw.split(":", 2)
    offset = int(offset_text)
    page_id = int(page_text)
    namespace = detect_namespace(title)
    normalized = normalize_title(title)
    shard = shard_for_normalized_title(normalized)
    bucket = f"wikipedia/{namespace.lower()}" if namespace else "wikipedia/article"
    gradient = f"wikipedia/index/{shard}"
    tags = [f"shard:{shard}", "source:wikipedia-index"]
    if namespace:
        tags.append(f"namespace:{namespace.lower().replace(' ', '_')}")
    first_token = normalized.split("-", 1)[0] if normalized else ""
    if first_token:
        tags.append(f"prefix:{first_token[:4]}")
    return {
        "kind": WIKIPEDIA_INDEX_KIND,
        "id": stable_leaf_id(snapshot, shard, normalized, page_id),
        "title": title,
        "normalized_title": normalized,
        "page_id": page_id,
        "offset": offset,
        "namespace": namespace,
        "bucket": bucket,
        "gradient": gradient,
        "tags": tags,
        "source_snapshot": snapshot,
        "source_ref": source_ref,
        "shard_key": shard,
    }


def iter_index_rows(
    source_path: str | Path,
    *,
    snapshot: str = "",
    max_entries: int | None = None,
) -> Iterator[Dict[str, Any]]:
    path = Path(source_path)
    resolved_snapshot = _readable_snapshot(snapshot or infer_snapshot_from_path(path))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for idx, line in enumerate(handle):
            if max_entries is not None and idx >= int(max_entries):
                break
            text = line.strip()
            if not text:
                continue
            yield parse_index_line(text, snapshot=resolved_snapshot, source_ref=path.name)


def _surface_preview(blob: bytes) -> Dict[str, Any]:
    colors = color_cells_from_bytes(blob)
    return {
        "bytes": len(blob),
        "checksum": _short_checksum(blob),
        "quadrant_cells": len(quadrant_cells_from_bytes(blob)),
        "color_cells": len(colors),
        "quadrant_preview": quadrant_cells_from_bytes(blob)[:6],
        "color_preview": colors[:6],
        "red_lens": lens_cells_from_color_cells(colors[:18], "red"),
        "green_lens": lens_cells_from_color_cells(colors[:18], "green"),
        "blue_lens": lens_cells_from_color_cells(colors[:18], "blue"),
        "black_lens": lens_cells_from_color_cells(colors[:18], "black"),
    }


def _file_checksum(path: str | Path) -> str:
    crc = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08x}"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _tokenize(text: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", str(text or "").lower()) if token]


def _write_external_strings(strings: Iterable[str], path: Path) -> List[str]:
    entries = sorted(set(str(item or "") for item in strings))
    write_external_dictionary(entries, path)
    return entries


def _pack_string_id_map(entries: List[str]) -> Dict[str, int]:
    return {value: idx for idx, value in enumerate(entries)}


def encode_pack_record(leaf: Dict[str, Any], string_ids: Dict[str, int]) -> bytes:
    title = str(leaf["title"]).encode("utf-8")
    normalized = str(leaf["normalized_title"]).encode("utf-8")
    out = bytearray()
    out.extend(_encode_uvarint(len(title)))
    out.extend(title)
    out.extend(_encode_uvarint(len(normalized)))
    out.extend(normalized)
    out.extend(_encode_uvarint(int(leaf["page_id"])))
    out.extend(_encode_uvarint(int(leaf["offset"])))
    out.extend(_encode_uvarint(string_ids[str(leaf.get("namespace") or "")]))
    out.extend(_encode_uvarint(string_ids[str(leaf.get("bucket") or "")]))
    out.extend(_encode_uvarint(string_ids[str(leaf.get("gradient") or "")]))
    out.extend(_encode_uvarint(string_ids[str(leaf.get("source_snapshot") or "")]))
    out.extend(_encode_uvarint(string_ids[str(leaf.get("source_ref") or "")]))
    tags = [str(tag) for tag in list(leaf.get("tags") or [])]
    out.extend(_encode_uvarint(len(tags)))
    for tag in tags:
        out.extend(_encode_uvarint(string_ids[tag]))
    return bytes(out)


def decode_pack_record(data: bytes, offset: int, dictionary_entries: List[str]) -> Tuple[Dict[str, Any], int]:
    pos = int(offset)
    title_len, pos = _decode_uvarint(data, pos)
    title = data[pos:pos + title_len].decode("utf-8")
    pos += title_len
    normalized_len, pos = _decode_uvarint(data, pos)
    normalized = data[pos:pos + normalized_len].decode("utf-8")
    pos += normalized_len
    page_id, pos = _decode_uvarint(data, pos)
    source_offset, pos = _decode_uvarint(data, pos)
    namespace_id, pos = _decode_uvarint(data, pos)
    bucket_id, pos = _decode_uvarint(data, pos)
    gradient_id, pos = _decode_uvarint(data, pos)
    snapshot_id, pos = _decode_uvarint(data, pos)
    source_ref_id, pos = _decode_uvarint(data, pos)
    tag_count, pos = _decode_uvarint(data, pos)
    tags: List[str] = []
    for _ in range(tag_count):
        tag_id, pos = _decode_uvarint(data, pos)
        tags.append(dictionary_entries[tag_id])
    leaf = {
        "kind": WIKIPEDIA_INDEX_KIND,
        "title": title,
        "normalized_title": normalized,
        "page_id": page_id,
        "offset": source_offset,
        "namespace": dictionary_entries[namespace_id],
        "bucket": dictionary_entries[bucket_id],
        "gradient": dictionary_entries[gradient_id],
        "source_snapshot": dictionary_entries[snapshot_id],
        "source_ref": dictionary_entries[source_ref_id],
        "tags": tags,
    }
    return leaf, pos


def write_lbpack(
    leaves: Iterable[Dict[str, Any]],
    *,
    pack_path: str | Path,
    idx_path: str | Path,
    dict_path: str | Path,
) -> Dict[str, Any]:
    leaf_list = list(leaves)
    strings = set()
    for leaf in leaf_list:
        strings.add(str(leaf.get("namespace") or ""))
        strings.add(str(leaf.get("bucket") or ""))
        strings.add(str(leaf.get("gradient") or ""))
        strings.add(str(leaf.get("source_snapshot") or ""))
        strings.add(str(leaf.get("source_ref") or ""))
        for tag in leaf.get("tags") or []:
            strings.add(str(tag))
    dictionary_entries = _write_external_strings(strings, Path(dict_path))
    dictionary = load_external_dictionary(dict_path)
    string_ids = _pack_string_id_map(dictionary_entries)
    header = bytearray()
    header.extend(LBPACK_MAGIC)
    header.append(LBPACK_VERSION)
    header.extend(int(dictionary.checksum).to_bytes(4, "little"))
    header.extend(_encode_uvarint(len(leaf_list)))
    pack_file = Path(pack_path)
    idx_file = Path(idx_path)
    entries: List[Dict[str, Any]] = []
    with pack_file.open("wb") as handle:
        handle.write(bytes(header))
        current_offset = len(header)
        for leaf in leaf_list:
            record = encode_pack_record(leaf, string_ids)
            checksum = zlib.crc32(record) & 0xFFFFFFFF
            handle.write(record)
            entries.append(
                {
                    "id": str(leaf["id"]),
                    "offset": current_offset,
                    "length": len(record),
                    "checksum": checksum,
                }
            )
            current_offset += len(record)
    write_lbidx(entries, idx_file)
    pack_blob = pack_file.read_bytes()
    idx_blob = idx_file.read_bytes()
    return {
        "pack_path": str(pack_file),
        "idx_path": str(idx_file),
        "dict_path": str(dict_path),
        "entry_count": len(entries),
        "pack_checksum": _short_checksum(pack_blob),
        "idx_checksum": _short_checksum(idx_blob),
        "dict_checksum": f"{dictionary.checksum:08x}",
        "pack_surface": _surface_preview(pack_blob),
        "idx_surface": _surface_preview(idx_blob),
    }


def write_lbidx(entries: List[Dict[str, Any]], idx_path: str | Path) -> Path:
    out = bytearray()
    out.extend(LBIDX_MAGIC)
    out.append(LBIDX_VERSION)
    out.extend(_encode_uvarint(len(entries)))
    for entry in entries:
        encoded_id = str(entry["id"]).encode("utf-8")
        out.extend(_encode_uvarint(len(encoded_id)))
        out.extend(encoded_id)
        out.extend(_encode_uvarint(int(entry["offset"])))
        out.extend(_encode_uvarint(int(entry["length"])))
        out.extend(int(entry["checksum"]).to_bytes(4, "little"))
    target = Path(idx_path)
    target.write_bytes(bytes(out))
    return target


def read_lbidx(idx_path: str | Path) -> List[Dict[str, Any]]:
    blob = Path(idx_path).read_bytes()
    if len(blob) < 6 or blob[:4] != LBIDX_MAGIC:
        raise ValueError("not an lbidx artifact")
    if blob[4] != LBIDX_VERSION:
        raise ValueError(f"unsupported lbidx version {blob[4]}")
    pos = 5
    count, pos = _decode_uvarint(blob, pos)
    entries: List[Dict[str, Any]] = []
    for _ in range(count):
        id_len, pos = _decode_uvarint(blob, pos)
        item_id = blob[pos:pos + id_len].decode("utf-8")
        pos += id_len
        offset, pos = _decode_uvarint(blob, pos)
        length, pos = _decode_uvarint(blob, pos)
        checksum = int.from_bytes(blob[pos:pos + 4], "little")
        pos += 4
        entries.append({"id": item_id, "offset": offset, "length": length, "checksum": checksum})
    return entries


def recover_lbpack_record(
    pack_path: str | Path,
    idx_path: str | Path,
    dict_path: str | Path,
    leaf_id: str,
) -> Dict[str, Any]:
    entries = read_lbidx(idx_path)
    hit = next((entry for entry in entries if entry["id"] == leaf_id), None)
    if hit is None:
        raise KeyError(leaf_id)
    blob = Path(pack_path).read_bytes()
    dictionary = load_external_dictionary(dict_path)
    record, end = decode_pack_record(blob, int(hit["offset"]), list(dictionary.entries))
    payload = blob[int(hit["offset"]):end]
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    if checksum != int(hit["checksum"]):
        raise ValueError("lbpack record checksum mismatch")
    record["id"] = leaf_id
    record["shard_key"] = shard_for_normalized_title(str(record["normalized_title"]))
    return record


def _build_shard_index_payload(
    *,
    snapshot: str,
    shard_key: str,
    entry_count: int,
    title_min: str,
    title_max: str,
    pack_rel: str,
    idx_rel: str,
    dict_rel: str,
    checksums: Dict[str, str],
    routing_tokens: List[str],
) -> Dict[str, Any]:
    return {
        "kind": ROOT_INDEX_KIND,
        "title": f"Wikipedia shard {shard_key}",
        "level": 1,
        "branching": 0,
        "snapshot": snapshot,
        "time_span": snapshot,
        "entry_count": entry_count,
        "class_counts": {"lbpack": 1, "lbidx": 1},
        "title_range": {"min": title_min, "max": title_max},
        "entries": [
            {
                "id": f"{shard_key}-bank",
                "path": pack_rel,
                "class": "lbpack",
                "topic": f"{title_min} -> {title_max}",
                "summary": f"{entry_count} wikipedia index entries",
                "bucket": "wikipedia/index",
                "gradient": f"wikipedia/index/{shard_key}",
                "tags": routing_tokens,
                "checksum": checksums["pack"],
                "children": [],
                "bank_paths": [pack_rel],
                "dict_paths": [dict_rel],
                "offset_paths": [idx_rel],
                "routing_tokens": routing_tokens,
                "snapshot": snapshot,
            }
        ],
    }


def _build_root_index_payload(snapshot: str, shard_entries: List[Dict[str, Any]], total_entries: int) -> Dict[str, Any]:
    return {
        "kind": ROOT_INDEX_KIND,
        "title": f"Wikipedia index root {snapshot}",
        "level": 0,
        "branching": len(shard_entries),
        "snapshot": snapshot,
        "time_span": snapshot,
        "entry_count": len(shard_entries),
        "total_entries": total_entries,
        "class_counts": {"wikipedia_shard_index": len(shard_entries)},
        "entries": shard_entries,
    }


def compile_wikipedia_index(
    source_path: str | Path,
    *,
    outdir: str | Path,
    snapshot: str = "",
    max_entries: int | None = None,
) -> Dict[str, Any]:
    source = Path(source_path)
    resolved_snapshot = _readable_snapshot(snapshot or infer_snapshot_from_path(source))
    snapshot_root = Path(outdir) / "snapshots" / resolved_snapshot
    packs_dir = snapshot_root / "packs"
    shards_dir = snapshot_root / "shards"
    dicts_dir = snapshot_root / "dicts"
    manifests_dir = snapshot_root / "manifests"
    for path in (packs_dir, shards_dir, dicts_dir, manifests_dir):
        path.mkdir(parents=True, exist_ok=True)

    totals = {
        "entries": 0,
        "artifact_bytes": defaultdict(int),
    }
    shard_stats: Dict[str, Dict[str, Any]] = {}
    temp_root = Path(tempfile.mkdtemp(prefix="wiki-index-loombit-"))
    try:
        for leaf in iter_index_rows(source, snapshot=resolved_snapshot, max_entries=max_entries):
            shard_key = str(leaf["shard_key"])
            stats = shard_stats.setdefault(
                shard_key,
                {
                    "entry_count": 0,
                    "title_min": str(leaf["normalized_title"]),
                    "title_max": str(leaf["normalized_title"]),
                    "routing": Counter(),
                    "temp_path": temp_root / f"{shard_key}.ndjson",
                },
            )
            stats["entry_count"] += 1
            stats["title_min"] = min(str(stats["title_min"]), str(leaf["normalized_title"]))
            stats["title_max"] = max(str(stats["title_max"]), str(leaf["normalized_title"]))
            tokens = _tokenize(str(leaf["normalized_title"]))
            if tokens:
                stats["routing"][tokens[0]] += 1
            with stats["temp_path"].open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(leaf, ensure_ascii=False, sort_keys=True) + "\n")
            totals["entries"] += 1

        shard_entries: List[Dict[str, Any]] = []
        for shard_key in sorted(shard_stats.keys()):
            stats = shard_stats[shard_key]
            leaves: List[Dict[str, Any]] = []
            with Path(stats["temp_path"]).open("r", encoding="utf-8") as handle:
                for line in handle:
                    leaves.append(json.loads(line))
            pack_path = packs_dir / f"{shard_key}.lbpack"
            idx_path = packs_dir / f"{shard_key}.idx"
            dict_path = dicts_dir / f"{shard_key}.ldict"
            artifact = write_lbpack(leaves, pack_path=pack_path, idx_path=idx_path, dict_path=dict_path)
            shard_rel_pack = str(pack_path.relative_to(snapshot_root)).replace("\\", "/")
            shard_rel_idx = str(idx_path.relative_to(snapshot_root)).replace("\\", "/")
            shard_rel_dict = str(dict_path.relative_to(snapshot_root)).replace("\\", "/")
            routing_tokens = [token for token, _count in stats["routing"].most_common(8)]
            shard_payload = _build_shard_index_payload(
                snapshot=resolved_snapshot,
                shard_key=shard_key,
                entry_count=stats["entry_count"],
                title_min=stats["title_min"],
                title_max=stats["title_max"],
                pack_rel=shard_rel_pack,
                idx_rel=shard_rel_idx,
                dict_rel=shard_rel_dict,
                checksums={
                    "pack": artifact["pack_checksum"],
                    "idx": artifact["idx_checksum"],
                    "dict": artifact["dict_checksum"],
                },
                routing_tokens=routing_tokens,
            )
            shard_index_path = shards_dir / f"{shard_key}.index.loombit"
            shard_index_blob = compile_object(shard_payload)
            shard_index_path.write_bytes(shard_index_blob)
            shard_entries.append(
                {
                    "id": shard_key,
                    "path": str(shard_index_path.relative_to(snapshot_root)).replace("\\", "/"),
                    "class": "wikipedia_shard_index",
                    "topic": f"{stats['title_min']} -> {stats['title_max']}",
                    "summary": f"{stats['entry_count']} wikipedia index entries",
                    "bucket": "wikipedia/index",
                    "gradient": f"wikipedia/index/{shard_key}",
                    "tags": routing_tokens,
                    "checksum": _short_checksum(shard_index_blob),
                    "children": [shard_rel_pack],
                    "snapshot": resolved_snapshot,
                    "time_span": resolved_snapshot,
                    "bank_paths": [shard_rel_pack],
                    "dict_paths": [shard_rel_dict],
                    "offset_paths": [shard_rel_idx],
                    "entry_count": stats["entry_count"],
                    "title_range": {"min": stats["title_min"], "max": stats["title_max"]},
                    "routing_tokens": routing_tokens,
                }
            )
            totals["artifact_bytes"]["lbpack"] += Path(pack_path).stat().st_size
            totals["artifact_bytes"]["lbidx"] += Path(idx_path).stat().st_size
            totals["artifact_bytes"]["ldict"] += Path(dict_path).stat().st_size
            totals["artifact_bytes"]["shard_index"] += len(shard_index_blob)

        root_payload = _build_root_index_payload(resolved_snapshot, shard_entries, totals["entries"])
        root_index_path = snapshot_root / "root.index.loombit"
        root_blob = compile_object(root_payload)
        root_index_path.write_bytes(root_blob)
        totals["artifact_bytes"]["root_index"] += len(root_blob)

        manifest = {
            "snapshot": resolved_snapshot,
            "source": {
                "filename": source.name,
                "path": str(source),
                "checksum": _file_checksum(source) if source.exists() else "",
                "max_entries": max_entries if max_entries is not None else None,
            },
            "total_entries": totals["entries"],
            "shard_count": len(shard_entries),
            "bank_count": len(shard_entries),
            "artifact_bytes": dict(sorted(totals["artifact_bytes"].items())),
            "root_index": {
                "path": str(root_index_path.relative_to(snapshot_root)).replace("\\", "/"),
                "checksum": _short_checksum(root_blob),
                "surface": _surface_preview(root_blob),
            },
            "shards": shard_entries,
        }
        manifest_path = manifests_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        return {
            "snapshot_root": str(snapshot_root),
            "snapshot": resolved_snapshot,
            "manifest_path": str(manifest_path),
            "root_index_path": str(root_index_path),
            "total_entries": totals["entries"],
            "shard_count": len(shard_entries),
            "bank_count": len(shard_entries),
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
