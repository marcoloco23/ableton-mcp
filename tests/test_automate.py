"""live_automate on a fake transport that records: each call writes its own bars and nothing else."""
import re

import pytest

from fake_live import Clip, Device, Param, TickingTransport, Transport, db, demo_song, hz, lom
from handlers import dispatch, params


@pytest.fixture
def song():
    s = demo_song()
    s.tracks[3].devices.append(Device("Utility", [Param("Output", db, value=0.5)]))  # PAD / STAB
    yield s
    dispatch._AUTOMATE.clear()


def gain(song):
    return song.tracks[3].devices[0].parameters[1]


def automate(song, live, lanes):
    r = dispatch.execute("automate", {"lanes": lanes}, song, live)
    live.run()
    assert dispatch._AUTOMATE["state"] == "done", dispatch._AUTOMATE
    return r


def shown(prm, raw):
    return prm.str_for_value(raw)


def test_a_fade_from_minus_inf_is_heard_on_its_way_up_and_a_second_call_leaves_it_alone(song):
    """20.11.2026: eased in dB, -inf -> -4 dB stayed at -inf until its last tick (PAD silent over bars 41-56)."""
    g = gain(song)
    live = Transport(song, [g])
    automate(song, live, [{"target": "PAD / STAB / Utility / Output", "points": [[41, "-inf dB"], [57, "-4 dB"]],
                           "shape": "ease"}])
    first = dict(live.lanes[id(g)])
    assert shown(g, live.at(g, 41)) == "-inf dB"
    assert -30 < float(shown(g, live.at(g, 51)).split()[0]) < -4  # on its way up, not silent
    assert shown(g, live.at(g, 58)) == "-4.0 dB"

    automate(song, live, [{"target": "PAD / STAB / Utility / Output", "points": [[177, "-4 dB"], [185, "-inf dB"]],
                           "shape": "ease"}])
    lane = live.lanes[id(g)]
    assert {b: v for b, v in lane.items() if b < 176 * 4} == {b: v for b, v in first.items() if b < 176 * 4}
    assert min(lane) >= 40 * 4 and min(b for b in lane if b > 58 * 4) >= 176 * 4  # nothing written between
    assert -30 < float(shown(g, live.at(g, 181)).split()[0]) < -4 and shown(g, live.at(g, 186)) == "-inf dB"


def test_the_job_hands_back_only_its_own_lanes(song):
    g, other = gain(song), song.tracks[0].mixer_device.volume
    other.automation_state = g.automation_state = 2  # both overridden by hand
    automate(song, Transport(song, [g]), [{"target": "PAD / STAB / Utility / Output", "points": [[3, "0 dB"], [4, "-6 dB"]]}])
    assert g.automation_state == 1 and other.automation_state == 2  # the other override stays


def test_mixer_volume_is_refused_with_marcs_rule(song):
    for lane in ({"target": "PAD / STAB / volume", "points": [[3, "0 dB"], [4, "-6 dB"]]},
                 {"track": "PAD / STAB", "param": "volume", "points": [[3, "0 dB"], [4, "-6 dB"]]}):
        with pytest.raises(ValueError, match=r"fades go on a Utility's Output, its gain \(Marc's rule\): add a "
                                             r"Utility and automate 'PAD / STAB / Utility / Output'"):
            dispatch.execute("automate", {"lanes": [lane]}, song, Transport(song, []))
        assert dispatch._AUTOMATE.get("state") != "running"
    with pytest.raises(ValueError, match="Utility's Output"):
        dispatch.execute("automate", {"lanes": [{"target": "Master / volume", "points": [[1, "0 dB"], [2, "-1 dB"]]}]},
                         song, Transport(song, []))


def test_clear_writes_a_flat_line_over_its_bars_mixer_volume_included(song):
    vol = song.tracks[3].mixer_device.volume
    live = Transport(song, [vol])
    automate(song, live, [{"target": "PAD / STAB / volume", "clear": "41-57"}])  # at the value it shows now
    lane = live.lanes[id(vol)]
    assert 40 * 4 <= min(lane) and max(lane) <= 58 * 4 + 1
    assert all(abs(v - 0.5) < 1e-5 for v in lane.values())  # 0 dB, give or take the flat lane's nudge
    with pytest.raises(ValueError, match="points or clear, not both"):
        dispatch.execute("automate", {"lanes": [{"target": "PAD / STAB / volume", "clear": "1-2",
                                                 "points": [[1, "0 dB"]]}]}, song, Transport(song, []))


