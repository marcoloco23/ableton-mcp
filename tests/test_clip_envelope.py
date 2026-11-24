"""live_clip_envelope: automation drawn into a Session clip, read back, arrangement clips refused."""
import pytest

from fake_live import Clip, Ctrl, Device, Param, Slot, demo_song, live


def rig():
    song = demo_song()
    t = song.tracks[1]
    t.devices[0].parameters = [Param("Transpose", lambda v: "%+.2f st" % v, lo=-48.0, hi=48.0, value=0.0)]
    t.clip_slots = [Slot(Clip("bend", 0.0, 8.0))] + list(t.clip_slots)[1:]
    return song, t


def draw(song, **k):
    return live.clip_envelope(song, {"target": "SERUM BASS / Serum 2 / Transpose", "scene": 1, **k}, Ctrl())


def test_a_rise_and_hold_is_drawn_and_read_back_in_shown_values():
    song, t = rig()
    r = draw(song, points=[[0, "0 st"], [2.5, "0 st"], [2.75, "+1.79 st"], [3.0, "0 st"]], shape="hold")
    env = t.clip_slots[0].clip.automation_envelope(t.devices[0].parameters[0])
    assert r["steps"] == 4 and env.value_at_time(2.9) == pytest.approx(1.79, abs=.02) and env.value_at_time(3.5) == pytest.approx(0, abs=.02)
    assert t.clip_slots[0].clip.has_envelopes and r["points"][2][1] == "+1.79 st"


def test_a_linear_ramp_rises_in_small_steps_and_the_last_value_holds_to_the_end():
    song, t = rig()
    r = draw(song, points=[[0, "0 st"], [0.25, "+1.79 st"]])
    env = t.clip_slots[0].clip.automation_envelope(t.devices[0].parameters[0])
    assert r["steps"] == 17 and env.value_at_time(0.125) == pytest.approx(0.9, abs=.15)
    assert env.value_at_time(7.9) == pytest.approx(1.79, abs=.02)


def test_the_arrangement_and_what_is_no_parameter_are_refused_before_anything():
    song, t = rig()
    with pytest.raises(ValueError, match="Session clips only.*to_arrangement"):
        live.clip_envelope(song, {"target": "SERUM BASS / Serum 2 / Transpose", "points": [[0, "0 st"]]}, Ctrl())
    with pytest.raises(ValueError, match="pitch bend is no parameter"):
        live.clip_envelope(song, {"target": "SERUM BASS / Serum 2", "scene": 1, "points": [[0, "0 dB"]]}, Ctrl())
    with pytest.raises(ValueError, match="Utility's gain"):
        live.clip_envelope(song, {"target": "SERUM BASS / volume", "scene": 1, "points": [[0, "0 dB"]]}, Ctrl())
    assert not t.clip_slots[0].clip.has_envelopes


def test_points_must_be_in_order_inside_the_clip_and_resolve():
    song, t = rig()
    for pts, word in (([[4, "0 st"], [2, "0 st"]], "in order"), ([[9, "0 st"]], "outside the clip"), ([], "empty")):
        with pytest.raises(ValueError, match=word):
            draw(song, points=pts)
    with pytest.raises(ValueError, match="has no clip"):
        draw(song, scene=2, points=[[0, "0 st"]])


def test_a_value_live_does_not_keep_is_an_error(monkeypatch):
    song, t = rig()
    import fake_live
    monkeypatch.setattr(fake_live.Envelope, "value_at_time", lambda self, x: 12.0)
    with pytest.raises(RuntimeError, match="not as asked: beat 0"):
        draw(song, points=[[0, "0 st"]])


def test_clear_empties_the_envelope():
    song, t = rig()
    draw(song, points=[[0, "+2 st"]])
    draw(song, points=[], clear=True)
    assert not t.clip_slots[0].clip.has_envelopes
