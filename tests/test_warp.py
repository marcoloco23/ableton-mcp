"""live_warp on a fake audio clip that keeps Live 12's warp-marker rules (see fake_live.AudioClip)."""
import pytest

from fake_live import AudioClip, Ctrl, Slot, WarpMarker, demo_song, live

SECONDS, FIRST, BPM = 420.78, 0.110, 128.0


@pytest.fixture
def song():
    s = demo_song()
    s.tracks[4].name = "REF FULL"
    s.tracks[4].arrangement_clips = [AudioClip("REF Another Love", 0.0, SECONDS, tempo=128.0)]
    return s


def clip(song):
    return song.tracks[4].arrangement_clips[0]


def warp(song, **p):
    return live.warp(song, dict({"track": "REF FULL", "bar": 1, "bpm": BPM, "first_beat_s": FIRST}, **p), Ctrl())


def straight(c, first, bpm):
    """Two real markers, both on one tempo line with the first beat on beat 0, the last whole beat in the file."""
    beats = int((SECONDS - first) * bpm / 60)
    assert [[round(s, 6), round(b, 6)] for s, b in c.real] == [[first, 0.0], [round(first + beats * 60 / bpm, 6), beats]]
    assert (c.start_marker, c.loop_start, c.end_marker, c.loop_end) == (0.0, 0.0, beats, beats)


def test_the_fake_does_what_live_did_to_the_first_draft():
    """Replays the draft's calls from the real-Live run: Live ended at [0, 0.03125] + shadow 0.0625, erroring
    'doesn't exist'. Grounds the fake's shadow and keep-one rules in what Live did."""
    c = AudioClip("ref", 0.0, SECONDS, tempo=128.0)
    assert [(m.sample_time, m.beat_time) for m in c.warp_markers] == [(0.0, 0.0), (0.0146484375, 0.03125)]
    n = int((SECONDS - FIRST) * BPM / 60)
    c.add_warp_marker(WarpMarker(sample_time=FIRST + n * 60 / BPM, beat_time=n))
    c.remove_warp_marker(0.0)
    with pytest.raises(RuntimeError, match="doesn't exist"):
        c.remove_warp_marker(0.03125)  # the old shadow moved after the new marker
    c.add_warp_marker(WarpMarker(sample_time=0.0, beat_time=0.0))  # the draft's rollback
    c.add_warp_marker(WarpMarker(sample_time=0.0146484375, beat_time=0.03125))
    c.remove_warp_marker(float(n))
    with pytest.raises(RuntimeError, match="doesn't exist"):
        c.remove_warp_marker(n + 1 / 32.0)
    assert [round(m.beat_time, 6) for m in c.warp_markers] == [0.0, 0.03125, 0.0625]
    c.remove_warp_marker(0.03125)
    c.remove_warp_marker(0.0)  # the only real marker: Live does nothing
    with pytest.raises(RuntimeError, match="shadow marker can't be deleted"):
        c.remove_warp_marker(0.03125)
    with pytest.raises(TypeError, match="TWarpMarker"):
        c.add_warp_marker({"sample_time": 0.11, "beat_time": 0})


def test_an_auto_warped_clip_goes_straight_from_its_first_beat(song):
    r = warp(song)
    straight(clip(song), FIRST, BPM)
    assert r["markers_before"] == [[0.0, 0.0]] and r["markers"] == [[0.11, 0.0], [r["markers"][1][0], r["beats"]]]
    assert (r["track"], r["clip"], r["bar"], r["beats"]) == ("REF FULL", "REF Another Love", 1.0, 897)
    assert [k for k, *_ in clip(song).calls] == ["move", "add", "add", "remove"]


def test_the_tempo_changes_when_the_auto_warp_guessed_wrong(song):
    clip(song).tempo = 123.0  # Live's guess
    warp(song)
    straight(clip(song), FIRST, BPM)


def test_a_hand_warped_clip_loses_every_old_marker(song):
    clip(song).real = [[0.5, -1.0], [30.0, 60.0], [100.0, 207.0], [300.0, 640.0]]
    r = warp(song, bpm=126, first_beat_s=0.025)
    straight(clip(song), 0.025, 126.0)
    assert len(r["markers_before"]) == 4


