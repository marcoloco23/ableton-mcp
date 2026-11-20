"""One test per finding of the PR #10 review, plus the session-view tools, on the fake Live set."""
import asyncio
import socket
import types

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fake_live import Clip, Ctrl, Device, Note, Param, Routing, Slot, Track, demo_song, live, lom
from MCP_Server.connection import AbletonConnection, AbletonError
from MCP_Server.tools import live as tools


@pytest.fixture
def song():
    return demo_song()


def run(fn, song, **p):
    return fn(song, p, Ctrl())


def notes_of(song, track):
    return [tuple(r[:3]) for r in run(live.read_notes, song, track=track)["notes"]]


# 1. a looped arrangement clip plays its loop to its end ---------------------------------------

def looped(song):
    """A 1-bar loop dragged over bars 1-8, with a kick on beat 1 and a note on beat 3."""
    t = song.tracks[0]
    t.arrangement_clips = [Clip("Loop", 0.0, 32.0, [Note(36, 0.0, 0.25), Note(40, 2.0, 0.25)], loop=(0.0, 4.0))]
    return t


def test_reading_a_looped_clip_unrolls_its_repeats(song):
    looped(song)
    assert notes_of(song, "BASS MID")[:4] == [(1, 1.0, 36), (1, 3.0, 40), (2, 1.0, 36), (2, 3.0, 40)]
    assert len(notes_of(song, "BASS MID")) == 16
    r = run(live.read_notes, song, track="BASS MID", bars="5")
    assert r["notes"] == [[5, 1.0, 36, 0.25, 100], [5, 3.0, 40, 0.25, 100]]
    assert r["clips"] == [{"name": "Loop", "bars": "1-8", "loop_bars": "1-1"}]


def test_a_start_marker_before_the_loop_plays_once_then_the_loop_repeats(song):
    t = song.tracks[0]
    t.arrangement_clips = [Clip("Intro+loop", 0.0, 16.0, [Note(30, 0.0, 1), Note(36, 4.0, 1)], loop=(4.0, 8.0))]
    assert notes_of(song, "BASS MID") == [(1, 1.0, 30), (2, 1.0, 36), (3, 1.0, 36), (4, 1.0, 36)]


def test_writing_into_a_loops_repeats_is_refused_and_changes_nothing(song):
    t = looped(song)
    with pytest.raises(ValueError, match="'Loop' holds its notes in bars 1-1 and repeats them to its end; notes in "
                                         "bars 5-5 would never play as written"):
        run(live.write_notes, song, track="BASS MID", start_bar=5, bars=1, notes=[[5, 1, 38, 0.5, 100]])
    assert len(t.arrangement_clips[0].notes) == 2
    run(live.write_notes, song, track="BASS MID", start_bar=1, bars=1, notes=[[1, 2, 38, 0.5, 100]])
    assert notes_of(song, "BASS MID")[:2] == [(1, 2.0, 38), (2, 2.0, 38)]


# 2. live_set is all or nothing ---------------------------------------------------------------

def test_set_refuses_what_a_track_cannot_do_before_changing_anything(song):
    song.tracks[0].mixer_device.volume.is_enabled = False
    cases = [("A-Reverb / arm", "on", "cannot be armed"), ("Master / mute", "on", "the master has no mute"),
             ("A-Reverb / monitor", "In", "has no monitor"), ("BASS MID / volume", "-6 dB", "Live has it disabled")]
    for target, value, why in cases:
        with pytest.raises(ValueError, match=why):
            run(live.set_values, song, changes=[{"target": "PAD / STAB / volume", "value": "-6 dB"},
                                                {"target": target, "value": value}])
    assert song.tracks[3].mixer_device.volume.value == 0.5


