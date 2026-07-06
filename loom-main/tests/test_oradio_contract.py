import json
import zipfile
from pathlib import Path

import pytest

import oradio_resolver
import oradio_player
import oradio_player_ui
from loom import radio_os_studio as studio
import yaml

# Some contract tests exercise the export-to-Radio-OS seam (feed-plugin discovery,
# the RADIO_OS_PLUGINS kernel env, the station plugin roster). They need the Radio OS
# *station* plugin tree — keyed on a feed plugin (antenna_http.py) that only exists in
# a Radio OS checkout, NOT our plugins/organs/ reference organs. Skip when absent.
_needs_station = pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "plugins" / "antenna_http.py").exists(),
    reason="needs Radio OS station plugin tree (export-to-station seam)",
)


def _contract_manifest():
    manifest = studio.default_manifest("ContractStation", "Contract Station", "Host")
    manifest["voices"] = {}
    manifest["audio"]["voices_provider"] = "kokoro"
    manifest["audio"]["piper_bin"] = ""
    return manifest


@_needs_station
def test_discover_feed_plugins_finds_roster_and_excludes_non_feeds():
    feeds = studio.discover_feed_plugins()
    for name in ("antenna_http", "rss", "reddit", "document", "markets", "bluesky"):
        assert name in feeds, name
    # IS_FEED = False plugins are not antennas and must be excluded
    for name in ("notes", "radio_dial", "transcript"):
        assert name not in feeds
    assert feeds["rss"]["desc"]  # PLUGIN_DESC read statically (no execution)


def test_feed_default_config_seeds_from_plugin_or_template():
    cfg = studio.feed_default_config("rss")
    assert cfg["plugin"] == "rss"
    assert "enabled" in cfg
    minimal = studio.feed_default_config("totally_made_up_xyz", {})
    assert minimal == {"enabled": True, "plugin": "totally_made_up_xyz"}


@_needs_station
def test_oradio_export_contains_requirements_and_lock(tmp_path):
    manifest = _contract_manifest()
    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"

    studio.write_oradio_package(
        out,
        manifest,
        signature={"fields": []},
        spec={"station": "contract"},
        assets=assets,
        lock=lock,
        station_dir=tmp_path,
    )

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert {"oradio.json", "manifest.yaml", "requirements.json", "requirements.lock.json"} <= names
        assert "plugins/meta/generated.py" in names
        assert "broadcast_grammar.py" in names
        descriptor = json.loads(zf.read("oradio.json"))
        requirements = json.loads(zf.read("requirements.json"))
        locked = json.loads(zf.read("requirements.lock.json"))

    assert descriptor["format"] == "oradio"
    assert descriptor["entry"]["requirements"] == "requirements.json"
    assert descriptor["entry"]["requirements_lock"] == "requirements.lock.json"
    assert requirements["narration"] == "live_llm"
    assert requirements["llm"]["required"] is True
    assert locked["narration"] == "live_llm"
    assert locked["llm"]["required"] is True


def test_oradio_preview_data_matches_export_contract(tmp_path):
    manifest = _contract_manifest()
    preview = studio.build_oradio_preview_data(manifest, tmp_path)

    assert preview["oradio"]["format"] == "oradio"
    assert preview["oradio"]["entry"]["requirements"] == "requirements.json"
    assert preview["requirements"]["narration"] == "live_llm"
    assert preview["requirements"]["llm"]["required"] is True
    assert preview["resolution_preview"]["lock"]["narration"] == "live_llm"
    assert preview["resolution_preview"]["lock"]["llm"]["required"] is True


def test_oradio_export_rewrites_bundled_voice_refs_to_package_paths(tmp_path):
    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"fake voice")
    manifest = _contract_manifest()
    manifest["voices"]["host"] = str(voice)

    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        packaged_manifest = yaml.safe_load(zf.read("manifest.yaml")) or {}

    assert "assets/voices/voice.onnx" in names
    assert packaged_manifest["voices"]["host"] == "assets/voices/voice.onnx"
    assert packaged_manifest["voices"]["host"] != str(voice)


