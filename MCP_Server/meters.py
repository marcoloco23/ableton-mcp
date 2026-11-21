"""Levels of every track over a span of bars, read from Live's meters while the set plays, without recording.

The Remote Script (handlers/live.py, `meters`) plays the bars once and samples every track's, group's, return's and
the master's output meter on Live's own clock, 20-30 times a second; a socket round trip per reading could not keep
up with 40 tracks. This module starts that job, waits for it (stopping it if it outlives its span), and turns the
samples into levels: `run`, then `analyse`, then `render`. live_meters and `dj produce meters` share it.

The scale. output_meter_left/right is the meter as Live draws it: a smoothed momentary peak, 0..1, placed on the
fader's scale (Live's docs: "corresponds to the meters shown in Live"). A reading becomes dB through the master
fader's own value -> text table, which the job sends with the samples, so the numbers are the ones printed beside
Live's fader. `level` is the mean of the meter on that scale, `peak` its highest reading, `envelope` the mean per
beat. None of them is LUFS or RMS: compare tracks with each other, and use `dj produce hear` for loudness.
UNCONFIRMED until checked in Live against a recorded take of the same bars.

Pump. A track carrying Kickstart or a compressor with its sidechain on is measured on the kicks of the kick
reference (the kick group, else the kick track): how far it dips below where it sat just before the kick, and how
long after the kick it is back within 1 dB. Only a kick the track sounds across counts: on a sparse part a dip
measures the gap between two notes, not the duck (21.11.2026, Serenity's 16th-note bass read "pump 10.1 dB
(-45.5-38.7)"). Depth and recovery are medians over the kicks that count, the spread their middle half; with fewer
than half the kicks counting the pump is not measurable. The meter's own release smooths a short, deep duck, so a
dip reads at most as deep as the meter can fall in that time.
"""

from __future__ import annotations

import statistics
import time
import unicodedata
from typing import Any, Callable

FLOOR_DB = -70.0      # what -inf reads as inside the pump arithmetic
KICK_RISE_DB = 4.0    # a kick: the kick meter jumps at least this far above its low of the last quarter beat
BACK_DB = 1.0         # "back": within this of the level just before the kick
SOUNDS_DB = 20.0      # "sounds": no more than this under the track's loud level (its 90th percentile); lower is a gap
SPARK = "▁▂▃▄▅▆▇█"
SPARK_DB = (-40.0, 0.0)


class MetersError(RuntimeError):
    """Live refused the job or ended it with an error; the message is Live's, word for word."""


