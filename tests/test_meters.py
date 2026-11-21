"""live_meters: every meter sampled inside Live while the bars play, the transport put back, levels and pump read
from the samples (21.11.2026: socket round trips could not keep up with 20+ tracks)."""
import asyncio
import json

import pytest

from fake_live import Device, MeterTransport, Param, Song, Track, db, live
from handlers import dispatch
from MCP_Server import meters as M

DIP = 10 ** (-6 / 20.0)  # a 6 dB duck on a fader where 0.5 is 0 dB


def kick(beat):  # a kick on every beat: up for a tenth of a beat, then near silence
    v = 0.7 if beat % 1 < 0.1 else 0.1
    return v, v


def pad(beat):  # Kickstart: 6 dB down for the first quarter of each beat, then back at 0 dB
    v = 0.5 * DIP if beat % 1 < 0.25 else 0.5
    return v, v * 0.9


def hat(beat):
    return 0.3, 0.3


def sidechained():
    return Device("Glue Compressor", [Param("Threshold"), Param("S/C On", items=["Off", "On"], value=1.0)])


@pytest.fixture
def song_and_live():
    group = Track("👊 KICK", audio=True)
    group.is_foldable = True
    k = Track("KICK", audio=True)
    k.is_grouped, k.group_track = True, group
    p = Track("PAD", [Device("Kickstart 2")], audio=True)
    b = Track("BASS", [sidechained()], audio=True)
    h = Track("HAT", audio=True)
    fx = Track("FX", audio=True)
    midi = Track("NO INSTRUMENT")  # no meter at all: Live has no audio out on it
    song = Song([group, k, p, b, h, fx, midi], [Track("A-Reverb", sends=())])
    song.current_song_time, song.loop = 6.0, True
    live_ = MeterTransport(song, {group: kick, k: kick, p: pad, b: lambda t: tuple(x ** 0.5 * 0.7 for x in pad(t)),
                                  h: hat, fx: lambda t: (0.0, 0.0)})
    yield song, live_
    live._METERS.clear()
    live._clock = __import__("time").time


def start(song, live_, **p):
    return dispatch.execute("live_meters", dict({"action": "start", "bars": "3-6"}, **p), song, live_)


class Conn:
    """The socket: each command runs in the fake Live; a wait lets the transport play."""

    def __init__(self, song, live_):
        self.song, self.live, self.sent = song, live_, []

    def send_command(self, name, params=None, timeout=None):
        self.sent.append(params.get("action"))
        return json.loads(json.dumps(dispatch.execute(name, params, self.song, self.live)))


def measure(song, live_, **kw):
    conn = Conn(song, live_)
    return M.analyse(M.run(conn, kw.pop("bars", "3-6"), sleep=lambda s: live_.run(), **kw), kw.get("tracks")), conn


def test_the_bars_play_once_sampled_inside_live_and_the_transport_is_put_back(song_and_live):
    song, live_ = song_and_live
    r = start(song, live_)
    assert r["state"] == "running" and song.master_track.meter_listeners  # the master's meter drives sampling
    live_.run()
    view = dispatch.execute("live_meters", {"action": "status"}, song, live_)
    assert view["state"] == "done"
    assert (song.is_playing, song.current_song_time, song.loop) == (False, 6.0, True)  # stopped, put back
    assert song.master_track.meter_listeners == []
    assert view["beats"][0] >= 7.0 and view["beats"][-1] < 24.0  # bars 3-6 and the beat before, not the pre-roll
    rows = {t["name"]: t for t in view["tracks"]}
    assert "NO INSTRUMENT" not in rows and rows["FX"] == {"name": "FX", "kind": "track", "silent": True}
    assert rows["KICK"]["group"] == "👊 KICK" and rows["PAD"]["pump"] == ["Kickstart 2"]
    assert rows["BASS"]["pump"] == ["Glue Compressor"] and "pump" not in rows["HAT"]
    assert len(view["scale"]) == 101 and view["scale"][0] is None and view["scale"][50] == 0.0  # the fader's dB


def test_levels_peaks_envelope_and_pump_per_kick_in_the_mixers_hierarchy(song_and_live):
    s, conn = measure(*song_and_live)
    assert conn.sent[0] == "start" and conn.sent[-1] == "status"
    assert 20 <= s["per_second"] <= 31 and s["beats_measured"] == s["span_beats"] == 16
    order = [(r["name"], r["depth"]) for r in s["tracks"]]
    assert order.index(("KICK", 1)) == order.index(("👊 KICK", 0)) + 1  # a group's tracks sit under it
    top = [n for n, d in order if d == 0 and n not in ("A-Reverb", "Master")]
    levels = {r["name"]: r for r in s["tracks"]}
    assert top == sorted(top, key=lambda n: -levels[n]["level_db"])  # loudest first
    assert s["silent"] == ["FX", "A-Reverb", "Master"]
    hat_ = levels["HAT"]
    assert abs(hat_["level_db"] - 20 * __import__("math").log10(0.3 / 0.5)) < 0.2 and len(hat_["envelope"]) == 16
    assert s["kick"] == {"track": "👊 KICK", "kicks": 16}
    p = levels["PAD"]["pump"]
    assert p["kicks"] == 16 and abs(p["dip_db"] - 6.0) < 0.3
    assert 110 <= p["back_ms"] <= 200 and p["back_on"] == 16  # back a quarter beat (121 ms at 124) after the kick
    assert 2.5 < levels["BASS"]["pump"]["dip_db"] < 3.5  # the square root of the pad's duck: half the dB
    assert "pump" not in levels["HAT"]