def test_oradio_export_bundles_local_sfx_into_package(tmp_path):
    stinger = tmp_path / "league_stinger.wav"
    stinger.write_bytes(b"RIFF....fake stinger")
    manifest = _contract_manifest()
    manifest["production"] = {"sfx": [{"tag": "league", "ref": str(stinger), "source": "local"}]}

    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        requirements = json.loads(zf.read("requirements.json"))
        locked = json.loads(zf.read("requirements.lock.json"))

    assert "assets/sfx/league_stinger.wav" in names
    sfx_lock = locked["sfx"]
    assert len(sfx_lock) == 1
    assert sfx_lock[0]["tag"] == "league"
    assert sfx_lock[0]["bundled"] is True
    assert sfx_lock[0]["arcname"] == "assets/sfx/league_stinger.wav"
    assert sfx_lock[0]["sha256"]
    # declared in the contract; sfx is seasoning, never the medium
    assert requirements["sfx"]["declared"][0]["tag"] == "league"


def test_oradio_export_bundles_global_background_art_and_rewrites_manifest(tmp_path):
    wallpaper = tmp_path / "station_wallpaper.gif"
    wallpaper.write_bytes(b"GIF89a fake wallpaper bytes")
    manifest = _contract_manifest()
    manifest["art"] = {
        "global_bg": {"type": "image", "path": str(wallpaper), "value": "#112233"},
        "accent": "#33ccff",
    }

    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        packaged_manifest = yaml.safe_load(zf.read("manifest.yaml")) or {}
        locked = json.loads(zf.read("requirements.lock.json"))

    assert "assets/art/station_wallpaper.gif" in names
    assert packaged_manifest["art"]["global_bg"]["path"] == "assets/art/station_wallpaper.gif"
    assert packaged_manifest["art"]["global_bg"]["path"] != str(wallpaper)
    assert locked["art"][0]["role"] == "global_bg"
    assert locked["art"][0]["bundled"] is True
    assert locked["art"][0]["arcname"] == "assets/art/station_wallpaper.gif"


def test_oradio_remote_sourced_sfx_is_declared_for_fetch(tmp_path):
    manifest = _contract_manifest()
    manifest["production"] = {"sfx": [{"tag": "rain", "ref": "rain ambience loop", "source": "freesound"}]}

    _assets, lock = studio.collect_station_assets(manifest, tmp_path)

    assert lock["sfx"][0] == {
        "tag": "rain", "ref": "rain ambience loop", "bundled": False, "resolve": "fetch", "provider": "freesound",
    }
    # remote sfx that isn't bundled must not be flagged as a missing-file unresolved error
    assert not any("rain" in u for u in lock["unresolved"])


def test_oradio_resolver_treats_corrupt_sfx_as_warning_not_block(tmp_path):
    stinger = tmp_path / "league_stinger.wav"
    stinger.write_bytes(b"original stinger")
    manifest = _contract_manifest()
    manifest["production"] = {"sfx": [{"tag": "league", "ref": str(stinger), "source": "local"}]}
    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    extract_dir = tmp_path / "extracted"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    oradio_resolver.extract_oradio(out, extract_dir)
    (extract_dir / "assets" / "sfx" / "league_stinger.wav").write_bytes(b"corrupt stinger payload")
    resolved = oradio_resolver.resolve_station(out, extract_dir=extract_dir, check_llm=False)

    # SFX is seasoning: a corrupt stinger is a warning, the station still broadcasts.
    assert resolved["ready"] is True
    assert any("league" in w and ("sha256" in w or "size" in w) for w in resolved["warnings"])


