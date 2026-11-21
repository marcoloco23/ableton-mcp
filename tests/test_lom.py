"""Names, bars and shown values in LOM paths and targets, on a fake Live set."""
import pytest

from fake_live import Ctrl, demo_song, lom


@pytest.fixture
def song():
    return demo_song()


def get(song, path, **kw):
    return lom.lom_get(song, dict(path=path, **kw), Ctrl())["value"]


def test_path_parsing_keeps_names_whole():
    assert lom.parse_path("tracks[BASS MID].devices[EQ Eight].parameters.1") == [
        ("attr", "tracks"), ("key", "BASS MID"), ("attr", "devices"), ("key", "EQ Eight"), ("attr", "parameters"), ("index", 1)]
    assert lom.parse_path("tracks[Mr. V / 🎸 Bass [Sub]].arrangement_clips[@49.5]") == [
        ("attr", "tracks"), ("key", "Mr. V / 🎸 Bass [Sub]"), ("attr", "arrangement_clips"), ("at", 49.5)]
    with pytest.raises(ValueError, match="never closed"):
        lom.parse_path("tracks[BASS")


def test_names_numbers_and_prefixes_reach_the_same_parameter(song):
    by_name = get(song, "tracks[BASS MID].devices[EQ Eight].parameters[1 Frequency A]")
    assert by_name == get(song, "song.tracks.0.devices.0.parameters.2")
    assert by_name == get(song, "tracks[bass mid].devices[eq eight].parameters[1 frequency a]")
    assert by_name["display"] == "20 Hz" and set(by_name) == {"name", "value", "display", "min", "max", "quantized"}
    assert get(song, "tracks[BASS MID].devices[EQ Eight].parameters[1 Filter Type A]")["items"][1] == "High Pass 12dB"


def test_a_miss_lists_the_names_and_the_close_ones(song):
    with pytest.raises(LookupError) as e:
        get(song, "tracks[BASS MD]")
    assert "no track 'BASS MD' (did you mean 'BASS MID'" in str(e.value) and "'SERUM BASS'" in str(e.value)


def test_two_of_one_name_are_told_apart_by_number(song):
    with pytest.raises(LookupError, match="2 tracks are named 'Drums', at positions 5 and 6 of 6; "
                                          "say 'Drums#1' or 'Drums#2'"):
        get(song, "tracks[Drums]")
    assert lom.resolve(song, Ctrl(), "tracks[Drums#2]") is song.tracks[5]


def test_clip_by_bar_and_attributes_across_a_list(song):
    assert get(song, "tracks[SERUM BASS].arrangement_clips[@50].name") == "Bass A"
    with pytest.raises(LookupError, match="no clip at bar 53; clips are at bars 49-52"):
        get(song, "tracks[SERUM BASS].arrangement_clips[@53]")
    assert get(song, "tracks.name")[:2] == ["BASS MID", "SERUM BASS"]


def test_fields_read_many_things_per_track_in_one_call(song):
    rows = get(song, "tracks", fields=["name", "output_routing_type.display_name", "mixer_device.volume"])
    assert rows[0] == {"name": "BASS MID", "output_routing_type.display_name": "Master",
                       "mixer_device.volume": rows[0]["mixer_device.volume"]}
    assert rows[0]["mixer_device.volume"]["display"] == "0.0 dB"
    out = lom.lom_get(song, {"path": "tracks", "fields": ["name", "nope"]}, Ctrl())
    assert out["value"][0]["nope"] is None and "tracks.0.nope" in out["errors"][0]


def test_set_takes_shown_values_and_option_names(song):
    r = lom.lom_set(song, {"path": "tracks[BASS MID].mixer_device.volume", "value": "-6 dB"}, Ctrl())
    assert r["before"] == "0.0 dB" and r["display"] == "-6.0 dB"
    lom.lom_set(song, {"path": "tracks[BASS MID].output_routing_type", "value": "🎸 BASS"}, Ctrl())
    assert song.tracks[0].output_routing_type.display_name == "🎸 BASS"
    lom.lom_set(song, {"path": "tracks[BASS MID].mute", "value": True}, Ctrl())
    assert song.tracks[0].mute is True
    with pytest.raises(ValueError, match="ending in a property"):
        lom.lom_set(song, {"path": "tracks[BASS MID]", "value": 1}, Ctrl())


def test_targets_walk_tracks_devices_chains_and_settings(song):
    t = lom.target(song, "BASS MID / EQ Eight / 1 Frequency A")
    assert t.param.name == "1 Frequency A"
    assert lom.target(song, "PAD / STAB / volume").param is song.tracks[3].mixer_device.volume
    assert lom.target(song, "DRUMS / Drum Rack / Kick / Simpler / Decay").param.name == "Decay"
    assert lom.target(song, "DRUMS / Drum Rack / Macro 1").param.name == "Macro 1"
    assert lom.target(song, "BASS MID / send B").param is song.tracks[0].mixer_device.sends[1]
    assert lom.target(song, "BASS MID / send A-Reverb").param is song.tracks[0].mixer_device.sends[0]
    assert lom.target(song, "Master / Limiter").device.name == "Limiter"
    assert lom.target(song, "A-Reverb").track is song.return_tracks[0]
    assert lom.target(song, "BASS MID / output").setting == "output"
    with pytest.raises(LookupError, match="no parameter '1 Freq' on 'EQ Eight'"):
        lom.target(song, "BASS MID / EQ Eight / 1 Freq")


def test_bars():
    s = demo_song()
    assert lom.parse_bars(s, "49-52") == (192.0, 208.0)
    assert lom.parse_bars(s, 49) == (192.0, 196.0)
    assert lom.spans_text(s, [(64.0, 96.0), (96.0, 128.0), (256.0, 260.0)]) == "17-32, 65-65"
    s.signature_numerator, s.signature_denominator = 6, 8
    assert lom.beat_of(s, 3) == 6.0
    with pytest.raises(ValueError, match="should look like"):
        lom.parse_bars(s, "bar 49")
