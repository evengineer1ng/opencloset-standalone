from __future__ import annotations

from pathlib import Path

from loom.loombit import (
    INDEX_KIND,
    build_index_payload_from_files,
    build_dictionary_from_files,
    color_cells_from_bytes,
    compile_file,
    compile_object,
    decode_loombit,
    inspect_blob,
    lens_cells_from_color_cells,
    load_external_dictionary,
    read_source_file,
    write_external_dictionary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_loombit_roundtrip_basketball_loom():
    path = ROOT / "spec" / "examples" / "basketball.loom"
    source = read_source_file(path)
    blob = compile_file(path)
    decoded = decode_loombit(blob)

    assert decoded["mode_name"] == "loom"
    assert decoded["checksum_ok"] is True
    assert decoded["payload"] == source
    assert compile_object(decoded["payload"], mode=decoded["mode"]) == blob


def test_loombit_roundtrip_basketball_oradio():
    path = ROOT / "spec" / "examples" / "basketball.oradio"
    source = read_source_file(path)
    blob = compile_file(path)
    decoded = decode_loombit(blob)

    assert decoded["mode_name"] == "oradio"
    assert decoded["checksum_ok"] is True
    assert decoded["payload"] == source
    assert compile_object(decoded["payload"], mode=decoded["mode"]) == blob


def test_loombit_inspect_derives_quadrant_cells():
    path = ROOT / "spec" / "examples" / "basketball.loom"
    blob = compile_file(path)
    info = inspect_blob(blob)

    assert info["checksum_ok"] is True
    assert info["quadrant_cells"] > 0
    assert len(info["quadrant_preview"]) > 0
    assert set(info["quadrant_preview"][0].keys()) == {"nw", "ne", "sw", "se"}


def test_loombit_external_dictionary_roundtrip_and_hides_strings(tmp_path):
    loom_path = ROOT / "spec" / "examples" / "basketball.loom"
    oradio_path = ROOT / "spec" / "examples" / "basketball.oradio"
    dict_path = tmp_path / "shared.ldict"

    built = build_dictionary_from_files([loom_path, oradio_path])
    write_external_dictionary(built.entries, dict_path)
    ext = load_external_dictionary(dict_path)

    blob = compile_file(loom_path, external_dictionary=ext, strict_external=True)
    decoded = decode_loombit(blob, external_dictionary=ext)

    assert decoded["dictionary_mode"] == "external"
    assert decoded["payload"] == read_source_file(loom_path)
    assert b"The Finals" not in blob
    assert b"basketball_pbp" not in blob


def test_index_loombit_payload_and_color_lenses(tmp_path):
    loom_path = ROOT / "spec" / "examples" / "basketball.loom"
    oradio_path = ROOT / "spec" / "examples" / "basketball.oradio"
    dict_path = tmp_path / "shared.ldict"

    built = build_dictionary_from_files([loom_path, oradio_path])
    write_external_dictionary(built.entries, dict_path)
    ext = load_external_dictionary(dict_path)

    loom_blob = compile_file(loom_path, external_dictionary=ext, strict_external=True)
    loom_out = tmp_path / "basketball.loom.external.loombit"
    loom_out.write_bytes(loom_blob)

    oradio_blob = compile_file(oradio_path, external_dictionary=ext, strict_external=True)
    oradio_out = tmp_path / "basketball.oradio.external.loombit"
    oradio_out.write_bytes(oradio_blob)

    payload = build_index_payload_from_files([loom_out, oradio_out], title="root knowledge index", branching=2, level=0)
    index_blob = compile_object(payload, external_dictionary=ext, strict_external=False)
    decoded = decode_loombit(index_blob, external_dictionary=ext if b"The Finals" not in index_blob else None)

    assert decoded["payload"]["kind"] == INDEX_KIND
    assert decoded["payload"]["entry_count"] == 2
    assert decoded["payload"]["entries"][0]["path"].endswith(".loombit")

    cells = color_cells_from_bytes(index_blob)
    assert len(cells) > 0
    assert set(cells[0].keys()) == {"r", "g", "b", "k"}
    red = lens_cells_from_color_cells(cells[:12], "red")
    green = lens_cells_from_color_cells(cells[:12], "green")
    blue = lens_cells_from_color_cells(cells[:12], "blue")
    assert len(red) == len(green) == len(blue) == min(12, len(cells))
