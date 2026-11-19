"""The live_* commands on a fake Live set: whole jobs by name and bar."""
import pytest

from fake_live import Clip, Ctrl, Track, demo_song, live, lom


@pytest.fixture
def song():
    return demo_song()


def run(fn, song, **p):
    return fn(song, p, Ctrl())


# --- live_set ------------------------------------------------------------------------------

def test_set_reports_before_and_after_as_shown(song):
    r = run(live.set_values, song, changes=[
        {"target": "BASS MID / EQ Eight / 1 Filter Type A", "value": "High Pass 48dB"},
        {"target": "BASS MID / EQ Eight / 1 Frequency A", "value": "110 Hz"},
        {"target": "BASS MID / volume", "value": "-6 dB"},
        {"target": "BASS MID / pan", "value": "25L"},
        {"target": "BASS MID / send A-Reverb", "value": "-inf dB"},
        {"target": "BASS MID / output", "value": "🎸 BASS"},
        {"target": "BASS MID / mute", "value": "on"},
        {"target": "BASS MID / monitor", "value": "off"},
        {"target": "BASS MID / EQ Eight", "value": "off"}])["changed"]
    assert [(c["before"], c["after"]) for c in r] == [
        ("Low Cut 48dB", "High Pass 48dB"), ("20 Hz", "110 Hz"), ("0.0 dB", "-6.0 dB"), ("C", "25L"),
        ("-inf dB", "-inf dB"), ("Master", "🎸 BASS"), ("off", "on"), ("Auto", "Off"), ("On", "Off")]
    t = song.tracks[0]
    assert t.mixer_device.sends[0].value == 0.0 and t.current_monitoring_state == 2


def test_set_changes_nothing_when_one_change_is_wrong(song):
    with pytest.raises(ValueError) as e:
        run(live.set_values, song, changes=[{"target": "BASS MID / volume", "value": "-6 dB"},
                                            {"target": "BASS MD / volume", "value": "-3 dB"},
                                            {"target": "BASS MID / volume", "value": -6}])
    msg = str(e.value)
    assert msg.startswith("nothing was changed")
    assert "change 2 (BASS MD / volume): no track 'BASS MD' (did you mean 'BASS MID'" in msg
    assert "change 3" in msg and "pass the shown value as a string" in msg
    assert song.tracks[0].mixer_device.volume.value == 0.5


def test_set_on_a_bare_track_says_what_it_takes(song):
    with pytest.raises(ValueError, match="add a device, parameter or setting"):
        run(live.set_values, song, changes=[{"target": "BASS MID", "value": 1}])


# --- notes ---------------------------------------------------------------------------------

def test_notes_read_in_song_bars(song):
    r = run(live.read_notes, song, track="SERUM BASS", bars="49-50")
    assert r["notes"] == [[49, 1.0, 38, 0.45, 110], [49, 1.75, 38, 0.2, 92], [50, 2.5, 33, 0.4, 100]]
    assert r["clips"] == [{"name": "Bass A", "bars": "49-52"}]
    assert run(live.read_notes, song, track="SERUM BASS", bars="50")["notes"] == [[50, 2.5, 33, 0.4, 100]]
    d = run(live.read_notes, song, track="SERUM BASS", response_format="detailed")["notes"][0]
    assert d == {"bar": 49, "beat": 1.0, "pitch": 38, "length": 0.45, "velocity": 110, "clip": "Bass A"}


def test_a_start_marker_moves_notes_to_where_they_play(song):
    clip = song.tracks[1].arrangement_clips[0]
    clip.start_marker = 4.0  # the clip plays from its second bar
    assert run(live.read_notes, song, track="SERUM BASS")["notes"] == [[49, 2.5, 33, 0.4, 100]]


def test_notes_write_creates_a_clip_where_there_is_none(song):
    r = run(live.write_notes, song, track="BASS MID", start_bar=33, bars=2, clip_name="Bass call",
            notes=[[33, 1, 38, 0.45, 110], {"bar": 34, "beat": 2.5, "pitch": 36, "length": 0.4}])
    clip = song.tracks[0].arrangement_clips[0]
    assert (clip.start_time, clip.end_time, clip.name) == (128.0, 136.0, "Bass call")
    assert [(n.pitch, n.start_time, n.velocity) for n in clip.notes] == [(38, 0.0, 110), (36, 5.5, 100)]
    assert r["clips"] == [{"name": "Bass call", "bars": "33-34", "created": True}] and r["added"] == 2


