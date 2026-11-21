"""Arrangement automation through the saved set, on a fake Live and a small .als shaped like Live 12's."""
import gzip
import math
import os
import sys
import time
from pathlib import Path

import pytest

from fake_live import Ctrl, Device, Param, Song, Track, hz
from handlers import dispatch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "MCP_Server"))
import saved_set as S  # noqa: E402


def send_db(v):  # a send as Live 12.4 shows it (read 20.11.2026): raw 1.0 is 0 dB, 0.85 is -6 dB, 0.7 is -12 dB
    return "-inf dB" if v <= 0 else "%.1f dB" % (40 * (v - 1))


def utility_db(v):  # Utility's gain, raw -1..1: -inf .. +35 dB
    return "-inf dB" if v <= -1 else "%.2f dB" % (35 * v if v >= 0 else 70 * v)


SET = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12402" Creator="Ableton Live 12.4.16"><LiveSet><Tracks>
 <AudioTrack Id="8"><Name><EffectiveName Value="PAD"/></Name>
  <AutomationEnvelopes><Envelopes>{envelopes}</Envelopes></AutomationEnvelopes>
  <DeviceChain>
   <Mixer>
    <Volume><Manual Value="0.63"/><MidiControllerRange><Min Value="0.0003162277571"/><Max Value="1.99526238"/></MidiControllerRange><AutomationTarget Id="100"/></Volume>
    <Pan><Manual Value="0"/><MidiControllerRange><Min Value="-1"/><Max Value="1"/></MidiControllerRange><AutomationTarget Id="101"/></Pan>
    <Sends>
     <TrackSendHolder Id="0"><Send><Manual Value="0.1995"/><MidiControllerRange><Min Value="0.0003162277571"/><Max Value="1"/></MidiControllerRange><AutomationTarget Id="102"/></Send></TrackSendHolder>
     <TrackSendHolder Id="1"><Send><Manual Value="0.0003162277571"/><MidiControllerRange><Min Value="0.0003162277571"/><Max Value="1"/></MidiControllerRange><AutomationTarget Id="103"/></Send></TrackSendHolder>
    </Sends>
   </Mixer>
   <DeviceChain><Devices>
    <AutoFilter2 Id="0">
     <On><Manual Value="true"/><AutomationTarget Id="110"/></On>
     <Filter_Frequency><Manual Value="1000"/><MidiControllerRange><Min Value="19.9999981"/><Max Value="19999.9961"/></MidiControllerRange><AutomationTarget Id="111"/></Filter_Frequency>
    </AutoFilter2>
    <StereoGain Id="1">
     <On><Manual Value="true"/><AutomationTarget Id="120"/></On>
     <Gain><Manual Value="1"/><MidiControllerRange><Min Value="0"/><Max Value="56.2341309"/></MidiControllerRange><AutomationTarget Id="121"/></Gain>
     <LegacyGain><Manual Value="0"/><MidiControllerRange><Min Value="-35"/><Max Value="35"/></MidiControllerRange><AutomationTarget Id="122"/></LegacyGain>
     <Mute><Manual Value="false"/><AutomationTarget Id="123"/></Mute>
    </StereoGain>
   </Devices></DeviceChain>
  </DeviceChain>
 </AudioTrack>
 <ReturnTrack Id="20"><Name><EffectiveName Value="A-VERB"/></Name><AutomationEnvelopes><Envelopes/></AutomationEnvelopes>
  <DeviceChain><Mixer/><DeviceChain><Devices/></DeviceChain></DeviceChain></ReturnTrack>
 <ReturnTrack Id="21"><Name><EffectiveName Value="B-DELAY"/></Name><AutomationEnvelopes><Envelopes/></AutomationEnvelopes>
  <DeviceChain><Mixer/><DeviceChain><Devices/></DeviceChain></DeviceChain></ReturnTrack>