def test_a_change_live_refuses_puts_the_earlier_ones_back(song):
    class Stubborn(Track):
        def __setattr__(self, k, v):
            if k == "solo" and getattr(self, "ready", False):
                raise RuntimeError("Live refused solo")
            object.__setattr__(self, k, v)

    t = Stubborn("STUBBORN", [Device("EQ Eight", [Param("Gain", lambda v: "%.1f dB" % (v * 30 - 15))])])
    t.ready = True
    song.tracks.append(t)
    with pytest.raises(RuntimeError, match=r"change 3 \(STUBBORN / solo\) failed in Live: Live refused solo. "
                                           r"The 2 change\(s\) before it were put back"):
        run(live.set_values, song, changes=[{"target": "STUBBORN / volume", "value": "-6 dB"},
                                            {"target": "STUBBORN / EQ Eight / Gain", "value": "3 dB"},
                                            {"target": "STUBBORN / solo", "value": "on"}])
    assert t.mixer_device.volume.value == 0.5 and t.devices[0].parameters[1].value == 0.0


# 3. a routing channel is chosen among the new routing type's channels ------------------------------

def test_input_then_input_channel_in_one_call(song):
    t = song.tracks[0]
    channels = {"Ext. In": ["1/2"], "Resampling": ["Pre FX", "Post FX"]}
    type(t).available_input_routing_channels = property(
        lambda self: [Routing(n) for n in channels.get(self.input_routing_type.display_name, [])],
        lambda self, v: None)
    t.input_routing_channel = Routing("1/2")
    r = run(live.set_values, song, changes=[{"target": "BASS MID / input", "value": "Resampling"},
                                            {"target": "BASS MID / input channel", "value": "Post FX"}])
    assert [c["after"] for c in r["changed"]] == ["Resampling", "Post FX"]
    del type(t).available_input_routing_channels


# 4. a bad note changes nothing ------------------------------------------------------------------

@pytest.mark.parametrize("note, why", [([49, 1, 128, 0.5, 100], "pitch is 0-127"), ([49, 1, 38, 0, 100], "length"),
                                       ([49, 1, 38, 0.5, 0], "velocity is 1-127"), ([49, 5, 38, 0.5, 90], "beat is 1")])
def test_notes_are_checked_before_any_are_removed(song, note, why):
    with pytest.raises(ValueError, match=why):
        run(live.write_notes, song, track="SERUM BASS", start_bar=49, bars=4, notes=[[49, 2, 40, 0.5, 90], note])
    assert len(song.tracks[1].arrangement_clips[0].notes) == 3


# 5. transport checks before it applies --------------------------------------------------------------

def test_transport_changes_nothing_when_an_argument_is_wrong(song):
    with pytest.raises(ValueError, match="tempo is 20-999"):
        run(live.transport, song, play=True, position_bar=17, tempo=5)
    assert (song.is_playing, song.current_song_time, song.tempo) == (False, 0.0, 124.0)
    assert run(live.transport, song, play=False, record=False)["recording"] is False


# 6. a new locator needs a name before anything is scheduled ----------------------------------

def test_set_locator_without_a_name_does_nothing(song):
    with pytest.raises(ValueError, match="set needs name"):
        run(live.locators, song, action="set", bar=97)
    assert len(song.cue_points) == 2 and song.current_song_time == 0.0


# 7. a position is not a name; scenes count from 1 ---------------------------------------------

def test_digits_are_names_and_scenes_count_from_one(song):
    with pytest.raises(LookupError, match="no track '0'"):
        lom.target(song, "0 / volume")
    assert lom.scene(song, "2").name == "Groove" and lom.scene(song, "groove").name == "Groove"
    for bad in ("0", "3"):
        with pytest.raises(LookupError, match="scenes are numbered 1 to 2"):
            lom.scene(song, bad)


# 8. annotations, a clear error for set without a value, load at the end of the chain -------------