def test_oradio_production_rules_declared_in_contract_and_lock(tmp_path):
    stinger = tmp_path / "league_stinger.wav"
    stinger.write_bytes(b"RIFF....fake stinger")
    manifest = _contract_manifest()
    manifest["production"] = {
        "sfx": [{"tag": "league", "ref": str(stinger), "source": "local"}],
        "rules": [{
            "event": "breaking_league_update", "stinger": "league", "voice": "Croft",
            "priority": "high", "interrupt": True, "cooldown_sec": 30,
        }],
        "interstitials": [{"kind": "station_id", "text": "You're on ATLFM.", "every_sec": 600}],
    }

    requirements = studio.station_requirements(manifest)
    _assets, lock = studio.collect_station_assets(manifest, tmp_path)

    rule = requirements["production"]["rules"][0]
    assert rule["event"] == "breaking_league_update"
    assert rule["stinger"] == "league"
    assert rule["interrupt"] is True
    assert rule["cooldown_sec"] == 30
    assert requirements["production"]["interstitials"][0]["kind"] == "station_id"
    assert lock["production"]["rules"][0]["event"] == "breaking_league_update"
    # valid stinger reference (tag is declared) → no dangling-reference warning
    assert not any("undeclared sfx tag" in u for u in lock["unresolved"])


def test_oradio_production_rule_with_dangling_sfx_tag_warns_but_does_not_block(tmp_path):
    manifest = _contract_manifest()
    manifest["production"] = {
        "sfx": [],
        "rules": [{"event": "breaking", "stinger": "missing_tag"}],
    }

    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)
    resolved = oradio_resolver.resolve_station(out, check_llm=False)

    assert any("undeclared sfx tag 'missing_tag'" in u for u in lock["unresolved"])
    # dangling production reference is seasoning — broadcast still ready
    assert resolved["ready"] is True


def test_sfx_sourcing_reads_key_and_searches(monkeypatch, tmp_path):
    import sfx_sourcing

    monkeypatch.setattr(sfx_sourcing.provisioning, "read_global_config",
                        lambda: {"asset_sources": {"freesound": {"api_key": "fs-test"}}})
    assert sfx_sourcing.freesound_api_key() == "fs-test"

    monkeypatch.setattr(
        sfx_sourcing, "freesound_search",
        lambda query, key, **kw: {"ok": True, "error": None,
                                  "results": [{"id": 42, "name": "Rain", "preview_url": "https://x/preview.mp3"}]},
    )

    def fake_download(url, dest, *, key="", timeout=30.0):
        from pathlib import Path as _P
        _P(dest).parent.mkdir(parents=True, exist_ok=True)
        _P(dest).write_bytes(b"fake mp3 bytes")
        return _P(dest)

    monkeypatch.setattr(sfx_sourcing, "download_to", fake_download)

    result = sfx_sourcing.source_sfx_entry("rain ambience", "fs-test", tmp_path, slug="rain")
    assert result["ok"] is True
    assert result["source_id"] == 42
    assert result["path"].is_file()


def test_sourced_remote_sfx_gets_bundled(monkeypatch, tmp_path):
    # A pre-fetched remote sfx file is bundled into the package like a local one.
    fetched = tmp_path / "rain_42.mp3"
    fetched.write_bytes(b"fake mp3 bytes")
    manifest = _contract_manifest()
    manifest["production"] = {"sfx": [{"tag": "rain", "ref": "rain ambience", "source": "freesound"}]}

    assets, lock = studio.collect_station_assets(manifest, tmp_path, sourced={"rain": fetched})
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())

    assert "assets/sfx/rain_42.mp3" in names
    rain = lock["sfx"][0]
    assert rain["tag"] == "rain"
    assert rain["bundled"] is True
    assert rain["sourced"] == "freesound"
    assert rain["sha256"]


