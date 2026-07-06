from __future__ import annotations

import json
from pathlib import Path

from loom.loombit import decode_loombit
from loom.loombit_route import route_index
from loom.wikipedia_index_loombit import (
    WIKIPEDIA_INDEX_KIND,
    compile_wikipedia_index,
    infer_snapshot_from_path,
    parse_index_line,
    read_lbidx,
    recover_lbpack_record,
    shard_for_normalized_title,
)


def _sample_index(tmp_path: Path) -> Path:
    lines = [
        "566:10:AccessibleComputing",
        "566:12:Anarchism",
        "566:13:AfghanistanHistory",
        "566:25:Autism spectrum",
        "634:100:Category:Applied_sciences",
        "900:200:Portal:Art",
        "1111:300:Zebra",
        "1111:301:1984",
    ]
    path = tmp_path / "enwiki-20260601-pages-articles-multistream-index.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parse_index_line_and_namespace_detection():
    leaf = parse_index_line("634:100:Category:Applied_sciences", snapshot="2026-06-01", source_ref="sample.txt")
    assert leaf["kind"] == WIKIPEDIA_INDEX_KIND
    assert leaf["page_id"] == 100
    assert leaf["offset"] == 634
    assert leaf["namespace"] == "Category"
    assert leaf["shard_key"] == shard_for_normalized_title(leaf["normalized_title"])


def test_compile_wikipedia_index_is_reproducible(tmp_path: Path):
    source = _sample_index(tmp_path)
    out_a = tmp_path / "out_a"
    out_b = tmp_path / "out_b"
    first = compile_wikipedia_index(source, outdir=out_a)
    second = compile_wikipedia_index(source, outdir=out_b)

    manifest_a = Path(first["manifest_path"]).read_text(encoding="utf-8")
    manifest_b = Path(second["manifest_path"]).read_text(encoding="utf-8")
    assert manifest_a == manifest_b

    root_a = Path(first["root_index_path"]).read_bytes()
    root_b = Path(second["root_index_path"]).read_bytes()
    assert root_a == root_b


def test_root_and_shard_indexes_point_to_pack_and_recover_record(tmp_path: Path):
    source = _sample_index(tmp_path)
    result = compile_wikipedia_index(source, outdir=tmp_path / "out")
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    root_path = Path(result["root_index_path"])
    root = decode_loombit(root_path.read_bytes())

    assert root["payload"]["snapshot"] == "2026-06-01"
    assert root["payload"]["entry_count"] == manifest["shard_count"]

    first_shard = manifest["shards"][0]
    shard_path = Path(result["snapshot_root"]) / first_shard["path"]
    shard = decode_loombit(shard_path.read_bytes())
    payload = shard["payload"]
    bank_path = Path(result["snapshot_root"]) / payload["entries"][0]["bank_paths"][0]
    idx_path = Path(result["snapshot_root"]) / payload["entries"][0]["offset_paths"][0]
    dict_path = Path(result["snapshot_root"]) / payload["entries"][0]["dict_paths"][0]
    idx_entries = read_lbidx(idx_path)
    recovered = recover_lbpack_record(bank_path, idx_path, dict_path, idx_entries[0]["id"])

    assert recovered["id"] == idx_entries[0]["id"]
    assert recovered["kind"] == WIKIPEDIA_INDEX_KIND
    assert recovered["title"]


def test_pack_and_indexes_surface_preview_and_routing(tmp_path: Path):
    source = _sample_index(tmp_path)
    result = compile_wikipedia_index(source, outdir=tmp_path / "out")
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["root_index"]["surface"]["quadrant_cells"] > 0
    assert manifest["root_index"]["surface"]["red_lens"]

    routed = route_index(result["root_index_path"], query="autism science category", top_k=4)
    assert routed["ranked"]
    assert routed["visited_indexes"] >= 1


def test_infer_snapshot_from_path_uses_dump_date(tmp_path: Path):
    source = _sample_index(tmp_path)
    assert infer_snapshot_from_path(source) == "2026-06-01"
