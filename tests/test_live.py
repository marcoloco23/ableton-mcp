"""The live_* commands on a fake Live set: whole jobs by name and bar."""
import pytest

from fake_live import Clip, Ctrl, Cue, Device, Note, Param, Routing, Scene, Slot, Track, demo_song, live, lom, take_input


@pytest.fixture
def song():
    return demo_song()


def run(fn, song, **p):
    return fn(song, p, Ctrl())


# --- live_set ------------------------------------------------------------------------------

def test_set_reports_before_and_after_as_shown(song):
    r = run(live.set_values, song, changes=[
        {"target": "BASS MID / EQ Eight / 1 Filter Type A", "value": "Bell"},
        {"target": "BASS MID / EQ Eight / 1 Frequency A", "value": "110 Hz"},
        {"target": "BASS MID / volume", "value": "-6 dB"},
        {"target": "BASS MID / pan", "value": "25L"},
        {"target": "BASS MID / send A-Reverb", "value": "-inf dB"},
        {"target": "BASS MID / output", "value": "🎸 BASS"},
        {"target": "BASS MID / mute", "value": "on"},
        {"target": "BASS MID / monitor", "value": "off"},
        {"target": "BASS MID / EQ Eight", "value": "off"}])["changed"]
    assert [(c["before"], c["after"]) for c in r] == [
        ("High Pass 48dB", "Bell"), ("20 Hz", "110 Hz"), ("0.0 dB", "-6.0 dB"), ("C", "25L"),
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


def test_clips_groove_by_pool_name_and_none_takes_it_off(song):
    song.tracks[0].clip_slots[1] = Slot(Clip("hats", 0.0, 16.0))
    song.tracks[1].clip_slots[1] = Slot(Clip("kick", 0.0, 16.0))
    song.tracks[1].clip_slots[1].clip.groove = song.groove_pool.grooves[0]
    r = run(live.clips, song, action="groove", scene="Groove", groove="swing mpc 3000 16ths 57")
    assert [(c["track"], c["before"], c["after"]) for c in r["clips"]] == [
        ("BASS MID", "none", "Swing MPC 3000 16ths 57"), ("SERUM BASS", "Swing Logic 16ths 52", "Swing MPC 3000 16ths 57")]
    assert song.tracks[0].clip_slots[1].clip.groove is song.groove_pool.grooves[2]
    r = run(live.clips, song, action="groove", track="SERUM BASS", bars="49", groove="None")
    assert r["clips"] == [{"track": "SERUM BASS", "name": "Bass A", "bars": "49-52", "before": "none", "after": "none"}]
    r = run(live.clips, song, action="groove", track="SERUM BASS", scene=2, groove="none")
    assert (r["clips"][0]["before"], r["clips"][0]["after"]) == ("Swing MPC 3000 16ths 57", "none")
    assert song.tracks[1].clip_slots[1].clip.groove is None


def test_clips_groove_checks_the_name_before_any_clip_changes(song):
    with pytest.raises(LookupError) as e:
        run(live.clips, song, action="groove", track="SERUM BASS", bars="49", groove="Swing MPC 16ths 57")
    assert "no groove 'Swing MPC 16ths 57' in the groove pool (did you mean 'Swing MPC 3000 16ths 57'" in str(e.value)
    with pytest.raises(ValueError, match="groove needs groove"):
        run(live.clips, song, action="groove", track="SERUM BASS", bars="49")
    assert song.tracks[1].arrangement_clips[0].groove is None


def test_clips_groove_puts_the_old_grooves_back_when_live_refuses_one(song):
    class Refusing(Clip):  # whether Live refuses a clip a groove is not measured; this proves the put-back
        @property
        def groove(self):
            return None

        @groove.setter
        def groove(self, g):
            if g is not None:
                raise RuntimeError("no groove here")

    song.tracks[0].clip_slots[1] = Slot(Clip("hats", 0.0, 16.0))
    song.tracks[1].clip_slots[1] = Slot(Refusing("loop", 0.0, 16.0))
    with pytest.raises(RuntimeError, match="'loop' on 'SERUM BASS' refused the groove: no groove here. The 1 clip"):
        run(live.clips, song, action="groove", scene="Groove", groove="Swing MPC 3000 16ths 57")
    assert song.tracks[0].clip_slots[1].clip.groove is None


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


def test_create_in_group_lands_before_its_last_track(song):
    drums_group(song)
    assert "group" not in run(live.tracks, song, action="create", track="KICK", name="AFTER KICK")  # what Marc hit
    run(live.tracks, song, action="delete", track="AFTER KICK")
    r = run(live.tracks, song, action="create", kind="audio", in_group="🥁 drums", name="TOP CLAP")
    assert r == {"action": "create", "track": "TOP CLAP", "kind": "audio", "group": "🥁 DRUMS"}
    assert [t.name for t in song.tracks[:6]] == ["BASS MID", "🥁 DRUMS", "TOP LOOP", "TOP CLAP", "KICK", "SERUM BASS"]
    assert "group" not in run(live.tracks, song, action="create", name="LEAD")


def test_create_in_group_with_one_track_or_a_group_last(song):
    group = drums_group(song)
    song.tracks.remove(next(t for t in song.tracks if t.name == "KICK"))
    assert run(live.tracks, song, action="create", in_group="🥁 DRUMS", name="CLAP")["group"] == "🥁 DRUMS"
    perc = Track("PERC")
    perc.is_foldable, perc.is_grouped, perc.group_track = True, True, group
    shaker = Track("SHAKER", audio=True)
    shaker.is_grouped, shaker.group_track = True, perc
    at = [t.name for t in song.tracks].index("TOP LOOP") + 1
    song.tracks[at:at] = [perc, shaker]  # 🥁 DRUMS: CLAP, TOP LOOP, PERC (a group: SHAKER)
    assert run(live.tracks, song, action="create", in_group="🥁 DRUMS", name="RIDE")["group"] == "🥁 DRUMS"
    assert [t.name for t in song.tracks[1:7]] == ["🥁 DRUMS", "CLAP", "TOP LOOP", "RIDE", "PERC", "SHAKER"]


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
    with pytest.raises(RuntimeError, match="outside any group, not in '🥁 DRUMS'; it was deleted again. Dupl"):
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
    song.tracks[0].arrangement_clips = [Clip("mid", 64.0, 96.0, [Note(36, 0.0, 32.0)]), Clip("mid", 96.0, 128.0)]
    o = run(live.overview, song)
    assert o["sections"] == [{"name": "START", "bars": "1-16", "playing": []},
                             {"name": "INTRO", "bars": "17-48", "playing": ["BASS MID"]},
                             {"name": "GROOVE A", "bars": "49-52", "playing": ["SERUM BASS"]}]
    assert o["tracks"][0] == {"name": "BASS MID", "kind": "midi", "volume": "0.0 dB", "output": "Master",
                              "devices": ["EQ Eight"], "clips": "17-32"}
    assert run(live.overview, song, response_format="detailed")["tracks"][0]["clips"][1] == {"name": "mid", "bars": "25-32"}


# what sounds where: input routing, monitor, mute and solo ------------------------------------

def test_a_midi_clip_sounds_only_where_its_notes_play_and_so_does_a_track_fed_by_it(song):
    """20.11.2026: SUB's clips at bars 145-152 were emptied on purpose; the checklist still put SUB and
    BASS MID (SUB's MIDI through its input) under MINI BREAK 145."""
    song.cue_points.append(type(song.cue_points[0])("MINI BREAK", 208.0))
    sub, bass_mid = song.tracks[1], song.tracks[0]
    sub.arrangement_clips += [Clip("empty", 208.0, 224.0), Clip("muted notes", 224.0, 232.0, [Note(36, 0.0, 1.0, mute=True)])]
    take_input(bass_mid, sub)
    o = run(live.overview, song, response_format="detailed")
    assert playing(o)["MINI BREAK"] == [] and playing(o)["GROOVE A"] == ["BASS MID", "SERUM BASS"]
    # the notes at beats 0-6 of the 16-beat clip at bar 49: bars 49-50; BASS MID hears the same
    assert o["tracks"][1]["sounds"] == "49-50" and o["tracks"][0]["sounds"] == "49-50"


def test_a_looped_midi_clip_sounds_in_every_repeat_and_an_audio_clip_over_its_span(song):
    song.tracks[3].arrangement_clips = [Clip("loop", 64.0, 96.0, [Note(60, 0.0, 0.5)], loop=(0.0, 8.0))]
    song.tracks[4].arrangement_clips = [Clip("hit", 128.0, 136.0)]
    song.tracks[4].arrangement_clips[0].is_midi_clip = False
    o = run(live.overview, song, response_format="detailed")
    assert o["tracks"][3]["sounds"] == "17-17, 19-19, 21-21, 23-23" and o["tracks"][4]["sounds"] == "33-34"


def playing(o):
    return {s["name"]: s["playing"] for s in o["sections"]}


def test_a_track_playing_anothers_clips_through_its_input_plays_where_they_play(song):
    """20.11.2026: BASS MID played SUB's MIDI (input SUB / Post FX, monitor In) with no clip of its own,
    and the overview said it played nowhere."""
    bass_mid, serum = song.tracks[0], song.tracks[1]
    take_input(bass_mid, serum)
    o = run(live.overview, song)
    assert o["tracks"][0] == {"name": "BASS MID", "kind": "midi", "volume": "0.0 dB", "output": "Master",
                              "devices": ["EQ Eight"], "plays_via": "SERUM BASS", "input": "SERUM BASS / Post FX",
                              "monitor": "In"}
    assert playing(o) == {"START": [], "INTRO": [], "GROOVE A": ["BASS MID", "SERUM BASS"]}
    assert "warning" not in o and "silenced_by_solo" not in o["sections"][0]


def test_monitor_in_suppresses_the_tracks_own_clips(song):
    take_input(song.tracks[0], song.tracks[1])
    song.tracks[0].arrangement_clips = [Clip("mid", 64.0, 96.0)]
    assert playing(run(live.overview, song))["INTRO"] == []


def test_auto_hears_the_input_only_while_armed_and_the_clips_too(song):
    bass_mid = song.tracks[0]
    take_input(bass_mid, song.tracks[1], monitor=1)
    bass_mid.arrangement_clips = [Clip("mid", 64.0, 96.0, [Note(36, 0.0, 1.0)])]
    o = run(live.overview, song)
    assert "plays_via" not in o["tracks"][0]
    assert playing(o) == {"START": [], "INTRO": ["BASS MID"], "GROOVE A": ["SERUM BASS"]}
    bass_mid.arm = True
    o = run(live.overview, song)
    assert o["tracks"][0]["monitor"] == "Auto (armed: where it plays no clip)"
    assert playing(o) == {"START": [], "INTRO": ["BASS MID"], "GROOVE A": ["BASS MID", "SERUM BASS"]}


def test_monitor_off_hears_no_input(song):
    take_input(song.tracks[0], song.tracks[1], monitor=2)
    o = run(live.overview, song)
    assert "plays_via" not in o["tracks"][0] and playing(o)["GROOVE A"] == ["SERUM BASS"]


@pytest.mark.parametrize("channel, heard", [("Post FX", True), ("Pre FX", True), ("Post Mixer", False)])
def test_a_muted_source_still_feeds_before_its_mixer_not_after(song, channel, heard):
    take_input(song.tracks[0], song.tracks[1], channel=channel)
    song.tracks[1].mute = True
    assert playing(run(live.overview, song))["GROOVE A"] == (["BASS MID"] if heard else [])


def test_resampling_plays_where_anything_else_plays(song):
    rec = Track("RESAMPLE", audio=True)
    song.tracks.append(rec)
    take_input(rec, "Resampling")
    o = run(live.overview, song)
    assert o["tracks"][-1]["plays_via"] == "Resampling" and o["tracks"][-1]["input"] == "Resampling"
    assert playing(o) == {"START": [], "INTRO": [], "GROOVE A": ["SERUM BASS", "RESAMPLE"]}


def test_a_group_mute_silences_its_tracks_and_a_group_feeds_what_its_tracks_play(song):
    group, serum, bass_mid = Track("🎸 BASS"), song.tracks[1], song.tracks[0]
    group.is_foldable, group.can_be_armed = True, False
    song.tracks.insert(1, group)
    serum.is_grouped, serum.group_track = True, group
    take_input(bass_mid, group)
    assert playing(run(live.overview, song))["GROOVE A"] == ["BASS MID", "SERUM BASS"]
    group.mute = True
    assert playing(run(live.overview, song))["GROOVE A"] == ["BASS MID"]


def test_tracks_feeding_each_other_end(song):
    take_input(song.tracks[0], song.tracks[3])
    take_input(song.tracks[3], song.tracks[0])
    assert playing(run(live.overview, song))["GROOVE A"] == ["SERUM BASS"]


def test_a_solo_comes_first_and_the_sections_say_what_it_silences(song):
    """20.11.2026: SERUM sat soloed all evening and every recording held only SERUM."""
    take_input(song.tracks[0], song.tracks[1])
    song.tracks[3].arrangement_clips = [Clip("stab", 192.0, 200.0, [Note(60, 0.0, 0.5)])]
    song.tracks[1].solo = True
    for t in song.tracks[:1] + song.tracks[2:] + song.return_tracks:  # Live's own answer, read as is
        t.muted_via_solo = True
    o = run(live.overview, song)
    assert list(o)[:2] == ["warning", "soloed"] and o["soloed"] == ["SERUM BASS"]
    assert o["warning"].startswith("SOLO IS ON: 'SERUM BASS' soloed, so 5 other tracks are silent")
    groove = o["sections"][2]
    assert groove["playing"] == ["SERUM BASS"] and groove["silenced_by_solo"] == ["BASS MID", "PAD / STAB"]
    assert o["tracks"][1]["flags"] == ["solo"] and o["tracks"][0]["flags"] == ["silenced by solo"]
    assert o["returns"][0]["flags"] == ["silenced by solo"]
    assert "warning" in run(live.overview, song, response_format="detailed")


# --- the audit -------------------------------------------------------------------------------

def audited(song):
    return dict((i["check"], i["text"]) for i in run(live.audit, song)["issues"])


def test_audit_names_what_trips_the_work_up(song):
    """21.11.2026, Serenity: four tracks named HOOK, a REV CRASH clip 0 beats long at bar 145, an OPEN HAT seat
    with its Simpler loaded duplicated, leftovers of a recording and a candidates round, clips past END."""
    song.tracks += [Track("HOOK"), Track("HOOK"), Track("HOOK"), Track("REV CRASH", audio=True),
                    Track("OPEN HAT", [Device("Simpler", type=1)]), Track("REC KICK", audio=True), Track("KICK CAND", audio=True)]
    song.tracks[-4].arrangement_clips = [Clip("rev", 576.0, 576.0), Clip("rev", 380.0, 384.0)]
    song.tracks[1].arrangement_clips.append(Clip("tail", 816.0, 832.0, [Note(38, 0.0, 1.0)]))
    song.cue_points += [Cue("START", 0.0), Cue("END", 816.0)]
    song.scenes += [Scene("CANDIDATES 1"), Scene("CANDIDATES 2")]
    song.tracks[4].mute = True
    take_input(song.tracks[0], song.tracks[1])  # BASS MID plays SERUM BASS: no seat
    a = audited(song)
    names = [i["text"] for i in run(live.audit, song)["issues"] if i["check"] == "duplicate names"]
    assert names[0].startswith("2 tracks named 'Drums' (tracks 5, 6)")  # the demo's two Drums
    assert names[1] == ("3 tracks named 'HOOK' (tracks 7, 8, 9): live_track and the recorder refuse the name; say "
                        "'HOOK#1', 'HOOK#2', 'HOOK#3'")
    assert lom.track(song, "HOOK#2") is song.tracks[7]  # the form the line gives addresses the track
    assert a["short clips"] == "clips under a 16th, all but silent: REV CRASH at 145 (0 beats)"
    assert a["past END"] == "clips run past the END locator (bar 205): SERUM BASS to 208"  # 816-832: bars 205-208
    assert "locators" not in a
    assert a["leftovers"] == ("tracks REC KICK, KICK CAND; scenes CANDIDATES 1, CANDIDATES 2: left by a recording or a "
                              "candidates round")
    assert a["muted"] == "muted: Drums"
    assert a["unused seats"].endswith("adding a track): DRUMS, OPEN HAT")  # a Drum Rack and a Simpler, no clip
    assert "solo" not in a


def test_audit_says_a_missing_locator_and_the_solo(song):
    song.tracks[1].solo = True
    a = audited(song)
    assert a["locators"] == "no START or END locator"
    assert a["solo"].startswith("SOLO IS ON: 'SERUM BASS' soloed")


def test_the_sidechain_map_groups_by_what_keys_it(song):
    comp = Device("Compressor", [Param("S/C On", items=["Off", "On"], value=1.0)], class_name="Compressor2")
    comp.input_routing_type, comp.input_routing_channel = Routing("👊 KICK"), Routing("Post FX")
    off = Device("Compressor", [Param("S/C On", items=["Off", "On"], value=0.0)], class_name="Compressor2")
    glue = Device("Glue Compressor", [Param("S/C On", items=["Off", "On"], value=1.0)], class_name="GlueCompressor")
    song.tracks[3].devices += [Device("Kickstart 2", class_name="PluginDevice"), comp]
    song.tracks[0].devices += [Device("Kickstart 2", class_name="PluginDevice"), off]
    song.return_tracks[0].devices.append(glue)
    assert run(live.audit, song)["sidechain"] == [
        {"device": "Kickstart 2", "keyed_by": None, "tracks": ["BASS MID", "PAD / STAB"]},
        {"device": "Compressor", "keyed_by": "👊 KICK / Post FX", "tracks": ["PAD / STAB"]},
        {"device": "Glue Compressor", "keyed_by": None, "tracks": ["A-Reverb"]}]


def test_the_overview_carries_the_audit_lines_and_says_the_solo_once(song):
    song.tracks[1].solo = True
    o = run(live.overview, song)
    assert list(o)[:3] == ["warning", "soloed", "audit"]
    assert o["audit"][0] == "2 tracks named 'Drums' (tracks 5, 6): live_track and the recorder refuse the name; say 'Drums#1', 'Drums#2'"
    assert not any(line.startswith("SOLO") for line in o["audit"])


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
        {"track": "PAD / STAB", "param": "send A", "points": [[49, "0 dB"], [50, "-6 dB"]]}]}, song, NoTick())
    assert r["lanes"] == ["BASS MID / EQ Eight / 1 Frequency A", "PAD / STAB / send A"]
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