def test_notes_write_replaces_only_the_bars_given(song):
    r = run(live.write_notes, song, track="SERUM BASS", start_bar=50, bars=1, notes=[[50, 4.75, 50, 0.2, 104]])
    assert (r["removed"], r["added"]) == (1, 1)
    assert run(live.read_notes, song, track="SERUM BASS")["notes"] == [
        [49, 1.0, 38, 0.45, 110], [49, 1.75, 38, 0.2, 92], [50, 4.75, 50, 0.2, 104]]
    run(live.write_notes, song, track="SERUM BASS", start_bar=49, bars=1, mode="add", notes=[[49, 3, 40, 0.5, 90]])
    assert len(song.tracks[1].arrangement_clips[0].notes) == 4


def test_notes_write_refuses_a_partial_clip_and_stray_notes(song):
    with pytest.raises(ValueError, match="only partly in clips .'Bass A' 49-52.; no clip covers bars 53-56"):
        run(live.write_notes, song, track="SERUM BASS", start_bar=49, bars=8, notes=[])
    with pytest.raises(ValueError, match="a note at bar 51 beat 1 is outside bars 49-50"):
        run(live.write_notes, song, track="SERUM BASS", start_bar=49, bars=2, notes=[[51, 1, 38, 1, 100]])
    assert len(song.tracks[1].arrangement_clips[0].notes) == 3
    with pytest.raises(ValueError, match="audio track"):
        run(live.write_notes, song, track="Drums#1", start_bar=1, bars=1, notes=[])


# --- clips, tracks, transport, locators ----------------------------------------------------

def test_clips_duplicate_mute_and_move(song):
    r = run(live.clips, song, action="duplicate", track="SERUM BASS", bars="49-52", to_bar=53, times=2)
    assert r["to"] == "53-60"
    assert [(c.start_time, c.end_time) for c in song.tracks[1].arrangement_clips] == [
        (192.0, 208.0), (208.0, 224.0), (224.0, 240.0)]
    run(live.clips, song, action="mute", bars="53-60")
    assert [c.muted for c in song.tracks[1].arrangement_clips] == [False, True, True]
    with pytest.raises(ValueError, match="do not overlap"):
        run(live.clips, song, action="move", track="SERUM BASS", bars="49-52", to_bar=51)
    with pytest.raises(LookupError, match="no clip starts in bars 1-4 on 'SERUM BASS'"):
        run(live.clips, song, action="delete", track="SERUM BASS", bars="1-4")


def test_tracks_by_name(song):
    assert run(live.tracks, song, action="duplicate", track="BASS MID", name="BASS MID 2")["track"] == "BASS MID 2"
    assert song.tracks[1].name == "BASS MID 2"
    run(live.tracks, song, action="create", kind="audio", track="SERUM BASS", name="RESAMPLE")
    assert [t.name for t in song.tracks[:4]] == ["BASS MID", "BASS MID 2", "SERUM BASS", "RESAMPLE"]
    run(live.tracks, song, action="delete", track="BASS MID 2")
    assert "BASS MID 2" not in [t.name for t in song.tracks]


def drums_group(song):
    """🥁 DRUMS, a group holding TOP LOOP and KICK, after BASS MID."""
    group = Track("🥁 DRUMS")
    group.is_foldable = True
    children = [Track("TOP LOOP", audio=True), Track("KICK", audio=True)]
    for t in children:
        t.is_grouped, t.group_track = True, group
    song.tracks[1:1] = [group] + children
    return group


def test_create_in_group_lands_after_its_last_track(song):
    drums_group(song)
    r = run(live.tracks, song, action="create", kind="audio", in_group="🥁 drums", name="TOP CLAP")
    assert r == {"action": "create", "track": "TOP CLAP", "kind": "audio", "group": "🥁 DRUMS"}
    assert [t.name for t in song.tracks[:6]] == ["BASS MID", "🥁 DRUMS", "TOP LOOP", "KICK", "TOP CLAP", "SERUM BASS"]
    assert "group" not in run(live.tracks, song, action="create", name="LEAD")


