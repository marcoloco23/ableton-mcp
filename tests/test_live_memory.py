"""The load guard reads Live's own memory log (26.11.2026: a kept rack ran Live to ~167 GB)."""

import pytest

from MCP_Server import live_memory


@pytest.fixture(autouse=True)
def _no_live_running(monkeypatch):
    monkeypatch.setattr(live_memory, "_from_ps", lambda: None)


def _log(tmp_path, lines):
    d = tmp_path / "Live 12.4.16"
    d.mkdir(exist_ok=True)
    (d / "Log.txt").write_text("".join(f"2026-11-26T17:1{i}:00: info: MemoryUsage: {x}\n" for i, x in enumerate(lines)))


def test_reads_the_last_memory_line_in_gb(tmp_path):
    _log(tmp_path, ["V: 450 GB, R: 3 GB, P: 0 Bytes", "V: 456.2 GB, R: 512 MB, P: 0 Bytes"])
    assert live_memory.live_memory(tmp_path) == (456.2, 0.5)


def test_a_jump_after_a_load_says_stop_and_a_quiet_load_says_nothing(tmp_path):
    _log(tmp_path, ["V: 456 GB, R: 3 GB, P: 0 Bytes"])
    assert live_memory.runaway((456.0, 3.0), wait_s=0, logs=tmp_path) is None
    _log(tmp_path, ["V: 456 GB, R: 3 GB, P: 0 Bytes", "V: 534 GB, R: 5.8 GB, P: 0 Bytes"])
    msg = live_memory.runaway((456.0, 3.0), wait_s=0, logs=tmp_path)
    assert msg and msg.startswith("STOP") and "456 -> 534 GB" in msg


def test_no_log_means_no_guess(tmp_path):
    assert live_memory.live_memory(tmp_path) is None and live_memory.runaway(None, wait_s=0) is None


def test_a_running_live_is_read_from_ps(monkeypatch):
    out = "  1234 5000 /usr/bin/other\n444596224 5662310 /Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live\n"
    monkeypatch.setattr(live_memory.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    monkeypatch.undo()   # the autouse stub: read ps for real through the patched run
    monkeypatch.setattr(live_memory.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    v, r = live_memory._from_ps()
    assert round(v) == 424 and round(r, 1) == 5.4
