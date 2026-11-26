"""Live's own memory, read from its Log.txt, and the check every load runs.

26.11.2026: loading a malformed rack ran Live to ~167 GB and crashed it (Log.txt MemoryUsage V 456 -> 617 GB in
2.5 min) while the next commands kept going. Live logs its memory every few seconds; a load now reads it before
and for a few seconds after, and a jump stops the work.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

LIVE_LOGS = Path.home() / "Library" / "Preferences" / "Ableton"
_MEM = re.compile(r"MemoryUsage: V: ([\d.]+) (GB|MB), R: ([\d.]+) (GB|MB)")


def _gb(x: str, unit: str) -> float:
    return float(x) / (1024 if unit == "MB" else 1)


def live_memory(logs: Path = LIVE_LOGS) -> tuple[float, float] | None:
    """Live's (virtual, resident) memory in GB: from macOS (ps) while Live runs, else the newest Log.txt's last line.

    The log alone is not enough: Live writes MemoryUsage only when it changes, and after a restart the last line
    can sit far back in the file (26.11.2026 the guard read None for that reason)."""
    now = _from_ps()
    if now is not None:
        return now
    return _from_log(logs)


def _from_ps() -> tuple[float, float] | None:
    try:
        out = subprocess.run(["ps", "-Ao", "vsz=,rss=,comm="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].endswith("/MacOS/Live"):
            return int(parts[0]) / 1048576, int(parts[1]) / 1048576
    return None


def _from_log(logs: Path) -> tuple[float, float] | None:
    try:
        log = max(logs.glob("Live*/Log.txt"), key=lambda f: f.stat().st_mtime)
        with log.open("rb") as fh:
            fh.seek(max(0, log.stat().st_size - 20_000_000))   # Live logs memory only now and then; Remote Script lines fill the tail
            tail = fh.read().decode("utf-8", "replace")
    except (ValueError, OSError):
        return None
    hits = _MEM.findall(tail)
    if not hits:
        return None
    v, vu, r, ru = hits[-1]
    return _gb(v, vu), _gb(r, ru)


def runaway(before: tuple[float, float] | None, wait_s: float = 6.0, logs: Path = LIVE_LOGS,
            max_v_jump: float = 40.0, max_r: float = 24.0) -> str | None:
    """A STOP message if Live's memory ran away since `before` (watched for wait_s seconds), else None."""
    if before is None:
        return None
    end = time.time() + wait_s
    while True:
        now = live_memory(logs)
        if now and (now[0] - before[0] > max_v_jump or now[1] > max_r):
            return (f"STOP: Live's memory is running away after this load (virtual {before[0]:.0f} -> {now[0]:.0f} GB, "
                    f"resident {now[1]:.1f} GB). Undo it in Live (Cmd+Z) at once, send Live nothing else, tell Marc.")
        if time.time() >= end:
            return None
        time.sleep(1.0)