def test_simpler_is_destructive_and_set_needs_a_value(song):
    m = FastMCP("t")
    tools.register(m, lambda: None)
    simpler = next(t for t in asyncio.run(m.list_tools()) if t.name == "live_simpler")
    assert simpler.annotations.destructiveHint is True
    assert "live_record_clip" not in [t.name for t in asyncio.run(m.list_tools())]
    with pytest.raises(ToolError, match="set needs value"):
        asyncio.run(m.call_tool("live_lom", {"op": "set", "path": "tracks[PAD].mute"}))
    with pytest.raises(ValueError, match="needs a value"):
        lom.lom_set(song, {"path": "tracks[BASS MID].mute"}, Ctrl())


def test_load_selects_the_last_device_so_the_new_one_goes_at_the_end(song):
    order = []
    item = types.SimpleNamespace(name="Utility", children=[], is_loadable=True, is_folder=False)
    browser = types.SimpleNamespace(audio_effects=types.SimpleNamespace(children=[item]),
                                    load_item=lambda it: order.append(song.view.selected_device.name))
    ctrl = types.SimpleNamespace(application=lambda: types.SimpleNamespace(browser=browser))
    song.tracks[1].devices.append(Device("Saturator 2"))
    live.load(song, {"track": "SERUM BASS", "item": "Utility"}, ctrl)
    assert order == ["Saturator 2"]


def test_load_scan_is_bounded_and_says_so(song):
    deep = types.SimpleNamespace(name="Deep Thing", children=[], is_loadable=True, is_folder=False)
    for k in range(5):
        deep = types.SimpleNamespace(name="folder %d" % k, children=[deep], is_loadable=False, is_folder=True)
    browser = types.SimpleNamespace(audio_effects=types.SimpleNamespace(children=[deep]), load_item=None)
    ctrl = types.SimpleNamespace(application=lambda: types.SimpleNamespace(browser=browser))
    with pytest.raises(LookupError, match="4 folders deep.*live_browser_find"):
        live.load(song, {"track": "SERUM BASS", "item": "Deep Thing"}, ctrl)


# 9. two tracks of one name: the error lists them and the way out ----------------------------------

def test_automate_old_lane_form_names_both_tracks_of_one_name(song):
    from handlers import dispatch
    with pytest.raises(LookupError, match="2 tracks are named 'Drums', at positions 5 and 6 of 9; say 'Drums#1' or "
                                          "'Drums#2'"):
        dispatch.execute("automate", {"lanes": [{"track": "Drums", "param": "volume", "points": [[1, "0 dB"]]}]},
                         song, Ctrl())


# 10. long jobs wait longer, and a timeout says the job may still be running -------------------------

def test_long_jobs_get_a_minute_and_timeouts_say_read_before_retrying():
    from handlers import dispatch
    assert dispatch.timeout("live_overview") == 60.0 and dispatch.timeout("live_transport") == 10.0
    sent = []

    class Conn:
        def send_command(self, name, params=None, timeout=None):
            sent.append((name, timeout))
            if name == "live_track":
                raise AbletonError("Timeout waiting for dynamic operation")
            raise TimeoutError("Live did not answer live_clips within 65 s. It may still be finishing it; "
                               "read the state before retrying.")

    m = FastMCP("t")
    tools.register(m, lambda: Conn())
    with pytest.raises(ToolError, match="may still be finishing it; read the state before retrying"):
        asyncio.run(m.call_tool("live_track", {"track": "PAD"}))
    with pytest.raises(ToolError, match="read the state before retrying"):
        asyncio.run(m.call_tool("live_transport", {}))
    assert sent == [("live_track", 65.0), ("live_transport", 15.0)]


def test_the_connection_raises_a_timeout_and_drops_the_socket():
    a, b = socket.socketpair()
    conn = AbletonConnection("localhost", 0, a)
    with pytest.raises(TimeoutError, match="may still be finishing it"):
        conn.send_command("live_overview", {}, timeout=0.2)
    assert conn.sock is None
    b.close()


# session view ---------------------------------------------------------------------------------------