</Tracks><MainTrack><Name><EffectiveName Value="Main"/></Name><AutomationEnvelopes><Envelopes/></AutomationEnvelopes>
  <DeviceChain><Mixer/><DeviceChain><Devices/></DeviceChain></DeviceChain></MainTrack></LiveSet></Ableton>"""

OLD_SEND_LANE = """<AutomationEnvelope Id="0"><EnvelopeTarget><PointeeId Value="102"/></EnvelopeTarget><Automation><Events>
 <FloatEvent Id="1" Time="-63072000" Value="0.1995"/><FloatEvent Id="2" Time="0" Value="0.1995"/>
 <FloatEvent Id="3" Time="32" Value="0.5"/><FloatEvent Id="4" Time="400" Value="0.5"/><FloatEvent Id="5" Time="480" Value="0.1"/>
</Events><AutomationTransformViewState><IsTransformPending Value="false"/><TimeAndValueTransforms/></AutomationTransformViewState>
</Automation></AutomationEnvelope>"""


class Conn:
    """The socket, answered by the Remote Script's own dispatch on a fake Song."""

    def __init__(self, song):
        self.song, self.sent = song, []

    def send_command(self, name, params=None, timeout=None):
        self.sent.append((name, params))
        return dispatch.execute(name, params or {}, self.song, Ctrl())


def pad():
    t = Track("PAD", [Device("Auto Filter", [Param("Frequency", hz, value=math.log(1000 / 20, 1000))], class_name="AutoFilter2"),
                      Device("Utility", [Param("Output", utility_db, lo=-1.0, value=0.0),
                                         Param("Mute", items=["Off", "On"])], class_name="StereoGain")],
              audio=True, sends=("A-VERB", "B-DELAY"))
    for s in t.mixer_device.sends:
        s.show = send_db
    t.mixer_device.sends[0].value = 0.65  # -14 dB, as saved
    return t


@pytest.fixture(autouse=True)
def no_real_live(monkeypatch):
    def refuse(path):
        raise AssertionError(f"a test tried to open {path} in the real Live")
    monkeypatch.setattr(S, "_open_in_live", refuse)


def journal(prefs, started, last):
    """Live's undo journal as measured on 21.11.2026: a new one (bands.cfg) when a set opens, its band written
    with every undo step and a few ms after every save."""
    undo = prefs / "Live 12.4.16" / "Undo"
    undo.mkdir(parents=True, exist_ok=True)
    for name, t in (("bands.cfg", started), ("0.band", last)):
        (undo / name).write_bytes(b"")
        os.utime(undo / name, (t, t))


@pytest.fixture
def live_set(tmp_path):
    """A set opened at 900 s and saved at 1000 s, and a fake Live holding it."""
    def make(envelopes=""):
        path = tmp_path / "Serenity Project" / "Serenity.als"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(gzip.compress(SET.format(envelopes=envelopes).encode()))
        os.utime(path, (1000.0, 1000.0))
        journal(tmp_path / "prefs", 900.0, 1000.004)
        song = Song([pad()], [Track("A-VERB", sends=()), Track("B-DELAY", sends=())])
        song.file_path = str(path)
        return path, tmp_path / "prefs", Conn(song)
    return make


def stamp(t):
    return time.strftime("%Y-%m-%d %H%M%S", time.localtime(t))


class Opens:
    """Live opening a set: it switches to the file, starts a new journal, and its plug-ins record steps."""

    def __init__(self, conn, prefs):
        self.conn, self.prefs, self.paths = conn, prefs, []

    def __call__(self, path):
        self.paths.append(path)
        self.conn.song.file_path = str(path)
        os.utime(path, (1990.0, 1990.0))  # written just before
        journal(self.prefs, 2000.0, 2000.4)


def lane(path, pointee):
    root = S.load(path)
    env = next(e for e in root.iter("AutomationEnvelope") if e.find("EnvelopeTarget/PointeeId").get("Value") == pointee)
    return [(float(e.get("Time")), e.get("Value")) for e in env.find("Automation/Events")]


def at(events, beat):
    return float(S._value_at([(t, float(v)) for t, v in events], beat))