def test_clear_all_spans_the_song_at_the_value_given(song):
    g = gain(song)
    song.song_length = 32.0
    live = Transport(song, [g])
    r = automate(song, live, [{"target": "PAD / STAB / Utility / Output", "clear": "all", "value": "-4 dB"}])
    assert r["windows"] == [[1.0, 9.0]]
    assert {shown(g, v) for v in live.lanes[id(g)].values()} == {"-4.0 dB"}
    assert shown(g, live.at(g, 1)) == "-4.0 dB" and max(live.lanes[id(g)]) <= 32.5


def utility_gain():
    """Utility's gain as Live 12.4 has it, named Output: raw -1..1, the bottom shown as -inf dB."""
    return Param("Output", lambda v: "-inf dB" if v <= -1 + 1e-9 else "%.1f dB" % (35 * v if v > 0 else 70 * v),
                 lo=-1.0, hi=1.0, value=0.0)


def test_a_fade_from_minus_inf_rises_through_the_values_between():
    g = utility_gain()
    curve = dispatch._automate_curve(g, [[41, "-inf dB"], [57, "0 dB"]], "linear")
    raws = [v for _, v in curve]
    assert raws[0] == -1.0 and g.str_for_value(raws[-1]).lstrip("-") == "0.0 dB"
    assert all(a <= b for a, b in zip(raws, raws[1:]))
    middle = dict(curve)[(49 - 1) * 4.0]
    assert -0.6 < middle < -0.4 and g.str_for_value(middle) not in ("-inf dB", "0.0 dB")


def test_a_fade_to_minus_inf_gets_there_at_its_end():
    g = utility_gain()
    curve = dispatch._automate_curve(g, [[177, "-4 dB"], [185, "-inf dB"]], "ease")
    assert curve[-1] == ((185 - 1) * 4.0, -1.0) and g.str_for_value(curve[-1][1]) == "-inf dB"
    assert g.str_for_value(curve[0][1]) == "-4.0 dB"


def test_an_eased_fade_from_minus_inf_is_still_eased():
    g = utility_gain()
    linear = dict(dispatch._automate_curve(g, [[1, "-inf dB"], [2, "0 dB"]], "linear"))
    eased = dict(dispatch._automate_curve(g, [[1, "-inf dB"], [2, "0 dB"]], "ease"))
    assert eased[1.0] < linear[1.0] and eased[2.0] == linear[2.0] and eased[3.0] > linear[3.0]  # slow, even, slow


def test_a_recorded_fade_to_minus_inf_ends_exactly_on_it():
    song = demo_song()
    g = utility_gain()
    song.tracks[3].devices.append(Device("Utility", [g]))
    for start in (177, 178):  # either parity of the flat-lane nudge on the last ticks
        live = Transport(song, [g])
        live.STEP = 1 / 32.0  # Live's ticks fall anywhere, also in the eighth of a beat after the last point
        automate(song, live, [{"target": "PAD / STAB / Utility / Output", "points": [[start, "-4 dB"], [185, "-inf dB"]],
                               "shape": "ease"}])
        after = [v for b, v in live.lanes[id(g)].items() if b >= (185 - 1) * 4.0]
        assert after and set(after) == {-1.0}  # never a hair above -inf once the fade is down
        dispatch._AUTOMATE.clear()


def test_the_check_reads_the_recorded_curve_eased_and_in_both_lane_forms():
    """check_automation assumed straight midpoints in shown units and only {track, param} lanes: an eased fade from
    -inf failed (want -100 "dB") and a target lane raised KeyError. It now asks for _automate_curve's own value
    at the playhead."""
    song = demo_song()
    g = utility_gain()
    song.tracks[3].devices.append(Device("Utility", [g]))
    points = [[41, "-inf dB"], [57, "0 dB"]]
    live = Transport(song, [g])
    automate(song, live, [{"target": "PAD / STAB / Utility / Output", "points": points, "shape": "ease"}])
    lanes = [{"target": "PAD / STAB / Utility / Output", "points": points, "shape": "ease"},
             {"track": "PAD / STAB", "param": "Utility.Output", "points": points, "shape": "ease"}]
    for bar in (43, 45, 49, 53):
        song.current_song_time, g.value = (bar - 1) * 4.0, live.at(g, bar)  # playback: what was recorded
        r = dispatch.execute("automate_check", {"lanes": lanes}, song, live)
        assert r["bar"] == bar and [x["name"] for x in r["lanes"]] == ["PAD / STAB / Utility / Output", "PAD / STAB / Utility.Output"]
        assert all(x["off"] < 0.01 for x in r["lanes"]), r
    song.current_song_time, g.value = 44 * 4.0, -0.75  # a straight line's value a quarter of the way: not what was eased
    assert dispatch.execute("automate_check", {"lanes": lanes[:1]}, song, live)["lanes"][0]["off"] > 0.03