def test_scenes_by_name_and_number(song):
    song.tracks[1].clip_slots[1] = Slot(Clip("Groove bass", 0.0, 16.0))
    assert run(live.scenes, song)["scenes"] == [{"scene": 1, "name": "Intro", "clips": []},
                                                {"scene": 2, "name": "Groove", "clips": ["SERUM BASS"]}]
    run(live.scenes, song, action="create", scene="Intro", name="Build")
    assert [s.name for s in song.scenes] == ["Intro", "Build", "Groove"]
    run(live.scenes, song, action="rename", scene=2, name="Build 1")
    run(live.scenes, song, action="fire", scene="Groove")
    assert song.scenes[2].fired == 1
    run(live.scenes, song, action="delete", scene="Build 1")
    assert [s.name for s in song.scenes] == ["Intro", "Groove"]


def test_session_notes_count_bars_from_the_clip(song):
    r = run(live.write_notes, song, track="SERUM BASS", scene="Groove", start_bar=1, bars=2, clip_name="Groove bass",
            notes=[[1, 1, 38, 0.45, 110], [2, 2.5, 36, 0.4, 100]])
    clip = song.tracks[1].clip_slots[1].clip
    assert r["clips"] == [{"name": "Groove bass", "created": True}] and r["scene"] == 2
    assert (clip.end_marker, [(n.pitch, n.start_time) for n in clip.notes]) == (8.0, [(38, 0.0), (36, 5.5)])
    assert run(live.read_notes, song, track="SERUM BASS", scene=2)["notes"] == [[1, 1.0, 38, 0.45, 110],
                                                                               [2, 2.5, 36, 0.4, 100]]
    with pytest.raises(ValueError, match="past its end"):
        run(live.write_notes, song, track="SERUM BASS", scene=2, start_bar=3, bars=1, notes=[])
    with pytest.raises(LookupError, match="no clip in scene 1"):
        run(live.read_notes, song, track="SERUM BASS", scene="Intro")


def test_a_scene_goes_to_the_arrangement_and_session_clips_can_be_deleted(song):
    song.tracks[0].clip_slots[1] = Slot(Clip("mid", 0.0, 16.0))
    song.tracks[1].clip_slots[1] = Slot(Clip("bass", 0.0, 32.0))
    song.tracks[3].clip_slots[1] = Slot(Clip("odd", 0.0, 12.0))
    r = run(live.clips, song, action="to_arrangement", scene="Groove", bars="17-32")
    assert r["to"] == "17-32"
    assert [c.start_time for c in song.tracks[0].arrangement_clips] == [64.0, 80.0, 96.0, 112.0]  # 4 bars each
    assert [c.start_time for c in song.tracks[1].arrangement_clips[1:]] == [64.0, 96.0]           # 8 bars each
    assert r["left_empty"] == [{"track": "PAD / STAB", "name": "odd", "bars": "32-32", "why": "a 12-beat clip does not fit"}]
    with pytest.raises(ValueError, match="give scene and bars"):
        run(live.clips, song, action="to_arrangement", scene="Groove", to_bar=17)
    with pytest.raises(ValueError, match="use to_arrangement"):
        run(live.clips, song, action="duplicate", scene="Groove", to_bar=1)
    run(live.clips, song, action="delete", scene=2, track="BASS MID")
    assert not song.tracks[0].clip_slots[1].has_clip and song.tracks[1].clip_slots[1].has_clip


# an audio file into a session slot or onto the arrangement ------------------------------------------

def test_an_audio_file_lands_in_a_session_slot_with_its_clip_settings(song, tmp_path):
    wav = tmp_path / "perc loop.wav"
    wav.write_bytes(b"RIFF")
    r = run(live.load, song, track="Drums#1", item=str(wav), scene="Groove", clip={"pitch_coarse": -2, "warping": False})
    assert r == {"track": "Drums", "loaded": "perc loop.wav", "scene": 2,
                 "clip": {"name": "perc loop", "warping": False, "pitch_coarse": -2}}
    with pytest.raises(ValueError, match="already has 'perc loop' in scene 2"):
        run(live.load, song, track="Drums#1", item=str(wav), scene=2)


