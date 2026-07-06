from plugins.elite_trade_crew import choose_preferred_ollama_gpu, format_commodity_map, parse_commodity_map


def test_parse_commodity_map_accepts_commas_semicolons_and_equals():
    parsed = parse_commodity_map("Gold:64, Silver=12; Palladium:4")
    assert parsed == {"Gold": 64, "Silver": 12, "Palladium": 4}


def test_format_commodity_map_returns_operator_friendly_string():
    formatted = format_commodity_map({"Gold": 64, "Silver": 12})
    assert formatted == "Gold:64, Silver:12"


def test_choose_preferred_ollama_gpu_prefers_1080_ti():
    chosen = choose_preferred_ollama_gpu(
        [
            {"index": 0, "name": "NVIDIA GeForce RTX 5060 Ti", "uuid": "gpu0", "memory_total": "16311 MiB"},
            {"index": 1, "name": "NVIDIA GeForce GTX 1080 Ti", "uuid": "gpu1", "memory_total": "11264 MiB"},
        ]
    )
    assert chosen["index"] == 1
    assert chosen["uuid"] == "gpu1"