def test_lanes_land_in_the_saved_set_in_each_controls_own_unit_and_the_original_is_kept(live_set):
    path, prefs, conn = live_set()
    original = path.read_bytes()
    r = S.patch(conn, [
        {"target": "PAD / Utility / Gain", "points": [[41, "-inf dB"], [57, "-6 dB"]]},
        {"target": "PAD / send A", "points": [[96.5, "-14 dB"], [96.75, "-6 dB"], [97, "-14 dB"]]},
        {"target": "PAD / Auto Filter / Frequency", "points": [[89, "900 Hz"], [97, "20 kHz"]], "shape": "ease-log"},
    ], prefs=prefs, now=0, opener=Opens(conn, prefs))
    assert [x["target"] for x in r["lanes"]] == ["PAD / Utility / Output", "PAD / send A", "PAD / Auto Filter / Frequency"]
    assert path.read_bytes() == original and open(r["backup"], "rb").read() == original  # the set itself is untouched
    assert r["set"] == str(path.with_name(f"Serenity [auto {stamp(0)}].als")) and r["opened"]
    new = r["set"]

    gain = lane(new, "121")  # Utility's Gain is linear gain in the XML: -inf is 0, -6 dB is 0.501
    assert gain[0] == (-63072000.0, "1") and at(gain, 160) == 0.0 and at(gain, 224) == pytest.approx(10 ** (-6 / 20), rel=1e-3)
    assert 10 ** (-40 / 20) < at(gain, 192) < 10 ** (-6 / 20)  # on its way up halfway, not silent until the end

    send = lane(new, "102")  # -6 dB on a send is 0.501 in the XML, never the fader's raw 0.85
    assert at(send, 383) == pytest.approx(10 ** (-6 / 20), rel=1e-3) and at(send, 382) == pytest.approx(10 ** (-14 / 20), rel=1e-3)

    freq = lane(new, "111")
    assert at(freq, 352) == pytest.approx(900) and at(freq, 384) == pytest.approx(20000)
    assert at(freq, 368) == pytest.approx(math.sqrt(900 * 20000), rel=0.02)  # ease-log: the middle is the geometric mean


def test_what_was_written_reads_back_in_bars_and_shown_values(live_set):
    path, prefs, conn = live_set()
    S.patch(conn, [{"target": "PAD / send A", "points": [[96.5, "-14 dB"], [96.75, "-6 dB"], [97, "-14 dB"]]},
                   {"target": "PAD / Auto Filter / Frequency", "points": [[89, "900 Hz"], [97, "20 kHz"]]}],
            prefs=prefs, opener=Opens(conn, prefs))
    got = {x["target"]: x for x in S.lanes(conn, "PAD", prefs=prefs)["lanes"]}
    assert got["PAD / send A"]["points"] == [[96.5, "-14.0 dB"], [96.75, "-6.0 dB"], [97.0, "-14.0 dB"]]
    assert got["PAD / Auto Filter / Frequency"]["points"] == [[89.0, "900 Hz"], [97.0, "20.00 kHz"]]
    assert got["PAD / send A"]["before"] == "-14.0 dB"
    assert [x["target"] for x in S.lanes(conn, "PAD / send A", prefs=prefs)["lanes"]] == ["PAD / send A"]


def test_an_existing_lane_changes_only_over_the_written_bars(live_set):
    path, prefs, conn = live_set(OLD_SEND_LANE)
    r = S.patch(conn, [{"target": "PAD / send A", "points": [[96.5, "-inf dB"], [97, "-inf dB"]]}], prefs=prefs,
                open_it=False)
    ev = lane(r["set"], "102")
    assert at(ev, 100) == pytest.approx(0.5) and at(ev, 470) == pytest.approx(0.15)   # the old lane, before and after
    assert at(ev, 383) == pytest.approx(0.0003162277571)
    assert (382.0, repr(0.5)) in ev and (384.0, repr(0.5)) in ev                      # jumps back at both edges