def test_an_audio_file_lands_on_the_arrangement_at_a_bar(song, tmp_path):
    wav = tmp_path / "vox.wav"
    wav.write_bytes(b"RIFF")
    r = run(live.load, song, track="Drums#2", item=str(wav), bar=97, clip={"name": "Vox chop"})
    assert r["bars"] == "97-100" and r["clip"]["name"] == "Vox chop"
    with pytest.raises(ValueError, match="bar 98 on 'Drums' is in 'Vox chop' .bars 97-100."):
        run(live.load, song, track="Drums#2", item=str(wav), bar=98)


def test_a_refused_clip_setting_removes_the_new_clip(song, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="clip setting pitch_corse: a clip has no 'pitch_corse'; the new clip was removed"):
        run(live.load, song, track="Drums#1", item=str(wav), scene=1, clip={"pitch_corse": 3})
    assert not song.tracks[4].clip_slots[0].has_clip


@pytest.mark.parametrize("p, why", [({"item": "perc.wav", "scene": 1}, "absolute path of an audio file"),
                                    ({"item": "/no/such.wav", "bar": 1}, "absolute path of an audio file"),
                                    ({"item": "/tmp", "scene": 1, "bar": 1}, "not both")])
def test_audio_loads_say_what_they_need(song, p, why):
    with pytest.raises(ValueError, match=why):
        run(live.load, song, track="Drums#1", **p)


def test_audio_needs_an_audio_track(song, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="'SERUM BASS' is a MIDI track"):
        run(live.load, song, track="SERUM BASS", item=str(wav), scene=1)


# numeric scenes reach the tools; slot details in one call -----------------------------------------

def test_a_scene_number_passes_the_tool_input_check():
    sent = []

    class Conn:
        def send_command(self, name, params=None, timeout=None):
            sent.append((name, params))
            return {}

    m = FastMCP("t")
    tools.register(m, lambda: Conn())
    for tool, args in [("live_scenes", {"action": "fire", "scene": 1}), ("live_notes_read", {"track": "BASS", "scene": 2}),
                       ("live_clips", {"action": "delete", "scene": 1}), ("live_load", {"track": "PERC", "item": "/a.wav", "scene": 1}),
                       ("live_notes_write", {"track": "BASS", "start_bar": 1, "bars": 1, "notes": [], "scene": 1})]:
        asyncio.run(m.call_tool(tool, args))
    assert [p["scene"] for _, p in sent] == [1, 2, 1, 1, 1]


def test_detailed_scenes_give_each_slots_clip(song, tmp_path):
    wav = tmp_path / "perc loop.wav"
    wav.write_bytes(b"RIFF")
    run(live.load, song, track="Drums#1", item=str(wav), scene=1, clip={"pitch_coarse": -2})
    song.tracks[1].clip_slots[0] = Slot(Clip("Bass loop", 0.0, 16.0))
    r = run(live.scenes, song, response_format="detailed")["scenes"][0]
    assert r["clips"] == [{"track": "SERUM BASS", "name": "Bass loop", "length_bars": 4.0},
                          {"track": "Drums", "name": "perc loop", "length_bars": 2.0, "file": str(wav), "pitch_coarse": -2}]
    assert run(live.scenes, song)["scenes"][0]["clips"] == ["SERUM BASS", "Drums"]


# fixes from the first run against real Live (Live 12.4) ---------------------------------------------

class Wrapped:
    """What Live hands back for a track: a new wrapper on every read, equal (==) to the others."""

    def __init__(self, inner):
        object.__setattr__(self, "inner", inner)

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def __setattr__(self, k, v):
        setattr(self.inner, k, v)

    def __eq__(self, other):
        return isinstance(other, Wrapped) and other.inner is self.inner

    __hash__ = None


