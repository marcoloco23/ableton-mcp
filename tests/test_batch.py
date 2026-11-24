"""live_batch: a whole job checked first, run in order, one undo step."""
import pytest

from fake_live import *  # noqa: F401,F403
from test_live import song  # noqa: F401  (the fixture)
from handlers import dispatch


def run(song, jobs, **k):
    return dispatch.execute("live_batch", {"jobs": jobs, **k}, song, None)


VOL = {"do": "live_set", "changes": [{"target": "KICK / volume", "value": "-6 dB"}]}


def first_track(song):
    return song.tracks[0].name


def test_a_job_runs_in_one_undo_step(song):
    name = first_track(song)
    jobs = [{"do": "live_set", "changes": [{"target": name + " / mute", "value": "on"}]},
            {"do": "live_set", "changes": [{"target": name + " / pan", "value": "C"}]}]
    r = run(song, jobs)
    assert r["done"] == 2 and song.tracks[0].mute and song.steps == ["begin", "end"]


def test_preview_checks_and_changes_nothing(song):
    name = first_track(song)
    r = run(song, [{"do": "live_set", "changes": [{"target": name + " / mute", "value": "on"}]}], preview=True)
    assert "->" in r["preview"][0][0] and not song.tracks[0].mute and not hasattr(song, "steps")


def test_one_wrong_job_stops_everything_before_it_starts(song):
    name = first_track(song)
    jobs = [{"do": "live_set", "changes": [{"target": name + " / mute", "value": "on"}]},
            {"do": "live_set", "changes": [{"target": "NO SUCH / mute", "value": "on"}]}, {"do": "nonsense"}]
    with pytest.raises(ValueError, match="nothing was done") as e:
        run(song, jobs)
    assert "job 2" in str(e.value) and "job 3" in str(e.value) and not song.tracks[0].mute


def test_a_job_live_refuses_midway_closes_the_undo_step_and_says_what_is_done(song, monkeypatch):
    name = first_track(song)
    jobs = [{"do": "live_set", "changes": [{"target": name + " / mute", "value": "on"}]}, {"do": "live_transport"}]
    reg = dispatch._get_registry()

    def refuse(song, p, ctrl):
        raise RuntimeError("Live said no")

    monkeypatch.setattr(dispatch, "_get_registry", lambda: {**reg, "live_transport": {"handler": refuse, "modifying": True}})
    with pytest.raises(RuntimeError, match="job 2 .*Live said no.*1 job"):
        run(song, jobs)
    assert song.steps == ["begin", "end"] and song.tracks[0].mute  # nothing undone for the musician


def test_an_empty_batch_is_refused(song):
    with pytest.raises(ValueError, match="empty"):
        run(song, [])
