"""Convert large text files into loombit artifact families."""

from __future__ import annotations

import re
import zlib
from pathlib import Path
from typing import Any, Dict, List

from .loombit import (
    ExternalDictionary,
    build_external_dictionary_from_objects,
    compile_object,
    decode_loombit,
    load_external_dictionary,
    write_external_dictionary,
)


TEXT_CHUNK_KIND = "loombit_text_chunk"
TEXT_INDEX_KIND = "loombit_text_index"


def slugify(text: str) -> str:
    parts = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "").strip()).strip("-").lower()
    return parts or "text"


def chunk_text(text: str, max_chars: int) -> List[Dict[str, Any]]:
    text = str(text or "")
    limit = max(1, int(max_chars or 1))
    chunks: List[Dict[str, Any]] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + limit)
        if end < length:
            # Prefer paragraph, then line, then whitespace boundaries.
            window = text[start:end]
            candidates = [
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind(" "),
            ]
            cut = max(candidates)
            if cut > limit // 3:
                end = start + cut + (2 if window[cut:cut + 2] == "\n\n" else 1)
        body = text[start:end]
        chunks.append({"index": len(chunks), "char_start": start, "char_end": end, "text": body})
        start = end
    if not chunks:
        chunks.append({"index": 0, "char_start": 0, "char_end": 0, "text": ""})
    return chunks


def build_text_chunk_payload(
    *,
    source_name: str,
    title: str,
    chunk_index: int,
    chunk_count: int,
    char_start: int,
    char_end: int,
    text: str,
) -> Dict[str, Any]:
    return {
        "kind": TEXT_CHUNK_KIND,
        "source_name": source_name,
        "title": title,
        "chunk_index": int(chunk_index),
        "chunk_count": int(chunk_count),
        "char_start": int(char_start),
        "char_end": int(char_end),
        "text": str(text),
    }


def build_text_index_payload(
    *,
    source_name: str,
    title: str,
    total_chars: int,
    chunk_chars: int,
    entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "kind": TEXT_INDEX_KIND,
        "source_name": source_name,
        "title": title,
        "total_chars": int(total_chars),
        "chunk_chars": int(chunk_chars),
        "chunk_count": len(entries),
        "entries": entries,
    }


def _checksum(blob: bytes) -> str:
    return f"{zlib.crc32(blob) & 0xFFFFFFFF:08x}"


def convert_text_file(
    source_path: str | Path,
    *,
    outdir: str | Path | None = None,
    chunk_chars: int = 12000,
    title: str = "",
    prefix: str = "",
) -> Dict[str, Any]:
    path = Path(source_path)
    text = path.read_text(encoding="utf-8")
    source_name = path.name
    resolved_title = str(title or path.stem)
    resolved_prefix = slugify(prefix or path.stem)
    target_dir = Path(outdir) if outdir else path.parent / f"{resolved_prefix}_loombits"
    target_dir.mkdir(parents=True, exist_ok=True)

    raw_chunks = chunk_text(text, chunk_chars)
    chunk_payloads = [
        build_text_chunk_payload(
            source_name=source_name,
            title=resolved_title,
            chunk_index=chunk["index"],
            chunk_count=len(raw_chunks),
            char_start=chunk["char_start"],
            char_end=chunk["char_end"],
            text=chunk["text"],
        )
        for chunk in raw_chunks
    ]

    provisional_entries: List[Dict[str, Any]] = []
    for payload in chunk_payloads:
        artifact_name = f"{resolved_prefix}.chunk-{payload['chunk_index']:04d}.loombit"
        provisional_entries.append(
            {
                "id": f"{resolved_prefix}-chunk-{payload['chunk_index']:04d}",
                "path": artifact_name,
                "class": "text_chunk",
                "chunk_index": payload["chunk_index"],
                "char_start": payload["char_start"],
                "char_end": payload["char_end"],
                "summary": f"chars {payload['char_start']}-{payload['char_end']}",
                "checksum": "",
            }
        )

    index_payload = build_text_index_payload(
        source_name=source_name,
        title=resolved_title,
        total_chars=len(text),
        chunk_chars=chunk_chars,
        entries=provisional_entries,
    )

    external = build_external_dictionary_from_objects(chunk_payloads + [index_payload])
    dict_path = target_dir / f"{resolved_prefix}.shared.ldict"
    write_external_dictionary(external.entries, dict_path)
    loaded_external = load_external_dictionary(dict_path)

    chunk_paths: List[Path] = []
    final_entries: List[Dict[str, Any]] = []
    for payload, entry in zip(chunk_payloads, provisional_entries):
        blob = compile_object(payload, external_dictionary=loaded_external, strict_external=True)
        artifact_path = target_dir / entry["path"]
        artifact_path.write_bytes(blob)
        chunk_paths.append(artifact_path)
        updated = dict(entry)
        updated["checksum"] = _checksum(blob)
        final_entries.append(updated)

    final_index_payload = build_text_index_payload(
        source_name=source_name,
        title=resolved_title,
        total_chars=len(text),
        chunk_chars=chunk_chars,
        entries=final_entries,
    )
    # Rebuild the dictionary including final checksum-bearing index metadata.
    external = build_external_dictionary_from_objects(chunk_payloads + [final_index_payload])
    write_external_dictionary(external.entries, dict_path)
    loaded_external = load_external_dictionary(dict_path)

    # Re-emit chunks against the final shared dictionary so everything is strict and consistent.
    final_entries = []
    for payload, entry, artifact_path in zip(chunk_payloads, provisional_entries, chunk_paths):
        blob = compile_object(payload, external_dictionary=loaded_external, strict_external=True)
        artifact_path.write_bytes(blob)
        updated = dict(entry)
        updated["checksum"] = _checksum(blob)
        final_entries.append(updated)

    final_index_payload = build_text_index_payload(
        source_name=source_name,
        title=resolved_title,
        total_chars=len(text),
        chunk_chars=chunk_chars,
        entries=final_entries,
    )
    index_blob = compile_object(final_index_payload, external_dictionary=loaded_external, strict_external=True)
    index_path = target_dir / f"{resolved_prefix}.root.text-index.loombit"
    index_path.write_bytes(index_blob)

    return {
        "source": str(path),
        "outdir": str(target_dir),
        "dict_path": str(dict_path),
        "index_path": str(index_path),
        "chunk_paths": [str(p) for p in chunk_paths],
        "chunk_count": len(chunk_paths),
        "source_chars": len(text),
        "index_bytes": len(index_blob),
        "dict_bytes": dict_path.stat().st_size,
    }


def decode_text_chunk(path: str | Path, dict_path: str | Path) -> Dict[str, Any]:
    external = load_external_dictionary(dict_path)
    return decode_loombit(Path(path).read_bytes(), external_dictionary=external)