def test_input_then_its_channel_with_fresh_track_wrappers_like_live(song):
    class Routed(Track):
        @property
        def available_input_routing_channels(self):
            return [Routing(n) for n in {"Ext. In": [], "SUB": ["Pre FX", "Post FX"]}[self.input_routing_type.display_name]]

        @available_input_routing_channels.setter
        def available_input_routing_channels(self, v):
            pass

    t = Routed("T AUDIO", audio=True)
    t.available_input_routing_types = [Routing("Ext. In"), Routing("SUB")]
    t.input_routing_type, t.input_routing_channel = t.available_input_routing_types[0], None
    inner = song.tracks + [t]
    type(song).tracks = property(lambda self: [Wrapped(x) for x in inner], lambda self, v: None)
    try:
        r = run(live.set_values, song, changes=[{"target": "T AUDIO / input", "value": "SUB"},
                                                {"target": "T AUDIO / input channel", "value": "Post FX"}])
    finally:
        del type(song).tracks
    assert [c["after"] for c in r["changed"]] == ["SUB", "Post FX"]
    assert t.input_routing_channel.display_name == "Post FX"


def test_transport_reports_the_position_it_was_given(song):
    class Lagging(type(song)):
        @property
        def current_song_time(self):
            return self.__dict__.get("_shown", 0.0)

        @current_song_time.setter
        def current_song_time(self, v):
            self.__dict__.setdefault("_shown", 288.0)  # Live applies it on its next tick

    song.__class__ = Lagging
    assert run(live.transport, song, position_bar=17)["position_bar"] == 17.0


@pytest.mark.parametrize("start, end, text", [(128.0, 136.0, "33-34"), (384.0, 384.2708, "97-97.07"),
                                              (287.966, 288.0, "72.99-73"), (256.0, 287.966, "65-72.99"),
                                              (0.0, 4.0, "1-1")])
def test_one_rule_for_spans(song, start, end, text):
    assert lom.bars_text(song, start, end) == text


def test_a_one_shot_and_a_section_ending_off_the_bar_line(song, tmp_path):
    song.tracks[4].arrangement_clips = [Clip("hit", 384.0, 384.2708)]
    assert run(live.track, song, track="Drums#1")["clips"] == "97-97.07"
    o = run(live.overview, song)
    assert [s["bars"] for s in o["sections"]] == ["1-16", "17-48", "49-97.07"]


def test_rename_answers_with_the_new_name(song):
    r = run(live.clips, song, action="rename", track="SERUM BASS", bars="49", name="Bass B")
    assert r["clips"] == [{"track": "SERUM BASS", "name": "Bass B", "bars": "49-52"}]
    assert run(live.clips, song, action="mute", track="SERUM BASS", bars="49")["clips"][0]["muted"] is True


# locators: several adds in one batch land in order, none lost ---------------------------------

class TickCtrl:
    """Like Live: a scheduled step runs on a later tick, not at once."""
    def __init__(self):
        self.queue = []

    def schedule_message(self, ticks, fn):
        self.queue.append(fn)

    def run(self):
        while self.queue:
            self.queue.pop(0)()


def test_several_new_locators_in_one_batch_all_land(song):
    ctrl = TickCtrl()
    for bar, name in [(33, "BUILD"), (97, "BREAK"), (113, "GROOVE B"), (177, "OUTRO")]:
        assert live.locators(song, {"action": "set", "bar": bar, "name": name}, ctrl)["pending"] == "set"
    ctrl.run()
    got = {(c["name"], c["bar"]) for c in live.locators(song, {"action": "list"}, ctrl)["locators"]}
    assert {("BUILD", 33.0), ("BREAK", 97.0), ("GROOVE B", 113.0), ("OUTRO", 177.0), ("INTRO", 17.0), ("GROOVE A", 49.0)} <= got
