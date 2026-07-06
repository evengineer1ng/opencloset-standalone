import importlib.util
from pathlib import Path

# Load the plugin in isolation (no sys.path mutation, so it can't shadow other test imports).
_spec = importlib.util.spec_from_file_location(
    "antenna_bridge", Path(__file__).resolve().parent.parent / "plugins" / "antenna_bridge.py"
)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def test_parse_observation_line_json_and_plaintext():
    cand = ab.parse_observation_line('{"type":"test_failed","title":"3 tests failed","priority":85}', "code", 60)
    assert cand["type"] == "test_failed"
    assert cand["title"] == "3 tests failed"
    assert cand["priority"] == 85  # heat hint passed through, not re-ranked
    assert cand["source"] == "code"
    assert cand["post_id"].startswith("code:")
    assert "test_failed" in cand["tags"]

    # plain text tolerated as a log_line
    plain = ab.parse_observation_line("build finished in 4.2s", "code", 60)
    assert plain["type"] == "log_line"
    assert plain["body"] == "build finished in 4.2s"
    assert plain["priority"] == 60  # default heat hint

    assert ab.parse_observation_line("   ", "code", 60) is None


def test_diff_state_reports_changes_not_judgments():
    prev = {"branch": "main", "failing_tests": 0}
    curr = {"branch": "main", "failing_tests": 2, "last_commit": "abc123"}
    obs = ab.diff_state(prev, curr, "code", 60)
    types = {(o["type"], o["title"]) for o in obs}
    assert ("field_changed", "failing_tests changed") in types
    assert ("field_added", "last_commit appeared") in types
    # unchanged key produced nothing
    assert not any("branch" in o["title"] for o in obs)


def test_diff_state_respects_ignore_keys():
    obs = ab.diff_state({"heartbeat": 1}, {"heartbeat": 2}, "code", 60, ignore_keys=("heartbeat",))
    assert obs == []


def test_read_new_lines_tails_and_skips_partial(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text('{"type":"a"}\n{"type":"b"}\n', encoding="utf-8")
    lines, off = ab.read_new_lines(str(f), 0)
    assert lines == ['{"type":"a"}', '{"type":"b"}']
    assert off == f.stat().st_size

    # append a complete + a partial line; only the complete one is returned, offset holds before partial
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"c"}\n{"type":"par')
    lines2, off2 = ab.read_new_lines(str(f), off)
    assert lines2 == ['{"type":"c"}']
    # finish the partial line; next read returns it
    with f.open("a", encoding="utf-8") as fh:
        fh.write('tial"}\n')
    lines3, _ = ab.read_new_lines(str(f), off2)
    assert lines3 == ['{"type":"partial"}']


def test_read_new_lines_resets_on_truncation(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text('{"type":"a"}\n{"type":"b"}\n', encoding="utf-8")
    _, off = ab.read_new_lines(str(f), 0)
    f.write_text('{"type":"fresh"}\n', encoding="utf-8")  # smaller than off → rotation
    lines, _ = ab.read_new_lines(str(f), off)
    assert lines == ['{"type":"fresh"}']


def test_poll_bridge_stream_emits_only_new(tmp_path):
    cfg = {**ab.FEED_DEFAULTS, "source": "code", "mode": "stream", "bridge_dir": str(tmp_path)}
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"file_changed","title":"main.py"}\n', encoding="utf-8")
    out = []
    ctx = {"offset": 0, "prev_state": None}

    ctx = ab.poll_bridge(ctx, cfg, str(tmp_path), out.append)
    assert [c["type"] for c in out] == ["file_changed"]

    # no new lines → silence
    ctx = ab.poll_bridge(ctx, cfg, str(tmp_path), out.append)
    assert len(out) == 1

    # append one → only the new one emits
    with events.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"test_failed","title":"2 failed","priority":90}\n')
    ctx = ab.poll_bridge(ctx, cfg, str(tmp_path), out.append)
    assert [c["type"] for c in out] == ["file_changed", "test_failed"]
    assert out[-1]["priority"] == 90


def test_poll_bridge_snapshot_baseline_is_silent_then_diffs(tmp_path):
    cfg = {**ab.FEED_DEFAULTS, "source": "code", "mode": "snapshot", "bridge_dir": str(tmp_path)}
    state = tmp_path / "state.json"
    state.write_text('{"failing_tests": 0, "branch": "main"}', encoding="utf-8")
    out = []
    ctx = {"offset": 0, "prev_state": None}

    # first poll establishes baseline silently — silence is the default valid state
    ctx = ab.poll_bridge(ctx, cfg, str(tmp_path), out.append)
    assert out == []

    # change the snapshot → exactly the changed field is reported
    state.write_text('{"failing_tests": 3, "branch": "main"}', encoding="utf-8")
    ctx = ab.poll_bridge(ctx, cfg, str(tmp_path), out.append)
    assert [c["type"] for c in out] == ["field_changed"]
    assert "failing_tests" in out[0]["title"]