LANE = [{"target": "PAD / STAB / Utility / Output", "points": [[149, "-4 dB"], [153, "0 dB"]]},
        {"target": "PAD / STAB / Utility / Output", "points": [[173, "0 dB"], [177, "-4 dB"]]}]


def clips(song):
    return {t.name: [(c.start_time, c.end_time) for c in t.arrangement_clips] for t in song.tracks}


def test_a_track_playing_session_is_refused_before_anything_changes(song):
    """A pass run after scenes were auditioned cut 19 tracks' clips where Arrangement Record went on and off."""
    drums, bass = song.tracks[2], song.tracks[1]
    drums.playing_slot_index = 0
    bass.arm = True
    with pytest.raises(RuntimeError, match=r"Not recording: 'DRUMS' plays Session, not the arrangement "
                                           r"\(Back to Arrangement is lit\).*Press Back to Arrangement first\.$"):
        dispatch.execute("automate", {"lanes": LANE[:1]}, song, Transport(song, [gain(song)]))
    assert dispatch._AUTOMATE.get("state") != "running"
    assert bass.arm and song.loop and not song.record_mode and not song.is_playing  # nothing touched


@pytest.mark.parametrize("state, says", [
    ({"back_to_arranger": True}, "a track was stopped in Session, not the arrangement"),
    ({"fired": True}, "'DRUMS' plays Session"),
    ({"arrangement_overdub": True}, "MIDI Arrangement Overdub is on"),
    ({"session_record": True}, "Session Overdub is on"),
    ({"record_mode": True}, "Arrangement Record is already on"),
])
def test_every_state_that_records_more_than_automation_is_refused(song, state, says):
    if state.pop("fired", False):
        song.tracks[2].fired_slot_index = 1  # a launched clip, still blinking
    for attr, v in state.items():
        setattr(song, attr, v)
    with pytest.raises(RuntimeError, match=re.escape(says)):
        dispatch.execute("automate", {"lanes": LANE[:1]}, song, Transport(song, [gain(song)]))


def cutting(song, tracks, undoable=True):
    """Arrangement Record for a track that does not follow the arrangement, though nothing shows it: each take cuts
    the clips of TRACKS over the beats it recorded, then writes the automation, two undo steps; undoable=False puts
    the cut where no undo reaches it."""

    def cut(a, b):
        old = {t: list(t.arrangement_clips) for t in tracks}
        new = {t: [part for c in t.arrangement_clips for part in
                   ([c] if c.end_time <= a or c.start_time >= b else
                    [Clip(c.name, s, e) for s, e in ((c.start_time, a), (b, c.end_time)) if e > s])] for t in tracks}
        put = lambda clips: [setattr(t, "arrangement_clips", list(cs)) for t, cs in clips.items()]
        put(new)
        if undoable:
            song.history.append((lambda: put(old), lambda: put(new)))
        song.history.append((lambda: None, lambda: None))  # the automation

    class Cutting(type(song)):
        @property
        def record_mode(self):
            return self.take_from is not None

        @record_mode.setter
        def record_mode(self, on):
            if on and self.take_from is None:
                self.take_from = self.current_song_time
            elif not on and self.take_from is not None:
                a, self.take_from = self.take_from, None
                cut(a, self.current_song_time)

    song.take_from = None
    song.__class__ = Cutting
    return song