def test_create_in_group_refuses_what_is_no_group_or_lands_elsewhere(song):
    drums_group(song)
    with pytest.raises(ValueError, match="'BASS MID' is not a group track"):
        run(live.tracks, song, action="create", in_group="BASS MID")
    with pytest.raises(ValueError, match="not both"):
        run(live.tracks, song, action="create", in_group="🥁 DRUMS", track="KICK")
    with pytest.raises(LookupError, match="no track 'DRUMZ'"):
        run(live.tracks, song, action="create", in_group="DRUMZ")
    count = len(song.tracks)
    song._placed = lambda t, i: t  # a Live that keeps the new track out of the group
    with pytest.raises(RuntimeError, match="outside any group, not in '🥁 DRUMS'; it was deleted again"):
        run(live.tracks, song, action="create", kind="audio", in_group="🥁 DRUMS", name="TOP CLAP")
    assert len(song.tracks) == count and "TOP CLAP" not in [t.name for t in song.tracks]


def test_transport_sets_what_is_given_and_reports(song):
    r = run(live.transport, song, loop_bars="17-208", position_bar=17, play=True, tempo=126)
    assert r == {"playing": True, "recording": False, "position_bar": 17.0, "tempo": 126.0, "signature": "4/4", "loop": True,
                 "loop_bars": "17-208", "metronome": False}


def test_locators_rename_add_and_refuse_while_playing(song):
    assert run(live.locators, song)["locators"] == [{"name": "INTRO", "bar": 17.0}, {"name": "GROOVE A", "bar": 49.0}]
    run(live.locators, song, action="set", locator="INTRO", name="INTRO 1")
    assert run(live.locators, song, action="set", bar=97, name="BREAK") == {"pending": "set", "bar": 97.0}
    assert {"name": "BREAK", "bar": 97.0} in run(live.locators, song)["locators"]
    song.is_playing = True
    with pytest.raises(RuntimeError, match="stop playback"):
        run(live.locators, song, action="delete", bar=97)


# --- reading -------------------------------------------------------------------------------

def test_overview_sections_say_what_plays(song):
    song.tracks[0].arrangement_clips = [Clip("mid", 64.0, 96.0), Clip("mid", 96.0, 128.0)]
    o = run(live.overview, song)
    assert o["sections"] == [{"name": "START", "bars": "1-16", "playing": []},
                             {"name": "INTRO", "bars": "17-48", "playing": ["BASS MID"]},
                             {"name": "GROOVE A", "bars": "49-52", "playing": ["SERUM BASS"]}]
    assert o["tracks"][0] == {"name": "BASS MID", "kind": "midi", "volume": "0.0 dB", "output": "Master",
                              "devices": ["EQ Eight"], "clips": "17-32"}
    assert run(live.overview, song, response_format="detailed")["tracks"][0]["clips"][1] == {"name": "mid", "bars": "25-32"}


def test_track_reads_every_shown_value_or_one_device(song):
    t = run(live.track, song, track="BASS MID")
    assert t["mixer"] == {"volume": "0.0 dB", "pan": "C", "sends": {"A-Reverb": "-inf dB", "B-Delay": "-inf dB"}}
    assert t["devices"][0]["params"]["1 Frequency A"] == "20 Hz" and t["devices"][0]["on"] is True
    sat = run(live.track, song, track="SERUM BASS / Saturator")
    assert sat == {"track": "SERUM BASS", "device": {"name": "Saturator", "type": "Fake", "on": True,
                                                     "params": {"Drive": "0.0 dB"}}}
    rack = run(live.track, song, track="DRUMS")["devices"][0]
    assert rack["chains"][0]["devices"][0]["params"] == {"Decay": "0.00"}
    with pytest.raises(ValueError, match="response_format"):
        run(live.track, song, track="DRUMS", response_format="short")


def test_batch_runs_any_command_in_one_round_trip(song):
    from handlers import dispatch
    r = dispatch.execute("lom_batch", {"ops": [
        {"op": "live_set", "changes": [{"target": "PAD / STAB / volume", "value": "-3 dB"}]},
        {"op": "get", "path": "tracks[PAD / STAB].mixer_device.volume"},
        {"op": "set_param", "path": "tracks[BASS MID].devices[EQ Eight]", "param": "1 Gain A", "value": "3 dB"},
        {"op": "nope"}]}, song, Ctrl())
    assert [x["ok"] for x in r["results"]] == [True, True, True, False]
    assert r["results"][1]["result"]["value"]["display"] == "-3.0 dB"
    assert "unknown op 'nope'" in r["results"][3]["error"]