def test_nothing_is_written_while_live_holds_changes_the_file_lacks(live_set):
    path, prefs, conn = live_set()
    original = path.read_bytes()
    journal(prefs, 900.0, 1030.0)  # a change 30 s after the save
    with pytest.raises(S.Refused, match=r"changes since Serenity.als was saved at .*Save the set in Live"):
        S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs)
    assert path.read_bytes() == original and not (path.parent / "Backup").exists()
    assert [p.name for p in path.parent.iterdir()] == ["Serenity.als"]


def test_a_set_just_opened_counts_as_changed_because_its_plugins_record_steps_while_loading(live_set):
    """21.11.2026: the journal of a freshly opened set held "Change Plug-in Parameter List" and "Change \"Program\""
    steps, Live asked "Save changes?" before switching away from it, and the check said unsaved."""
    path, prefs, conn = live_set()
    journal(prefs, 1500.0, 1500.4)  # opened at 1500 s, untouched since
    with pytest.raises(S.Refused, match="changed since it opened at .* plug-ins record steps while a set loads"):
        S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs)
    journal(prefs, 1500.0, 1600.004)
    os.utime(path, (1600.0, 1600.0))  # saved once in Live
    assert S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs,
                   open_it=False)["set"]


def test_live_switches_to_the_new_file_and_the_next_write_waits_for_a_save_there(live_set):
    path, prefs, conn = live_set()
    opens = Opens(conn, prefs)
    first = S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs,
                    now=0, opener=opens)
    assert opens.paths == [Path(first["set"])] and conn.song.file_path == first["set"]
    assert "Cmd+S now saves there" in first["next"]
    with pytest.raises(S.Refused, match="changed since it opened"):
        S.patch(conn, [{"target": "PAD / send B", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs, opener=opens)
    os.utime(first["set"], (2100.0, 2100.0))  # Cmd+S in Live, into the new file
    journal(prefs, 2000.0, 2100.004)
    second = S.patch(conn, [{"target": "PAD / send B", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs,
                     now=60, opener=opens)
    assert Path(second["set"]).name == f"Serenity [auto {stamp(60)}].als"  # named after the set, not round one
    os.utime(second["set"], (2200.0, 2200.0))
    journal(prefs, 2000.0, 2200.004)  # Live opened round two; Marc saved it
    lanes = {x["target"] for x in S.lanes(conn, "PAD", prefs=prefs)["lanes"]}
    assert lanes == {"PAD / send A", "PAD / send B"}  # round two is written onto round one, not onto the old set


def test_a_change_while_the_lanes_are_written_leaves_live_where_it_is(live_set, monkeypatch):
    path, prefs, conn = live_set()
    opens, dump = Opens(conn, prefs), S.dump

    def marc_edits_meanwhile(root):
        journal(prefs, 900.0, 1050.0)
        return dump(root)
    monkeypatch.setattr(S, "dump", marc_edits_meanwhile)
    r = S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs, opener=opens)
    assert opens.paths == [] and not r["opened"] and "left as it is" in r["next"]
    assert Path(r["set"]).exists() and conn.song.file_path == str(path)


def test_a_save_in_live_while_the_lanes_are_written_leaves_live_where_it_is(live_set, monkeypatch):
    path, prefs, conn = live_set()
    opens, dump = Opens(conn, prefs), S.dump

    def live_saves_meanwhile(root):
        os.utime(path, (1040.0, 1040.0))
        journal(prefs, 900.0, 1040.004)
        return dump(root)
    monkeypatch.setattr(S, "dump", live_saves_meanwhile)
    r = S.patch(conn, [{"target": "PAD / send A", "points": [[1, "-6 dB"], [2, "-12 dB"]]}], prefs=prefs, opener=opens)
    assert opens.paths == [] and "saved the set while the lanes were being written" in r["next"]
    assert S.load(path).find(".//AutomationEnvelope") is None


@pytest.mark.parametrize("target, why", [("PAD / volume", "Utility's Output"), ("PAD / Utility / Mute", "switch"),
                                         ("PAD / Utility / Width", "no parameter"), ("NOPE / send A", "no track")])
def test_what_it_refuses(live_set, target, why):
    path, prefs, conn = live_set()
    with pytest.raises(S.Refused, match=why):
        S.patch(conn, [{"target": target, "points": [[1, "On"], [2, "Off"]]}], prefs=prefs)


def test_only_reads_reach_live(live_set):
    path, prefs, conn = live_set()
    S.patch(conn, [{"target": "PAD / Utility / Output", "points": [[1, "-inf dB"], [3, "0 dB"]]}], prefs=prefs,
            opener=Opens(conn, prefs))
    S.lanes(conn, prefs=prefs)
    ops = [o for name, p in conn.sent for o in p["ops"]]
    assert {name for name, _ in conn.sent} == {"lom_batch"}
    assert all(o["op"] == "get" or o["path"].endswith(".str_for_value") for o in ops)
    with pytest.raises(ValueError, match="reads only"):
        S.Live(conn).batch([{"op": "set", "path": "tempo", "value": 120}])


def lp(name, lo, hi, ends, now=None, quantized=False, items=()):
    return {"name": name, "min": lo, "max": hi, "quantized": quantized, "items": list(items), "ends": ends, "now": now}


def xp(tag, manual, lo=None, hi=None):
    return {"manual": manual, "lo": lo, "hi": hi, "words": S._tokens(tag), "tag": tag}


UTILITY_LIVE = [lp("Device On", 0, 1, ("Off", "On"), (1.0, "On"), True, ["Off", "On"]),
                lp("Balance", -1, 1, ("50L", "50R"), (0.0, "C")),
                lp("Output", -1, 1, ("-inf dB", "35.0 dB"), (0.0, "0.00 dB")),
                lp("Mute", 0, 1, ("Off", "On"), (0.0, "Off"), True, ["Off", "On"])]
UTILITY_XML = [xp("On", "true"), xp("Balance", "0", -1, 1), xp("Gain", "1", 0, 56.2341309),
               xp("LegacyGain", "0", -35, 35), xp("Mute", "false")]


def test_alignment_pairs_live_parameters_past_controls_live_hides():
    pairs = S.align(UTILITY_LIVE, UTILITY_XML)
    assert {i: j for i, (j, _) in pairs.items()} == {0: 0, 1: 1, 2: 2, 3: 4}  # Output is Gain; LegacyGain is skipped
    assert pairs[2][1].kind == "gain"


def test_a_device_whose_xml_runs_in_another_order_is_not_trusted():
    shuffled = [UTILITY_XML[i] for i in (4, 3, 2, 1, 0)]
    assert S.align(UTILITY_LIVE, shuffled) == {}


@pytest.mark.parametrize("live, xml, kind", [
    (lp("Feedback", 0, 1, ("0.0 %", "95 %"), (0.4158, "40 %")), xp("Feedback", "0.395", 0, 0.95), "scale"),  # shown rounding
    (lp("Attack", 0, 1, ("0.00 ms", "100 ms"), (0.2, "1.00 ms")), xp("Attack", "0.001", 0, 0.1), "scale"),   # ms -> s
    (lp("Reflect", 0, 1, ("-30 dB", "6.0 dB"), (0.4, "-1.7 dB")), xp("MixReflect", "0.8255", 0.03, 1.9953), "gain"),
    (lp("Ratio", 0, 1, ("1.00 : 1", "inf : 1"), (0.67, "3.00 : 1")), xp("Ratio", "2.995", 1, 3.4e38), "scale"),
    (lp("Fe Attack", 0, 1, ("0.00 ms", "20.0 s"), (0.0, "0.00 ms")), xp("AttackLevel", "0", 0, 1), None),  # never raw
])
def test_units_are_found_from_the_ends_of_the_range_and_the_current_value(live, xml, kind):
    rule = S.fit(live, xml)
    assert (rule.kind if rule else None) == kind
