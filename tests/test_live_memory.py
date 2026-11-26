"""The load guard reads Live's own memory log (26.11.2026: a kept rack ran Live to ~167 GB)."""

from MCP_Server import live_memory


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