def test_simulator_readiness_reports_live_vs_scaffold(monkeypatch):
    import provisioning

    monkeypatch.setattr(provisioning, "provisioning_status", lambda: {"ready": True, "provider": "ollama"})
    live = studio.simulator_readiness()
    assert live["live_llm"] is True
    assert live["provider"] == "ollama"

    monkeypatch.setattr(
        provisioning, "provisioning_status",
        lambda: {"ready": False, "provider": "ollama", "error": "Ollama not running"},
    )
    scaffold = studio.simulator_readiness()
    # creator-side simulator never blocks — it just reports it is in scaffold mode
    assert scaffold["live_llm"] is False
    assert scaffold["error"] == "Ollama not running"


def test_oradio_resolver_reads_export_contract(tmp_path):
    manifest = _contract_manifest()
    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    loaded = oradio_resolver.load_oradio(out)
    resolved = oradio_resolver.resolve_station(out, check_llm=False)

    assert loaded["oradio"]["station_id"] == "ContractStation"
    assert loaded["requirements"]["narration"] == "live_llm"
    assert loaded["lock"]["narration"] == "live_llm"
    assert resolved["station"] == "Contract Station"
    assert resolved["narration"] == "live_llm"
    assert resolved["ready"] is True


def test_oradio_resolver_rejects_corrupt_bundled_voice(tmp_path):
    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"original voice")
    manifest = _contract_manifest()
    manifest["voices"]["host"] = str(voice)
    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    extract_dir = tmp_path / "extracted"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    oradio_resolver.extract_oradio(out, extract_dir)
    (extract_dir / "assets" / "voices" / "voice.onnx").write_bytes(b"corrupt voice")
    resolved = oradio_resolver.resolve_station(out, extract_dir=extract_dir, check_llm=False)

    assert resolved["ready"] is False
    assert any("sha256" in item or "size" in item for item in resolved["blocking"])