def run(conn: Any, bars: str, follow: bool = False, tracks: list[str] | None = None, poll: float = 0.5,
        sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict:
    """Start the job, wait for it, return its samples. A job still running past its own timeout (plus 5 s), or
    when this is interrupted, is stopped, so Live's transport is always put back."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    def send(**p: Any) -> dict:
        try:
            return conn.send_command("live_meters", {k: v for k, v in p.items() if v is not None})
        except Exception as e:  # Live's refusal, word for word
            raise MetersError(str(e)) from e

    st = send(action="start", bars=bars, follow=follow or None, tracks=list(tracks) if tracks else None)
    deadline = clock() + st["timeout_s"] + 5.0
    try:
        while st["state"] == "running" and clock() < deadline:
            sleep(poll)
            st = send(action="status")
    finally:
        if st["state"] == "running":  # timed out here, interrupted, or the connection broke
            try:
                st = send(action="stop")
            except MetersError:
                pass
    if st["state"] == "error":
        raise MetersError(st.get("error") or "meters ended with an error")
    return st


def to_db(scale: list[float | None], v: float) -> float | None:
    """A meter reading (0..1) as dB on Live's fader scale; None is -inf."""
    if v <= 0:
        return None
    x = min(v, 1.0) * (len(scale) - 1)
    i = min(int(x), len(scale) - 2)
    lo, hi = scale[i], scale[i + 1]
    if lo is None or hi is None:
        return hi if lo is None else lo
    return round(lo + (hi - lo) * (x - i), 2)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def kick_onsets(beats: list[float], kdb: list[float]) -> list[int]:
    """Sample indices where a kick starts: the low just before the kick meter jumps KICK_RISE_DB, half a beat apart."""
    out: list[int] = []
    for i in range(1, len(beats)):
        k = i
        while k > 0 and beats[i] - beats[k - 1] <= 0.25:
            k -= 1
        k = min(k, i - 1)
        low = min(range(k, i), key=lambda j: kdb[j])
        if kdb[i] - kdb[low] < KICK_RISE_DB or kdb[i] <= kdb[i - 1]:
            continue
        start = i - 1  # where the rise began
        while start > low and kdb[start - 1] < kdb[start]:
            start -= 1
        if not out or beats[start] - beats[out[-1]] >= 0.5:
            out.append(start)
    return out


def pump(beats: list[float], tdb: list[float], kicks: list[int], spb: float) -> dict | None:
    """Per kick the track sounds across: the dip below the level just before it and how long until it is back within
    BACK_DB. It sounds across a kick when every reading from a quarter beat before it to the next kick is above
    SOUNDS_DB under the track's loud level: a gap anywhere there would be read as the dip or as the level before."""
    floor = sorted(tdb)[int(0.9 * (len(tdb) - 1))] - SOUNDS_DB
    looked, dips = 0, []
    for n, k in enumerate(kicks):
        t = (beats[k] + beats[k + 1]) / 2 if k + 1 < len(beats) else beats[k]  # it hit between the low and the jump
        end = beats[kicks[n + 1]] if n + 1 < len(kicks) else t + 1.0
        before = [tdb[j] for j in range(len(beats)) if t - 0.25 <= beats[j] <= t]
        after = [j for j in range(len(beats)) if t < beats[j] < end]
        if not before or not after:
            continue
        looked += 1
        if min(before) < floor or min(tdb[j] for j in after) < floor:
            continue  # a gap between notes, not the duck
        base = max(before)
        low = min(after, key=lambda j: tdb[j])
        back = next((j for j in after if beats[j] > beats[low] and tdb[j] >= base - BACK_DB), None)
        dips.append({"beat": round(t, 3), "dip_db": round(base - tdb[low], 1),
                     "back_ms": round((beats[back] - t) * spb * 1000) if back is not None else None})
    if not looked:
        return None
    out = {"kicks": looked, "measured": len(dips), "dip_db": None, "dip_iqr_db": None, "back_ms": None, "back_on": 0,
           "dips": dips}
    if len(dips) * 2 < looked:
        return out  # mostly gaps
    depth = [d["dip_db"] for d in dips]
    backs = [d["back_ms"] for d in dips if d["back_ms"] is not None]
    q1, _, q3 = statistics.quantiles(depth, n=4, method="inclusive") if len(depth) > 1 else depth * 3
    return dict(out, dip_db=round(statistics.median(depth), 1), dip_iqr_db=[round(q1, 1), round(q3, 1)],
                back_ms=round(statistics.median(backs)) if backs else None, back_on=len(backs))


def _kick_reference(rows: list[dict], name: str | None) -> dict | None:
    if name:
        hit = [r for r in rows if r["name"] == name] or [r for r in rows if r["name"].lower() == name.lower()]
        if not hit:
            raise MetersError(f"no track '{name}' to take the kicks from; there are: {[r['name'] for r in rows]}")
        return hit[0]
    kicks = ([r for r in rows if r["kind"] == "group" and "kick" in r["name"].lower()]
             + [r for r in rows if r["kind"] == "track" and "kick" in r["name"].lower()])
    return next((r for r in kicks if not r.get("silent")), kicks[0] if kicks else None)


def analyse(raw: dict, tracks: list[str] | None = None, kick: str | None = None) -> dict:
    """Level, peak, per-beat envelope and pump per track, in the order of the mixer's hierarchy: groups and
    ungrouped tracks by level, each group's tracks by level under it, then returns and the master."""
    scale, beats, a, b = raw["scale"], raw["beats"], raw["from_beat"], raw["to_beat"]
    spb, per_bar = 60.0 / raw["tempo"], raw.get("beats_per_bar", 4)
    n_beats = int(round(b - a))
    inside = [i for i, x in enumerate(beats) if x >= a]  # the beat before the bars is there for the first kick only
    slot = [min(n_beats - 1, int(beats[i] - a)) for i in inside]
    rows = []
    for t in raw["tracks"]:
        row = {"name": t["name"], "kind": t["kind"], "group": t.get("group"), "pump_devices": t.get("pump")}
        sent = not t.get("silent")  # Live sends no samples for a track silent throughout
        l, r = [t["l"][i] for i in inside] if sent else [], [t["r"][i] for i in inside] if sent else []
        if not any(l) and not any(r):
            rows.append(dict(row, silent=True))
            continue
        mid = [(x + y) / 2 for x, y in zip(l, r)]
        per = [[] for _ in range(n_beats)]
        for j, v in zip(slot, mid):
            per[j].append(v)
        rows.append(dict(row, level_db=to_db(scale, _mean(mid)), peak_db=to_db(scale, max(max(l), max(r))),
                         envelope=[to_db(scale, _mean(p)) if p else None for p in per],
                         _db=[to_db(scale, max(x, y)) for x, y in zip(t["l"], t["r"])]))
    ref = _kick_reference(rows, kick)
    kicks = [k for k in kick_onsets(beats, [FLOOR_DB if v is None else v for v in ref["_db"]]) if beats[k] >= a - 0.125
             ] if ref and not ref.get("silent") else []
    for r in rows:
        if r.get("pump_devices") and not r.get("silent") and r is not ref:
            r["pump"] = pump(beats, [FLOOR_DB if v is None else v for v in r["_db"]], kicks, spb) if kicks else None
    for r in rows:
        r.pop("_db", None)
        if not r.get("pump_devices"):
            r.pop("pump_devices", None)
    heard = [r for r in rows if not r.get("silent")]
    if tracks:
        heard = _only(heard, tracks)
    seconds = (beats[-1] - beats[0]) * spb if len(beats) > 1 else 0.0
    return {"bars": raw["bars"], "tempo": raw["tempo"], "state": raw["state"], "why": raw.get("why"),
            "warning": raw.get("warning"), "samples": len(inside),
            "per_second": round((len(beats) - 1) / seconds, 1) if seconds else None,
            "beats_measured": len(set(slot)), "span_beats": n_beats, "beats_per_bar": per_bar,
            "kick": {"track": ref["name"], "kicks": len(kicks)} if ref else None,
            "tracks": _hierarchy(heard),
            "silent": [r["name"] for r in rows if r.get("silent") and (not tracks or r["name"] in tracks)]}


def _only(rows: list[dict], names: list[str]) -> list[dict]:
    """The named tracks, every track inside a named group, and the groups around them (as headers)."""
    by = {r["name"]: r for r in rows}
    want = {n.lower() for n in names}

    def ancestors(r: dict) -> list[str]:
        out, g = [], r.get("group")
        while g in by:
            out.append(g)
            g = by[g].get("group")
        return out

    keep = {r["name"] for r in rows if r["name"].lower() in want or any(g.lower() in want for g in ancestors(r))}
    keep |= {g for r in rows if r["name"] in keep for g in ancestors(r)}
    return [r for r in rows if r["name"] in keep]


def _hierarchy(rows: list[dict]) -> list[dict]:
    loud = lambda r: -(r["level_db"] if r.get("level_db") is not None else -999.0)
    names = {r["name"] for r in rows}
    children: dict[str | None, list[dict]] = {}
    for r in rows:
        if r["kind"] in ("track", "group"):
            children.setdefault(r["group"] if r["group"] in names else None, []).append(r)
    out: list[dict] = []

    def walk(parent: str | None, depth: int) -> None:
        for r in sorted(children.get(parent, []), key=loud):
            out.append(dict(r, depth=depth))
            if r["kind"] == "group":
                walk(r["name"], depth + 1)

    walk(None, 0)
    out += [dict(r, depth=0) for r in sorted((r for r in rows if r["kind"] == "return"), key=loud)]
    out += [dict(r, depth=0) for r in rows if r["kind"] == "master"]
    return out


def _per_bar(env: list[float | None], per_bar: int) -> list[float | None]:
    out, step = [], int(per_bar)
    for i in range(0, len(env), step):
        got = [v for v in env[i:i + step] if v is not None]
        out.append(round(_mean(got), 1) if got else None)
    return out


def spark(env: list[float | None], per_bar: int = 4, width: int = 64) -> str:
    """The per-beat envelope as blocks from SPARK_DB[0] to SPARK_DB[1]; per bar when there are more beats than width."""
    if len(env) > width:
        env = _per_bar(env, per_bar)
    lo, hi = SPARK_DB
    out = []
    for v in env:
        if v is None:
            out.append(" ")
        else:
            x = (min(hi, max(lo, v)) - lo) / (hi - lo)
            out.append(SPARK[min(len(SPARK) - 1, int(x * len(SPARK)))])
    return "".join(out)


def _cols(text: str) -> int:
    """Terminal columns: an emoji in a group's name ("👊 KICK") takes two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _db(v: float | None) -> str:
    return "-inf" if v is None else f"{v:.1f}"


def _pump_text(r: dict) -> str:
    p, devs = r.get("pump"), ", ".join(r["pump_devices"])
    if p is None:
        return f"pump: no kicks heard to measure ({devs})"
    if p["dip_db"] is None:
        return (f"pump not measurable: it sounds across {p['measured']} of {p['kicks']} kicks, mostly gaps between "
                f"notes · {devs}")
    back = (f", back within {BACK_DB:g} dB after {p['back_ms']} ms ({p['back_on']} of {p['measured']})"
            if p["back_ms"] is not None else ", not back before the next kick")
    lo, hi = p["dip_iqr_db"]
    spread = f" (IQR {lo:.1f}-{hi:.1f})" if hi - lo >= 0.1 else ""
    on = f"{p['kicks']}" if p["measured"] == p["kicks"] else f"{p['measured']} of {p['kicks']}"
    return f"pump {p['dip_db']:.1f} dB{spread} on {on} kicks{back} · {devs}"


def render(s: dict) -> str:
    """The analysis as text: the hierarchy with level, peak, a per-beat sparkline and the pump."""
    head = (f"Meters, bars {s['bars']} at {s['tempo']:g} BPM: {s['samples']} samples"
            + (f", {s['per_second']:g} a second" if s["per_second"] else "")
            + ". dB on Live's meter scale (the fader's); level = mean, peak = highest; not LUFS.")
    lines = [head]
    if s["state"] != "done":
        lines.append(f"Ended early ({s['why']}): {s['beats_measured']} of {s['span_beats']} beats measured.")
    if s.get("warning"):
        lines.append(f"Warning: {s['warning']}")
    if s.get("kick"):
        lines.append(f"Kicks from {s['kick']['track']}: {s['kick']['kicks']}.")
    width = min(32, max([_cols("  " * r["depth"] + r["name"]) for r in s["tracks"]] + [10]))
    lines.append(f"{'':<{width}}  level   peak  per beat ({SPARK_DB[0]:g} to {SPARK_DB[1]:g} dB)")
    for r in s["tracks"]:
        name = "  " * r["depth"] + r["name"]
        while _cols(name) > width:
            name = name[:-1]
        line = f"{name}{' ' * (width - _cols(name))}  {_db(r['level_db']):>5}  {_db(r['peak_db']):>5}  {spark(r['envelope'], s['beats_per_bar'])}"
        if r.get("pump_devices"):
            line += "  " + _pump_text(r)
        lines.append(line)
    if s["silent"]:
        lines.append("Silent: " + ", ".join(s["silent"]))
    return "\n".join(lines)


def concise(s: dict) -> dict:
    """The analysis for an MCP answer: the envelope per bar instead of per beat, pump without the per-kick list."""
    out = dict(s, tracks=[])
    for r in s["tracks"]:
        row = dict(r, envelope_per_bar=_per_bar(r["envelope"], s["beats_per_bar"]))
        row.pop("envelope")
        if row.get("pump"):
            row["pump"] = {k: v for k, v in row["pump"].items() if k != "dips"}
        out["tracks"].append(row)
    return out
