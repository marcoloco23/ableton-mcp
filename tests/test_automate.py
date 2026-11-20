"""live_automate on a fake transport that records: each call writes its own bars and nothing else."""
import pytest

from fake_live import Device, Param, Transport, db, demo_song, lom
from handlers import dispatch


@pytest.fixture
def song():
    s = demo_song()
    s.tracks[3].devices.append(Device("Utility", [Param("Gain", db, value=0.5)]))  # PAD / STAB
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
    automate(song, live, [{"target": "PAD / STAB / Utility / Gain", "points": [[41, "-inf dB"], [57, "-4 dB"]],
                           "shape": "ease"}])
    first = dict(live.lanes[id(g)])
    assert shown(g, live.at(g, 41)) == "-inf dB"
    assert -30 < float(shown(g, live.at(g, 51)).split()[0]) < -4  # on its way up, not silent
    assert shown(g, live.at(g, 58)) == "-4.0 dB"

    automate(song, live, [{"target": "PAD / STAB / Utility / Gain", "points": [[177, "-4 dB"], [185, "-inf dB"]],
                           "shape": "ease"}])
    lane = live.lanes[id(g)]
    assert {b: v for b, v in lane.items() if b < 176 * 4} == {b: v for b, v in first.items() if b < 176 * 4}
    assert min(lane) >= 40 * 4 and min(b for b in lane if b > 58 * 4) >= 176 * 4  # nothing written between
    assert -30 < float(shown(g, live.at(g, 181)).split()[0]) < -4 and shown(g, live.at(g, 186)) == "-inf dB"


def test_the_job_hands_back_only_its_own_lanes(song):
    g, other = gain(song), song.tracks[0].mixer_device.volume
    other.automation_state = g.automation_state = 2  # both overridden by hand
    automate(song, Transport(song, [g]), [{"target": "PAD / STAB / Utility / Gain", "points": [[3, "0 dB"], [4, "-6 dB"]]}])
    assert g.automation_state == 1 and other.automation_state == 2  # the other override stays


def test_mixer_volume_is_refused_with_marcs_rule(song):
    for lane in ({"target": "PAD / STAB / volume", "points": [[3, "0 dB"], [4, "-6 dB"]]},
                 {"track": "PAD / STAB", "param": "volume", "points": [[3, "0 dB"], [4, "-6 dB"]]}):
        with pytest.raises(ValueError, match=r"fades go on a Utility's Gain \(Marc's rule\): add a Utility and "
                                             r"automate 'PAD / STAB / Utility / Gain'"):
            dispatch.execute("automate", {"lanes": [lane]}, song, Transport(song, []))
        assert dispatch._AUTOMATE.get("state") != "running"
    with pytest.raises(ValueError, match="Utility's Gain"):
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
    r = automate(song, live, [{"target": "PAD / STAB / Utility / Gain", "clear": "all", "value": "-4 dB"}])
    assert r["windows"] == [[1.0, 9.0]]
    assert {shown(g, v) for v in live.lanes[id(g)].values()} == {"-4.0 dB"}
    assert shown(g, live.at(g, 1)) == "-4.0 dB" and max(live.lanes[id(g)]) <= 32.5
