from __future__ import annotations

from pathlib import Path

from loom.loombit import build_dictionary_from_files, build_index_payload, compile_file, compile_object, write_external_dictionary
from loom.loombit_route import RouteHint, route_index


ROOT = Path(__file__).resolve().parents[1]


def test_route_index_uses_bucket_and_query_tokens(tmp_path: Path):
    loom_path = ROOT / "spec" / "examples" / "basketball.loom"
    oradio_path = ROOT / "spec" / "examples" / "basketball.oradio"
    built = build_dictionary_from_files([loom_path, oradio_path])
    dict_path = tmp_path / "shared.ldict"
    write_external_dictionary(built.entries, dict_path)

    sports_blob = compile_file(loom_path, external_dictionary=built, strict_external=True)
    sports_path = tmp_path / "sports.basketball.loombit"
    sports_path.write_bytes(sports_blob)

    news_blob = compile_file(oradio_path, external_dictionary=built, strict_external=True)
    news_path = tmp_path / "news.generic.loombit"
    news_path.write_bytes(news_blob)

    payload = build_index_payload(
        [
            {
                "id": "sports-basketball",
                "path": sports_path.name,
                "class": "loombit_text_index",
                "topic": "basketball finals winner",
                "summary": "basketball result and winner trail",
                "bucket": "sports",
                "gradient": "sports/basketball/finals",
                "tags": ["basketball", "winner", "finals"],
            },
            {
                "id": "weather",
                "path": news_path.name,
                "class": "loombit_text_index",
                "topic": "weather report",
                "summary": "wind and rain",
                "bucket": "weather",
                "gradient": "weather/regional",
                "tags": ["forecast"],
            },
        ],
        title="root",
        branching=2,
        level=0,
    )
    root_blob = compile_object(payload, external_dictionary=built, strict_external=False)
    root_path = tmp_path / "root.index.loombit"
    root_path.write_bytes(root_blob)

    routed = route_index(
        root_path,
        query="who won the basketball finals?",
        dict_path=dict_path,
        hint=RouteHint(gradient_bucket="sports", aim_tokens=["basketball", "winner"]),
        top_k=2,
    )
    assert routed["ranked"][0]["id"] == "sports-basketball"
    assert routed["ranked"][0]["score"] >= routed["ranked"][1]["score"]