def test_a_pass_that_cut_clips_is_undone_to_a_match_and_says_so(song):
    drums, pad = song.tracks[2], song.tracks[4]
    drums.arrangement_clips = [Clip("Loop", 512.0, 768.0)]  # bars 129-192: both windows
    pad.arrangement_clips = [Clip("Beat", 512.0, 640.0), Clip("Beat", 640.0, 768.0)]
    earlier = []
    song.history.append((lambda: earlier.append("undone"), lambda: earlier.remove("undone")))  # Marc's own last step
    before = clips(song)
    cutting(song, [drums, pad])
    live = Transport(song, [gain(song)])
    dispatch.execute("automate", {"lanes": LANE}, song, live)
    live.run()
    a = dispatch._AUTOMATE
    assert a["state"] == "error" and a["repaired"] is True and a["touched"] == ["DRUMS", "Drums"]
    assert "changed the arrangement clips of 'DRUMS', 'Drums' (2 tracks), so it was undone (4 undos)" in a["error"]
    assert clips(song) == before and earlier == []  # back to a match, and not one step further
    assert len(song.history) == 1 and not song.record_mode and song.loop  # the set as it was


def test_an_undo_that_cannot_restore_is_an_error_naming_it_and_the_set_is_left_as_the_pass_left_it(song):
    drums = song.tracks[2]
    drums.arrangement_clips = [Clip("Loop", 512.0, 768.0)]
    marcs = []
    for k in range(3):  # Marc's own steps before the pass: undone on the way, and redone
        song.history.append((lambda k=k: marcs.append(k), lambda k=k: marcs.remove(k)))
    cutting(song, [drums], undoable=False)
    live = Transport(song, [gain(song)])
    dispatch.execute("automate", {"lanes": LANE}, song, live)
    live.run()
    a = dispatch._AUTOMATE
    assert a["state"] == "error" and a["repaired"] is False and a["touched"] == ["DRUMS"]
    assert re.search(r"changed the arrangement clips of 'DRUMS' \(1 track\), and \d+ undos did not bring them back, "
                     r"so they were redone: the set is as the pass left it", a["error"])
    assert clips(song)["DRUMS"] == [(512.0, 591.5), (608.25, 687.5), (704.25, 768.0)]  # cut at both windows, as left
    assert marcs == [] and len(song.history) == 5  # Marc's steps undone on the way and redone


def test_a_recorder_is_told_what_stops_it_and_gets_the_clips_to_compare(song):
    song.tracks[2].arrangement_clips = [Clip("Loop", 128.0, 256.0)]
    r = dispatch.execute("record_check", {}, song, None)
    assert r == {"refused": None, "clips": [["DRUMS", 128.0, 256.0, "Loop"], ["SERUM BASS", 192.0, 208.0, "Bass A"]]}
    song.arrangement_overdub = True  # harmless while Arrangement Record stays off
    assert dispatch.execute("record_check", {}, song, None)["refused"] is None
    song.tracks[0].playing_slot_index = 1
    assert "'BASS MID' plays Session" in dispatch.execute("record_check", {}, song, None)["refused"]
    song.tracks[0].playing_slot_index, song.record_mode = -1, True
    assert dispatch.execute("record_check", {}, song, None)["refused"] == "Not recording: Arrangement Record is already on."


STEPS = (48.0, 65.0, 177.0)  # the kick group's high-pass in Serenity: written on the first tick after, 0.04 bars late


@pytest.mark.parametrize("seed", range(25))
def test_a_step_lands_on_the_last_tick_before_its_time_never_after(seed):
    """23.11.2026: steps asked for at 48, 65 and 177 landed at 48.04, 65.04 and 177.04, so the high-pass opened inside
    the downbeat kick. On ticks at 10 a second and any phase, each now lands at most one tick early, never late."""
    song = demo_song()
    song.tempo = 132.0
    f = Param("Frequency", hz, value=0.0)
    song.tracks[3].devices.append(Device("Auto Filter", [f]))
    pts = []
    for s in STEPS:  # shut at s, open again half a bar on, as fillers' dropouts do
        pts += [[s - 0.01, "20 Hz"], [s, "150 Hz"], [s + 0.49, "150 Hz"], [s + 0.5, "20 Hz"]]
    live = TickingTransport(song, [f], seed)
    automate(song, live, [{"target": "PAD / STAB / Auto Filter / Frequency", "points": pts, "shape": "linear"}])
    lane, tick = sorted(live.lanes[id(f)].items()), 10 * TickingTransport.BUFFER_S * 132.0 / 60.0
    shut, open_ = params.resolve_value(f, "150 Hz"), params.resolve_value(f, "20 Hz")
    for s in STEPS:
        for at, value in (((s - 1) * 4.0, shut), ((s + 0.5 - 1) * 4.0, open_)):
            landed = next(b for b, v in lane if b > at - 1.0 and abs(v - value) < 1e-4)
            assert at - tick <= landed <= at, (seed, s, at, landed)