@_needs_station
def test_oradio_player_builds_kernel_launch_env(tmp_path):
    manifest = _contract_manifest()
    assets, lock = studio.collect_station_assets(manifest, tmp_path)
    out = tmp_path / "ContractStation.oradio"
    extract_dir = tmp_path / "extracted"
    studio.write_oradio_package(out, manifest, assets=assets, lock=lock, station_dir=tmp_path)

    oradio_resolver.extract_oradio(out, extract_dir)
    readiness = oradio_resolver.resolve_station(out, extract_dir=extract_dir, check_llm=False)
    env = oradio_player.build_launch_env(extract_dir, readiness, headless=True)

    assert env["STATION_DIR"] == str(extract_dir)
    assert env["STATION_DB_PATH"] == str(extract_dir / "station.sqlite")
    assert env["STATION_MEMORY_PATH"] == str(extract_dir / "station_memory.json")
    assert env["RADIO_OS_PLUGINS"] == str(extract_dir / "plugins")
    assert env["RADIO_OS_HEADLESS"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_oradio_player_windows_association_plan_quotes_paths():
    python_exe = r"C:\Program Files\Python313\python.exe"
    player = r"C:\Radio OS\oradio_player.py"

    plan = oradio_player.windows_association_plan(python_exe=python_exe, player_path=player)

    assert plan["extension"] == ".oradio"
    assert plan["prog_id"] == "RadioOS.Station"
    # Double-click opens the themed GUI runtime directly (no --shell headless thin-chrome route).
    assert plan["open_command"] == '"C:\\Program Files\\Python313\\python.exe" "C:\\Radio OS\\oradio_player.py" "%1"'
    assert "--shell" not in plan["open_command"]
    assert plan["open_command_key"].endswith(r"RadioOS.Station\shell\open\command")


def test_oradio_player_windows_association_plan_uses_packaged_exe_when_frozen(monkeypatch):
    monkeypatch.setattr(oradio_player.app_paths, "is_frozen", lambda: True)

    plan = oradio_player.windows_association_plan(executable_path=r"C:\Radio OS\loom.exe")

    assert plan["open_command"] == '"C:\\Radio OS\\loom.exe" "%1"'
    assert plan["default_icon"] == '"C:\\Radio OS\\loom.exe",0'


def test_oradio_player_prints_association_plan_without_package(capsys):
    code = oradio_player.main(["--print-windows-association"])

    out = capsys.readouterr().out
    assert code == 0
    assert ".oradio Windows association plan" in out
    assert "open command:" in out


def test_oradio_player_without_package_opens_loom(monkeypatch):
    called = {}

    def fake_launch():
        called["opened"] = True
        return 0

    monkeypatch.setattr(oradio_player, "launch_loom_authoring", fake_launch)

    code = oradio_player.main([])

    assert code == 0
    assert called["opened"] is True


def test_oradio_player_shell_cli_routes_to_runtime_shell(monkeypatch, tmp_path):
    called = {}
    package = tmp_path / "ContractStation.oradio"
    package.write_bytes(b"not a real package for this routing test")

    def fake_main(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(oradio_player_ui, "main", fake_main)

    code = oradio_player.main(["--shell", str(package)])

    assert code == 0
    assert called["argv"] == [str(package)]


def test_oradio_player_tune_in_membership_validates_and_saves(monkeypatch, tmp_path):
    saved = {}

    monkeypatch.setattr(oradio_player.provisioning, "read_global_config", lambda: {"default_models": {}})
    monkeypatch.setattr(
        oradio_player.provisioning,
        "validate_provider",
        lambda provider, endpoint="", key="", model="": {"ok": True, "provider": provider},
    )
    monkeypatch.setattr(oradio_player.provisioning, "global_config_path", lambda: tmp_path / "config.json")

    def fake_save(provider, *, endpoint=None, key=None, host_model=None, producer_model=None):
        saved.update({
            "provider": provider,
            "endpoint": endpoint,
            "key": key,
            "host_model": host_model,
            "producer_model": producer_model,
        })
        return {"default_models": saved}

    monkeypatch.setattr(oradio_player.provisioning, "save_llm_membership", fake_save)

    result = oradio_player.tune_in_membership(
        provider="openai",
        endpoint="https://api.openai.com/v1",
        api_key="sk-test",
        model="gpt-test",
    )

    assert result["ok"] is True
    assert result["provider"] == "openai"
    assert saved["provider"] == "openai"
    assert saved["key"] == "sk-test"
    assert saved["host_model"] == "gpt-test"
    assert saved["producer_model"] == "gpt-test"


def test_oradio_player_tune_in_cli_without_package(monkeypatch, capsys):
    monkeypatch.setattr(
        oradio_player,
        "tune_in_membership",
        lambda **kwargs: {"ok": True, "provider": "ollama", "model": "rnj-1:8b", "config": "config.json"},
    )

    code = oradio_player.main(["--tune-in", "--provider", "ollama", "--model", "rnj-1:8b"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Tune-In saved" in out
    assert "rnj-1:8b" in out


def test_oradio_player_tune_in_hint_mentions_pull_for_ollama():
    hint = oradio_player.tune_in_hint({"llm": {"provider": "ollama", "model": "rnj-1:8b"}})

    assert "--tune-in" in hint
    assert "--provider ollama" in hint
    assert "--model rnj-1:8b" in hint
    assert "--pull-model" in hint


def test_oradio_runtime_shell_surfaces_from_readiness():
    readiness = {
        "station": "Contract Station",
        "ready": True,
        "voices": [{"role": "host", "source": "bundled"}],
        "piper": {"needed": False},
    }

    specs = oradio_player_ui.surface_specs_from_readiness(readiness)

    assert specs[0].title == "Contract Station"
    assert any(s.kind == "readiness" and "READY" in s.body for s in specs)
    assert any(s.kind == "voices" and "host: bundled" in s.body for s in specs)


def test_oradio_runtime_shell_palette_inherits_manifest_art():
    manifest = {
        "art": {
            "global_bg": {"type": "color", "value": "#010203"},
            "panels": {
                "toolbar": {"type": "color", "value": "#111213"},
                "subtitle": {"type": "color", "value": "#212223"},
            },
            "accent": "#33ccff",
        }
    }

    palette = oradio_player_ui.palette_from_manifest(manifest)

    assert palette["bg"] == "#010203"
    assert palette["surface"] == "#010203"
    assert palette["panel"] == "#111213"
    assert palette["subtitle"] == "#212223"
    assert palette["accent"] == "#33ccff"


def test_oradio_runtime_shell_palette_uses_gradient_and_resolves_packaged_art(tmp_path):
    manifest = {
        "art": {
            "global_bg": {
                "type": "gradient",
                "value": "#010203",
                "gradient": {"type": "linear", "color1": "#123456", "color2": "#654321"},
            }
        }
    }

    palette = oradio_player_ui.palette_from_manifest(manifest)

    assert palette["bg"] == "#123456"
    assert palette["surface"] == "#654321"
    assert oradio_player_ui.background_status(manifest)["mode"] == "gradient"

    extracted = tmp_path / "extracted"
    art_dir = extracted / "assets" / "art"
    art_dir.mkdir(parents=True)
    video = art_dir / "wallpaper.mp4"
    video.write_bytes(b"fake mp4 bytes")
    packaged_manifest = {"art": {"global_bg": {"type": "video", "path": "assets/art/wallpaper.mp4", "value": "#010203"}}}

    resolved = oradio_player_ui.resolve_art_path_from_manifest(packaged_manifest, extracted)
    status = oradio_player_ui.background_status(packaged_manifest, extracted)

    assert resolved == video
    assert status["mode"] == "video"
    assert status["ok"] is True
    assert "Video wallpaper" in status["message"]


def test_oradio_runtime_shell_blocking_text_and_subtitle_helpers():
    readiness = {"ready": False, "blocking": ["LLM not provisioned", "voice missing"]}

    assert "- LLM not provisioned" in oradio_player_ui.blocking_text(readiness)
    assert oradio_player_ui.clean_runtime_line("[host] HOST: hello world") == "HOST: hello world"
    assert oradio_player_ui.is_subtitle_candidate("HOST: hello world") is True
    assert oradio_player_ui.is_subtitle_candidate("feed heartbeat") is False


def test_oradio_runtime_shell_event_history_and_live_surface_bodies(tmp_path):
    lines = []
    oradio_player_ui.append_recent(lines, "feed candidate: standings moved", limit=2)
    oradio_player_ui.append_recent(lines, "Audio segment: seg_1.wav", limit=2)
    oradio_player_ui.append_recent(lines, "HOST: live line", limit=2)

    assert lines == ["Audio segment: seg_1.wav", "HOST: live line"]
    assert oradio_player_ui.event_kind("Audio segment: seg_1.wav") == "audio"
    assert oradio_player_ui.event_kind("feed candidate: standings moved") == "signal"
    assert oradio_player_ui.event_kind("HOST: live line") == "subtitle"

    readiness = {"ready": True, "blocking": []}
    body = oradio_player_ui.surface_body_for_kind(
        "audio",
        readiness=readiness,
        extract_dir=tmp_path,
        log_lines=["Kernel started"],
        audio_lines=["Audio segment: seg_1.wav"],
        signal_lines=["feed candidate: standings moved"],
    )

    assert str(tmp_path / ".audio_pipe") in body
    assert "seg_1.wav" in body

    signal_body = oradio_player_ui.surface_body_for_kind(
        "signal",
        readiness=readiness,
        extract_dir=tmp_path,
        log_lines=[],
        audio_lines=[],
        signal_lines=["feed candidate: standings moved"],
    )
    assert "standings moved" in signal_body


def test_oradio_runtime_shell_transport_capabilities_are_honest():
    caps = oradio_player_ui.transport_capabilities()

    assert caps["play"] is True
    assert caps["stop"] is True
    assert caps["restart"] is True
    assert caps["open_audio_pipe"] is True
    assert caps["pause"] is False
    assert caps["rewind"] is False
    assert caps["forward"] is False
    assert "not exposed" in oradio_player_ui.unsupported_transport_message("pause")
