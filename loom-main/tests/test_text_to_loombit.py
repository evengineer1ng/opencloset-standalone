from __future__ import annotations

from pathlib import Path

from loom.loombit import decode_loombit, load_external_dictionary
from loom.text_loombit import TEXT_CHUNK_KIND, TEXT_INDEX_KIND, convert_text_file


def test_convert_large_text_file_to_loombits(tmp_path):
    source = tmp_path / "story.txt"
    text = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa\n\n"
        "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega\n\n"
    ) * 20
    source.write_text(text, encoding="utf-8")

    result = convert_text_file(source, outdir=tmp_path / "out", chunk_chars=140, title="Greek story", prefix="greek")

    dict_path = Path(result["dict_path"])
    index_path = Path(result["index_path"])
    chunk_paths = [Path(p) for p in result["chunk_paths"]]

    assert dict_path.is_file()
    assert index_path.is_file()
    assert len(chunk_paths) > 1

    external = load_external_dictionary(dict_path)
    index_decoded = decode_loombit(index_path.read_bytes(), external_dictionary=external)
    assert index_decoded["payload"]["kind"] == TEXT_INDEX_KIND
    assert index_decoded["payload"]["chunk_count"] == len(chunk_paths)

    first_chunk = decode_loombit(chunk_paths[0].read_bytes(), external_dictionary=external)
    assert first_chunk["payload"]["kind"] == TEXT_CHUNK_KIND
    assert first_chunk["payload"]["title"] == "Greek story"
    assert first_chunk["payload"]["text"]