def test_a_first_beat_at_the_old_marker_reuses_it(song):
    warp(song, first_beat_s=0.0)
    straight(clip(song), 0.0, BPM)
    assert [k for k, *_ in clip(song).calls] == ["add"]


def test_an_unwarped_clip_is_warped(song):
    clip(song)._warping = False
    r = warp(song)
    assert clip(song).warping and r["markers_before"] == "unwarped"
    straight(clip(song), FIRST, BPM)


def test_the_session_clip_in_a_scene(song):
    song.tracks[4].clip_slots[1] = Slot(AudioClip("loop", 0.0, 60.0))
    r = live.warp(song, {"track": "REF FULL", "scene": "Groove", "bpm": 124, "first_beat_s": 0.2}, Ctrl())
    assert r["scene"] == 2 and song.tracks[4].clip_slots[1].clip.real[0] == [0.2, 0.0]


def test_a_refused_step_puts_the_old_markers_back(song):
    old = [[0.5, -1.0], [30.0, 60.0], [300.0, 640.0]]
    clip(song).real = [list(m) for m in old]
    add = clip(song).add_warp_marker

    def refuse_far(m):
        if m.beat_time == 897:  # the new last marker
            raise RuntimeError("Segment length out of range.")
        add(m)
    clip(song).add_warp_marker = refuse_far
    with pytest.raises(RuntimeError, match=r"^Live refused the warp: Segment length out of range\. The old markers are back$"):
        warp(song)
    assert [[round(s, 9), round(b, 9)] for s, b in clip(song).real] == old


def test_markers_live_puts_elsewhere_are_caught_and_undone(song):
    add = clip(song).add_warp_marker
    clip(song).add_warp_marker = lambda m: add(  # the far marker lands 50 ms late
        WarpMarker(beat_time=m.beat_time, sample_time=m.sample_time + (0.05 if m.beat_time > 0 else 0)))
    with pytest.raises(RuntimeError, match=r"Live put the markers at \[\[0\.11, 0\.0\], \[420\.6288, 897\.0\]\], not .*The old markers are back$"):
        warp(song)
    assert [[round(s, 9), round(b, 9)] for s, b in clip(song).real] == [[0.0, 0.0]]


def test_an_unwarped_clip_is_unwarped_again_when_live_refuses(song):
    clip(song)._warping = False
    clip(song).add_warp_marker = lambda m: (_ for _ in ()).throw(RuntimeError("Warp marker sample time is out of range."))
    with pytest.raises(RuntimeError, match="The clip is unwarped again"):
        warp(song)
    assert not clip(song).warping


@pytest.mark.parametrize("p, why", [
    ({"bar": 5}, r"no clip on 'REF FULL' starts at bar 5; its clips: 'REF Another Love' 1-"),
    ({"scene": 1, "bar": None}, "'REF FULL' has no clip in scene 1"),
    ({"scene": 1, "bar": 1}, "not both"),
    ({"bar": None}, "give bar"),
    ({"bpm": None}, "bpm is needed"),
    ({"bpm": 12}, "bpm 12 is outside 20-999"),
    ({"first_beat_s": 421}, r"first_beat_s 421 is outside the file \(0-420.780 s\)"),
    ({"track": "REF FUL"}, "did you mean 'REF FULL'"),
])
def test_wrong_arguments_say_what_is_wrong(song, p, why):
    with pytest.raises((ValueError, LookupError), match=why):
        warp(song, **p)
    assert clip(song).calls == []


def test_a_midi_clip_does_not_warp(song):
    with pytest.raises(ValueError, match="'Bass A' on 'SERUM BASS' is a MIDI clip"):
        live.warp(song, {"track": "SERUM BASS", "bar": 49, "bpm": 124}, Ctrl())


def test_the_command_is_dispatched(song):
    from handlers import dispatch
    r = dispatch.execute("live_warp", {"track": "REF FULL", "bar": 1, "bpm": BPM, "first_beat_s": FIRST}, song, Ctrl())
    assert r["beats"] == 897