def test_automate_takes_a_target(song):
    from handlers import dispatch

    class NoTick:
        def schedule_message(self, ticks, fn):
            pass

    song.session_automation_record = False
    r = dispatch.execute("automate", {"lanes": [
        {"target": "BASS MID / EQ Eight / 1 Frequency A", "points": [[49, "900 Hz"], [51, "20 kHz"]], "shape": "ease-log"},
        {"track": "PAD / STAB", "param": "volume", "points": [[49, "0 dB"], [50, "-6 dB"]]}]}, song, NoTick())
    assert r["lanes"] == ["BASS MID / EQ Eight / 1 Frequency A", "PAD / STAB / volume"]
    assert r["windows"] == [[49.0, 51.0]]
    dispatch._AUTOMATE.clear()


class Item:
    def __init__(self, name, children=(), loadable=True):
        self.name, self.children, self.is_loadable, self.is_folder = name, list(children), loadable, bool(children)


def test_load_finds_by_name_selects_the_track_and_loads(song):
    from types import SimpleNamespace
    loaded = []
    browser = SimpleNamespace(
        audio_effects=Item("Audio Effects", [Item("EQ & Filters", [Item("EQ Eight"), Item("EQ Three")], loadable=False)]),
        load_item=lambda it: (loaded.append(it.name), song.view.selected_track.devices.append(SimpleNamespace(name=it.name))))
    app = SimpleNamespace(browser=browser)
    ctrl = SimpleNamespace(application=lambda: app)
    song.view = SimpleNamespace(selected_track=None)
    r = live.load(song, {"track": "PAD / STAB", "item": "eq eight"}, ctrl)
    assert loaded == ["EQ Eight"] and r == {"track": "PAD / STAB", "loaded": "EQ Eight", "devices": ["EQ Eight"]}
    with pytest.raises(LookupError, match="no loadable browser item 'EQ 8'"):
        live.load(song, {"track": "PAD / STAB", "item": "EQ 8"}, ctrl)
    with pytest.raises(LookupError, match="close: 'EQ Eight', 'EQ Three'"):
        live.load(song, {"track": "PAD / STAB", "item": "EQ"}, ctrl)


def packs_browser(loaded=None):
    """An installed pack as Live 12 shows it: neither folder nor loadable, its Sounds a folder."""
    from types import SimpleNamespace
    pack = Item("Electric Keyboards", [Item("Sounds", [Item("Suitcase Piano", [Item("Suitcase Blender.adg")], False),
                                                       Item("Wurly Piano", [Item("Wurly Smokey.adg")], False)], False)],
                loadable=False)
    pack.is_folder = False
    return SimpleNamespace(packs=Item("Packs", [Item("Core Library", loadable=False), pack]),
                           audio_effects=Item("Audio Effects", [Item("EQ Eight")]),
                           load_item=lambda it: loaded.append(it.name))


def test_browser_find_opens_packs_and_live_load_takes_the_path(song):
    from types import SimpleNamespace
    loaded = []
    ctrl = SimpleNamespace(application=lambda: SimpleNamespace(browser=browser))
    browser = packs_browser(loaded)
    r = live.browser_find(song, {"contains": "suitcase"}, ctrl)
    assert [(m["path"], m["name"]) for m in r["matches"]] == [
        ("app.browser.packs.children.1.children.0.children.0", "Suitcase Piano"),
        ("app.browser.packs.children.1.children.0.children.0.children.0", "Suitcase Blender.adg")]
    assert r["searched"] == 8 and r["seconds"] >= 0 and "stopped" not in r
    wurly = live.browser_find(song, {"name": "Wurly Smokey.adg", "roots": ["packs"]}, ctrl)["matches"][0]
    song.view = SimpleNamespace(selected_track=None)
    assert live.load(song, {"track": "PAD / STAB", "item": wurly["path"]}, ctrl)["loaded"] == "Wurly Smokey.adg"
    assert loaded == ["Wurly Smokey.adg"]
    with pytest.raises(ValueError, match="no browser root 'pack'; roots: plugins"):
        live.browser_find(song, {"contains": "x", "roots": ["pack"]}, ctrl)


def test_browser_find_stops_on_time_and_says_so(song, monkeypatch):
    from types import SimpleNamespace
    browser = packs_browser()
    monkeypatch.setattr(live, "FIND_SECONDS", -1.0)
    r = live.browser_find(song, {"contains": "wurly"}, SimpleNamespace(application=lambda: SimpleNamespace(browser=browser)))
    assert r["matches"] == [] and r["searched"] == 1 and r["stopped"].startswith("after -1 s")