def test_render_reads_as_a_mixer(song_and_live):
    s, _ = measure(*song_and_live)
    text = M.render(s)
    assert "Kicks from 👊 KICK: 16." in text and "\n  KICK " in text
    pad_line = next(line for line in text.splitlines() if line.startswith("PAD"))
    assert "pump 6.0 dB" in pad_line and "Kickstart 2" in pad_line
    assert text.splitlines()[-1] == "Silent: FX, A-Reverb, Master"


def test_tracks_narrows_the_answer_and_keeps_the_group_around_a_track(song_and_live):
    s, _ = measure(*song_and_live, tracks=["KICK", "PAD"])
    assert [r["name"] for r in s["tracks"]] == ["PAD", "👊 KICK", "KICK"]


def test_a_solo_refuses_before_anything_plays(song_and_live):
    song, live_ = song_and_live
    song.tracks[2].solo = True
    with pytest.raises(RuntimeError, match="SOLO IS ON: 'PAD' soloed"):
        start(song, live_)
    assert not song.is_playing and live._METERS.get("state") != "running"


def test_a_playing_transport_is_refused_unless_follow_which_leaves_it_alone(song_and_live):
    song, live_ = song_and_live
    song.is_playing = True
    with pytest.raises(RuntimeError, match="The transport is playing .* pass follow"):
        start(song, live_)
    s, _ = measure(song, live_, follow=True)
    assert s["state"] == "done" and s["beats_measured"] == 16
    assert song.is_playing and song.current_song_time >= 24.0  # still playing, never moved back


def test_an_unknown_track_is_refused_before_anything_plays(song_and_live):
    song, live_ = song_and_live
    with pytest.raises(LookupError, match="no track 'PADD' \\(did you mean 'PAD'\\?\\)"):
        start(song, live_, tracks=["PADD"])
    assert not song.is_playing


def test_a_stop_in_live_ends_the_job_with_what_it_has(song_and_live):
    song, live_ = song_and_live
    start(song, live_)
    f = live_.meters[song.tracks[3]]
    live_.meters[song.tracks[3]] = lambda t: (song.stop_playing() if t >= 16.0 else None) or f(t)
    live_.run()
    view = dispatch.execute("live_meters", {"action": "status"}, song, live_)
    assert view["state"] == "stopped" and view["why"].startswith("the transport stopped at bar 5")
    s = M.analyse(json.loads(json.dumps(view)))
    assert s["beats_measured"] == 9 and "Ended early (the transport stopped" in M.render(s)
    assert song.current_song_time == 6.0


def test_live_ends_a_job_that_outlives_its_span_and_puts_the_transport_back(song_and_live):
    song, live_ = song_and_live
    clock = iter(range(0, 10 ** 6, 1000))
    live._clock = lambda: next(clock)  # every tick a thousand seconds later
    start(song, live_)
    live_.run()
    view = dispatch.execute("live_meters", {"action": "status"}, song, live_)
    assert view["state"] == "error" and "no end of bars 3-6 within" in view["error"]
    assert (song.is_playing, song.current_song_time) == (False, 6.0)
    with pytest.raises(M.MetersError, match="no end of bars 3-6"):
        M.run(Conn(song, live_), "3-6", sleep=lambda s: live_.run())


def test_the_client_stops_a_job_that_never_ends():
    class Stuck:
        sent = []

        def send_command(self, name, params=None, timeout=None):
            self.sent.append(params["action"])
            return {"state": "stopped" if params["action"] == "stop" else "running", "timeout_s": 3.0}

    t = iter(range(100))
    conn = Stuck()
    st = M.run(conn, "3-6", sleep=lambda s: None, clock=lambda: next(t))
    assert conn.sent[0] == "start" and conn.sent[-1] == "stop" and st["state"] == "stopped"


def test_a_second_job_is_refused_while_one_runs(song_and_live):
    song, live_ = song_and_live
    start(song, live_)
    with pytest.raises(RuntimeError, match="already running"):
        start(song, live_)
    dispatch.execute("live_meters", {"action": "stop"}, song, live_)
    assert (song.is_playing, song.current_song_time) == (False, 6.0)


def test_the_mcp_tool_answers_concise_levels(song_and_live):
    from mcp.server.fastmcp import FastMCP

    from MCP_Server.tools import live as tools

    song, live_ = song_and_live
    m = FastMCP("test")
    tools.register(m, lambda: Conn(song, live_))
    import time as _t
    real, _t.sleep = _t.sleep, lambda s: live_.run()
    try:
        r = asyncio.run(m.call_tool("live_meters", {"bars": "3-6"}))
    finally:
        _t.sleep = real
    body = json.loads(r[0][0].text if isinstance(r, tuple) else r[0].text)
    pad_ = next(t for t in body["tracks"] if t["name"] == "PAD")
    assert len(pad_["envelope_per_bar"]) == 4 and "dips" not in pad_["pump"] and "envelope" not in pad_
