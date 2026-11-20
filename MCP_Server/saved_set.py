"""Arrangement automation through the saved set: every lane read without playing, lanes written in an instant.

Live's API can neither read nor write arrangement automation: clip envelopes exist on arrangement clips, but
Live refuses to create them there ("Not a session clip or parameter belongs to another track"), and Push's own
script will not edit them either (pushbase AutomationComponent: no envelopes on an arrangement clip). The saved
set is gzipped XML in which every automatable control carries an AutomationTarget id, and an arrangement lane is
an AutomationEnvelope of FloatEvents (time in beats, value in the control's own unit) on its track. So lanes are
read from the .als, and written into it, and Live shows written lanes once it opens the set again.

Two things make that exact and safe:

Units. The XML keeps a control in its own unit (Hz, linear gain, seconds, a fraction, or Live's raw value), the
API in a raw value, the screen in a third form. A control's XML unit is found, never assumed: Live's shown value
at both ends of the control's range (str_for_value, a read) must land on the XML's MidiControllerRange, and its
current value on the XML's when nothing automates it. The same match pairs each of Live's parameters with its XML
element, in order, so "PAD / Utility / Gain" finds StereoGain.Gain without a table of names.

Freshness. Live writes its undo history to disk (Preferences/Ableton/Live x/Undo/*.band) on every change and on
every save. A set file older than that journal has changes in Live that the file lacks, and patching it and
reopening would lose them. So a patch needs the set saved after the last change, keeps the original in the
project's Backup folder, checks the XML it wrote before it replaces the set, and reopening is its own step that
checks again.

Only reads reach Live from here (get, and str_for_value); a test holds that.
"""

from __future__ import annotations

import gzip
import importlib.util
import math
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_shown_values", Path(__file__).resolve().parent.parent / "AbletonMCP_Remote_Script" / "handlers" / "params.py")
_params = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_params)
display_number = _params.display_number  # "4.5 kHz" -> 4500, "1.80 s" -> 1800, "-inf dB" -> -1e9

PREFS = Path.home() / "Library" / "Preferences" / "Ableton"
BEFORE_START = -63072000.0  # the time of the value an envelope holds before the song starts
TRACK_TAGS = ("AudioTrack", "MidiTrack", "GroupTrack")
STEP = 0.5  # beats between the points of a curve (eased, or straight in a unit the XML does not keep)
SAVED_AFTER = 1.0  # seconds: Live writes the journal a few ms after the set when it saves


class Refused(Exception):
    """Nothing was written: the reason, in words for the person at Live."""


# --- reading Live ---------------------------------------------------------------------------------

class Live:
    """The only way this module talks to Live: lom_batch of gets and str_for_value calls. Anything else is refused
    before it is sent."""

    def __init__(self, conn):
        self.conn = conn

    def batch(self, ops: list[dict]) -> list:
        for o in ops:
            if not (o["op"] == "get" or (o["op"] == "call" and o["path"].endswith(".str_for_value"))):
                raise ValueError(f"saved_set reads only; refused {o['op']} {o['path']}")
        out = []
        for k in range(0, len(ops), 500):
            res = self.conn.send_command("lom_batch", {"ops": ops[k:k + 500], "continue_on_error": True})["results"]
            for r in res:
                if not r.get("ok"):
                    raise Refused(f"Live: {r.get('error')} ({r.get('op', {}).get('path')})")
                out.append(r["result"].get("value", r["result"].get("result")))
        return out

    def get(self, *paths: str) -> list:
        return self.batch([{"op": "get", "path": p} for p in paths])

    def shown(self, pairs: list[tuple[str, float]]) -> list[str]:
        """What Live shows for raw values: [(parameter path, raw)] -> texts."""
        return [str(s) for s in self.batch([{"op": "call", "path": f"{p}.str_for_value", "args": [float(v)]}
                                            for p, v in pairs])]


# --- the file ---------------------------------------------------------------------------------------

def load(path: Path) -> ET.Element:
    with gzip.open(path, "rb") as fh:
        return ET.fromstring(fh.read())


def dump(root: ET.Element) -> bytes:
    """The set as Live writes it: its declaration and final newline. Untouched, it differs from Live's own file only
    where Live quotes an attribute with ' (ViewData), which this writes as &quot; (measured on a 60 MB set)."""
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    return gzip.compress(xml.encode("utf-8"), compresslevel=6)


def journal_time(prefs: Path | None = None) -> float | None:
    """When Live last wrote its undo history: the newest Undo/*.band of any Live version."""
    times = [p.stat().st_mtime for p in (prefs or PREFS).glob("Live */Undo/*.band")]
    return max(times) if times else None


def state(path: Path, prefs: Path | None = None) -> str:
    """'unsaved': Live changed the set after the file was written. 'saved': the file is Live's own save of what it
    holds. 'patched': the file was written after Live's last change, by a patch, and waits for a reopen."""
    j = journal_time(prefs)
    if j is None:
        raise Refused(f"no undo journal under {prefs or PREFS}, so there is no telling whether Live has unsaved "
                      "changes; nothing written")
    f = path.stat().st_mtime
    if j > f + SAVED_AFTER:
        return "unsaved"
    return "saved" if f - j <= SAVED_AFTER else "patched"


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


# --- names --------------------------------------------------------------------------------------------

def _pick(names: list[str], key: str, what: str) -> int:
    """Index of key among names: exact, then ignoring case; "Name#2" is the second of two."""
    base, _, nth = key.strip().rpartition("#") if re.search(r"#\d+$", key.strip()) else (key.strip(), "", "1")
    for same in (lambda n: n == base, lambda n: n.lower() == base.lower()):
        hits = [i for i, n in enumerate(names) if same(n)]
        if len(hits) >= int(nth):
            return hits[int(nth) - 1]
    raise Refused(f"no {what} {key!r}; there are {', '.join(sorted(set(names)))}")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", text)
    return {w.lower() for w in words}


def _el(root: ET.Element, tag: str) -> ET.Element:
    found = root.find(tag)
    if found is None:
        raise Refused(f"the saved set has no {tag}")
    return found


# --- units --------------------------------------------------------------------------------------------

class Rule:
    """How one control's XML value relates to what Live shows: 'gain' (dB as linear gain), 'scale' (the shown
    number times k: Hz, ms -> s, % -> fraction), 'raw' (Live's raw value), 'bool' (true/false), 'xml' (a control
    no Live parameter was paired with: read in the XML's own unit, never written)."""

    def __init__(self, kind: str, k: float = 1.0, unit: str = "", lo: float = 0.0, hi: float = 1.0):
        self.kind, self.k, self.unit, self.lo, self.hi = kind, k, unit, lo, hi

    def to_xml(self, shown: str, raw: float):
        if self.kind == "bool":
            return "true" if raw >= 0.5 else "false"
        if self.kind == "raw":
            return min(self.hi, max(self.lo, raw))
        n = display_number(shown)
        if self.kind == "gain":
            return self.lo if n <= -1e9 else min(self.hi, max(self.lo, 10 ** (n / 20.0)))
        return min(self.hi, max(self.lo, n * self.k))  # "20 kHz" is the top of a range that ends at 19999.996

    def number(self, xml) -> float:
        """The shown number for an XML value (dB, Hz, ms, %), for the rules that keep one."""
        v = float(xml)
        if self.kind == "gain":
            return -math.inf if v <= 3.163e-4 else 20 * math.log10(v)  # Live keeps -inf as 0 or -70 dB
        return v / self.k

    def text(self, xml) -> str:
        if self.kind == "xml":
            return xml if xml in ("true", "false") else "%g" % float(xml)
        n = self.number(xml)
        if self.unit == "dB":
            return "-inf dB" if n == -math.inf else "%.1f dB" % (0.0 if abs(n) < 0.05 else n)
        if self.unit == "Hz":
            return "%.2f kHz" % (n / 1000) if n >= 1000 else "%.0f Hz" % n
        if self.unit == "ms":
            return "%.2f s" % (n / 1000) if n >= 1000 else "%.1f ms" % n
        return ("%.1f %s" % (n, self.unit)).strip()

    def axis(self, xml) -> float:
        """Where a value sits for simplifying a lane: dB, semitones, or the share of the range."""
        if self.kind == "bool" or str(xml) in ("true", "false"):
            return 1.0 if str(xml) == "true" else 0.0
        if self.kind == "xml":
            return float(xml)
        if self.kind == "gain":
            return max(-70.0, self.number(xml))
        if self.unit == "Hz":
            return 12 * math.log2(max(1e-3, float(xml)))
        return 100 * (float(xml) - self.lo) / ((self.hi - self.lo) or 1.0)


def _unit(text: str) -> str:
    t = text.strip()
    if t.lower().endswith("db"):
        return "dB"
    if re.search(r"hz$", t, re.I):
        return "Hz"
    if re.search(r"\d\s*m?s$", t):
        return "ms"
    if t.endswith("%"):
        return "%"
    return ""


_NUM = re.compile(r"-?\d+(?:\.(\d+))?")


def _same(rule: Rule, raw: float, shown: str, xml: float) -> bool:
    """Whether the XML value xml is what Live shows as shown (raw for the raw rule), to the shown text's own
    precision: "40 %" covers 39.5-40.5 %, "-30 dB" -30.5 to -29.5 dB."""
    if rule.kind == "raw":
        return abs(raw - xml) <= 1e-4 * max(1.0, abs(rule.hi - rule.lo))
    n = display_number(shown)
    if n <= -1e9:
        return xml <= max(rule.lo, 3.163e-4) if rule.kind == "gain" else xml <= -1e30
    if n >= 1e9:
        return xml >= 1e30
    if rule.kind == "gain" and xml <= 0:
        return False
    m = _NUM.search(shown)
    step = 10.0 ** -len(m.group(1) or "") * abs(display_number(_NUM.sub("1", shown, count=1)) or 1.0) if m else 1.0
    return abs(rule.number(xml) - n) <= max(0.6 * step, 0.005 * abs(n), 1e-6)


def fit(lp: dict, xp: dict) -> Rule | None:
    """The rule under which Live's parameter lp and the XML element xp are one control, or None.

    lp: {min, max, quantized, items, ends: (shown at min, shown at max), now: (raw, shown) or None};
    xp: {manual, lo, hi} (lo and hi None when the XML keeps no range, as for a switch)."""
    if xp["manual"] in ("true", "false"):
        if not (lp["quantized"] and len(lp["items"]) == 2):
            return None
        rule = Rule("bool")
        return rule if lp["now"] is None or rule.to_xml("", lp["now"][0]) == xp["manual"] else None
    if xp["lo"] is None:
        return None
    witnesses = [(lp["min"], lp["ends"][0], xp["lo"]), (lp["max"], lp["ends"][1], xp["hi"])]
    if lp["now"] is not None:
        witnesses.append((lp["now"][0], lp["now"][1], float(xp["manual"])))
    unit = _unit(lp["ends"][1]) or _unit(lp["ends"][0])
    candidates = []
    if not lp["quantized"] and all(display_number(t) is not None for _, t, _ in witnesses):
        if unit == "dB":
            candidates.append(Rule("gain", unit="dB", lo=xp["lo"], hi=xp["hi"]))
        candidates += [Rule("scale", k, unit, xp["lo"], xp["hi"]) for k in (1.0, 0.001, 0.01)
                       if k == 1.0 or (k == 0.001 and unit == "ms") or (k == 0.01 and unit == "%")]
    if lp["quantized"] or not unit:  # a control shown in dB, Hz, s or % keeps that unit in the XML, never raw
        candidates.append(Rule("raw", unit="", lo=xp["lo"], hi=xp["hi"]))
    for rule in candidates:
        if all(_same(rule, r, t, x) for r, t, x in witnesses):
            return rule
    return None


def align(lom: list[dict], xml: list[dict]) -> dict[int, tuple[int, Rule]]:
    """Pair Live's parameters with the device's XML elements, both in order. The XML keeps controls Live hides
    (Utility's LegacyGain, EQ Eight's band switches), so this is an alignment: a pair must fit() and scores 1, plus
    1 when their names share a word; an XML element may go unpaired for free, a Live parameter at a cost."""
    n, m = len(lom), len(xml)
    rules = {}
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], move[i][0] = score[i - 1][0] - 0.5, "lom"
    for j in range(1, m + 1):
        move[0][j] = "xml"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = score[i][j - 1], "xml"
            if score[i - 1][j] - 0.5 > best:
                best, how = score[i - 1][j] - 0.5, "lom"
            rule = fit(lom[i - 1], xml[j - 1])
            if rule is not None:
                gain = 1.0 + (1.0 if _tokens(lom[i - 1]["name"]) & xml[j - 1]["words"] else 0.0)
                if score[i - 1][j - 1] + gain > best:
                    best, how = score[i - 1][j - 1] + gain, "pair"
                    rules[(i - 1, j - 1)] = rule
            score[i][j], move[i][j] = best, how
    pairs, i, j = {}, n, m
    while i > 0 or j > 0:
        how = move[i][j]
        if how == "pair":
            pairs[i - 1] = (j - 1, rules[(i - 1, j - 1)])
            i, j = i - 1, j - 1
        elif how == "lom":
            i -= 1
        else:
            j -= 1
    named = {i for i, (j, _) in pairs.items() if _tokens(lom[i]["name"]) & xml[j]["words"]}
    # A pair is trusted when the names share a word, or when it sits between two such pairs (Utility's Output
    # between Balance and Mute is its Gain). A range fit alone is not enough: Operator and Simpler keep their XML
    # in another order than Live lists the parameters, and a unitless 0..1 fits too many places.
    trusted = {i: pr for i, pr in pairs.items()
               if i in named or (i - 1 in named and i + 1 in named and pairs[i - 1][0] < pr[0] < pairs[i + 1][0])}
    # A device whose parameters mostly fail to pair keeps its XML in an order of its own; trust nothing there.
    return trusted if len(trusted) >= 0.8 * n else {}


def xml_params(device: ET.Element) -> list[tuple[ET.Element, dict]]:
    """A device's automatable XML controls in document order, without the devices of its chains."""
    out = []

    def walk(el: ET.Element, path: str):
        for child in el:
            if child.tag in ("Branches", "DeviceChain", "Devices", "ReturnBranches"):
                continue
            target, manual = child.find("AutomationTarget"), child.find("Manual")
            if target is not None and manual is not None:
                rng = child.find("MidiControllerRange")
                name = child.tag
                if child.tag == "ParameterValue":  # a plugin's parameter carries its own name beside it
                    name = (el.findtext("ParameterName") or el.find("ParameterName").get("Value", "")
                            if el.find("ParameterName") is not None else child.tag)
                out.append((child, {"manual": manual.get("Value"), "words": _tokens(f"{path} {name}"),
                                    "lo": float(rng.find("Min").get("Value")) if rng is not None else None,
                                    "hi": float(rng.find("Max").get("Value")) if rng is not None else None,
                                    "tag": child.tag}))
            walk(child, f"{path} {child.tag}")

    walk(device, "")
    return out


# --- the set as Live and the file see it -------------------------------------------------------------

class Control:
    """One automatable control, known to Live and found in the XML: where its lanes live and its unit."""

    def __init__(self, target: str, path: str, track_el: ET.Element, el: ET.Element, rule: Rule, lp: dict):
        self.target, self.path, self.track_el, self.el, self.rule, self.lp = target, path, track_el, el, rule, lp
        self.pointee = el.find("AutomationTarget").get("Id")


class SetView:
    """The open set (read from Live) beside its saved file (the XML), track for track."""

    def __init__(self, live: Live, root: ET.Element, strict: bool = True):
        """strict (for writing): the file's tracks must be Live's, in order. Otherwise (for reading) tracks pair by
        name, and a track one side lacks is left out."""
        self.live, self.root = live, root
        liveset = _el(root, "LiveSet")
        names, returns, num, den = live.get("tracks.name", "return_tracks.name", "signature_numerator",
                                            "signature_denominator")
        self.beats_per_bar = float(num) * 4.0 / float(den)
        xml_tracks = [t for t in _el(liveset, "Tracks") if t.tag in TRACK_TAGS]
        xml_returns = [t for t in _el(liveset, "Tracks") if t.tag == "ReturnTrack"]
        def pair(live_names, xml_els, prefix):
            xml_names = [t.find("Name/EffectiveName").get("Value") for t in xml_els]
            if strict and xml_names != list(live_names):
                raise Refused("the saved set's tracks differ from Live's; save the set in Live first")
            out, used = [], set()
            for i, n in enumerate(live_names):
                k = next((k for k, x in enumerate(xml_names) if x == n and k not in used), None)
                if k is not None:
                    used.add(k)
                    out.append((n, f"{prefix}.{i}", xml_els[k]))
            return out

        self.tracks = pair(names, xml_tracks, "tracks")
        self.returns = pair(returns, xml_returns, "return_tracks")
        main = liveset.find("MainTrack") if liveset.find("MainTrack") is not None else liveset.find("MasterTrack")
        self.main = ("Master", "master_track", main)
        self.return_names = list(returns)
        self._devices: dict[str, tuple[list[dict], list[tuple[ET.Element, dict]], dict]] = {}

    def bar(self, beat: float) -> float:
        return beat / self.beats_per_bar + 1

    def beat(self, bar: float) -> float:
        return (float(bar) - 1) * self.beats_per_bar

    def all_tracks(self):
        return self.tracks + self.returns + ([self.main] if self.main[2] is not None else [])

    def track(self, name: str):
        if name.strip().lower() in ("master", "main"):
            return self.main
        everything = self.tracks + self.returns
        return everything[_pick([n for n, _, _ in everything], name, "track")]

    def _live_params(self, path: str) -> list[dict]:
        rows = self.live.get(f"{path}.parameters")[0]
        states = self.live.batch([{"op": "get", "path": f"{path}.parameters", "fields": ["automation_state"]}])[0]
        ends = self.live.shown([(f"{path}.parameters.{k}", v) for k, r in enumerate(rows) for v in (r["min"], r["max"])])
        out = []
        for k, (r, s) in enumerate(zip(rows, states)):
            still = not s.get("automation_state")  # an automated knob's saved Manual is stale
            out.append({"name": r["name"], "min": r["min"], "max": r["max"], "quantized": r["quantized"],
                        "items": r.get("items", []), "ends": (ends[2 * k], ends[2 * k + 1]),
                        "now": (r["value"], r["display"]) if still else None})
        return out

    def device(self, track_path: str, track_el: ET.Element, index: int):
        """Live's parameters, the XML controls and their pairing, for one device on a track."""
        key = f"{track_path}.devices.{index}"
        if key not in self._devices:
            xml_dev = list(_el(track_el, "DeviceChain/DeviceChain/Devices"))[index]
            xml = xml_params(xml_dev)
            lom = self._live_params(key)
            self._devices[key] = (lom, xml, align(lom, [x for _, x in xml]))
        return self._devices[key]

    def devices(self, track_path: str, track_el: ET.Element) -> list[str]:
        names, classes = self.live.get(f"{track_path}.devices.name", f"{track_path}.devices.class_name")
        xml = list(_el(track_el, "DeviceChain/DeviceChain/Devices"))
        if [d.tag for d in xml] != list(classes):
            raise Refused(f"the saved set's devices on {track_el.find('Name/EffectiveName').get('Value')} differ "
                          f"from Live's ({', '.join(classes)}); save the set in Live first")
        return list(names)

    def mixer(self, track_path: str, track_el: ET.Element, what: str) -> tuple[str, ET.Element]:
        mixer = _el(track_el, "DeviceChain/Mixer")
        w = what.strip().lower()
        if w == "volume":
            return f"{track_path}.mixer_device.volume", _el(mixer, "Volume")
        if w in ("pan", "panning"):
            return f"{track_path}.mixer_device.panning", _el(mixer, "Pan")
        if w.startswith("send "):
            key = what.strip()[5:].strip()
            if len(key) == 1 and key.isalpha():
                k = ord(key.upper()) - 65
            elif key.lower() in (n.lower() for n in self.return_names):
                k = _pick(self.return_names, key, "return")
            else:  # "A-Reverb": the letter names it
                k = ord(key[0].upper()) - 65
            holders = mixer.findall("Sends/TrackSendHolder")
            if not 0 <= k < len(holders):
                raise Refused(f"no send {key!r} on this track")
            return f"{track_path}.mixer_device.sends.{k}", _el(holders[k], "Send")
        raise Refused(f"{what!r} is not volume, pan or a send")

    def control(self, target: str) -> Control:
        """A target as the live_* tools name it ("PAD / Utility / Gain", "PAD / send A", "Master / Utility / Gain")."""
        parts = [p.strip() for p in target.split("/")]
        name, track_path, track_el = self.track(parts[0])
        if len(parts) == 2:
            path, el = self.mixer(track_path, track_el, parts[1])
            r = self.live.get(path)[0]
            ends = self.live.shown([(path, r["min"]), (path, r["max"])])
            lp = {"name": parts[1], "min": r["min"], "max": r["max"], "quantized": False, "items": [],
                  "ends": tuple(ends), "now": None}
            rng = el.find("MidiControllerRange")
            xp = {"manual": el.find("Manual").get("Value"), "lo": float(rng.find("Min").get("Value")),
                  "hi": float(rng.find("Max").get("Value"))}
            rule = fit(lp, xp)
            if rule is None:
                raise Refused(f"{target}: the saved set's unit for it is not one this knows")
            return Control(f"{name} / {parts[1]}", path, track_el, el, rule, lp)
        if len(parts) != 3:
            raise Refused(f"{target!r}: a target is 'Track / Device / Param' or 'Track / volume|pan|send X' "
                          "(devices inside racks are not reachable in the saved set yet)")
        devices = self.devices(track_path, track_el)
        d = _pick(devices, parts[1], f"device on {name}")
        lom, xml, pairs = self.device(track_path, track_el, d)
        try:
            p = _pick([x["name"] for x in lom], parts[2], f"parameter on {devices[d]}")
        except Refused:  # the XML's name for it: Live 12.4 shows Utility's Gain as "Output"
            p = next((i for i, (j, _) in pairs.items() if xml[j][1]["tag"].lower() == parts[2].lower()), None)
            if p is None:
                raise
        if p not in pairs:
            raise Refused(f"{target}: no control in the saved set matches Live's {lom[p]['name']} by its range")
        el, rule = xml[pairs[p][0]][0], pairs[p][1]
        return Control(f"{name} / {devices[d]} / {lom[p]['name']}", f"{track_path}.devices.{d}.parameters.{p}",
                       track_el, el, rule, lom[p])

    def control_of(self, track_path: str, track_el: ET.Element, pointee: str) -> Control | None:
        """The control a lane points at, named as Live names it; None for one Live does not show."""
        name = track_el.find("Name/EffectiveName").get("Value") if track_el.tag != "MainTrack" else "Master"
        mixer = track_el.find("DeviceChain/Mixer")
        settings = [("volume", mixer.find("Volume")), ("pan", mixer.find("Pan"))] if mixer is not None else []
        for k, h in enumerate(mixer.findall("Sends/TrackSendHolder") if mixer is not None else []):
            settings.append((f"send {chr(65 + k)}", h.find("Send")))
        for what, el in settings:
            target = el.find("AutomationTarget") if el is not None else None
            if target is not None and target.get("Id") == pointee:
                return self.control(f"{name} / {what}")
        devices = self.devices(track_path, track_el)
        for d, xml_dev in enumerate(_el(track_el, "DeviceChain/DeviceChain/Devices")):
            ids = [x.find("AutomationTarget").get("Id") for x, _ in xml_params(xml_dev)]
            if pointee not in ids:
                continue
            lom, xml, pairs = self.device(track_path, track_el, d)
            k = ids.index(pointee)
            for p, (j, rule) in pairs.items():
                if j == k:
                    return Control(f"{name} / {devices[d]} / {lom[p]['name']}",
                                   f"{track_path}.devices.{d}.parameters.{p}", track_el, xml[j][0], rule, lom[p])
            # not paired with one of Live's parameters: shown by its XML name, in the XML's own unit
            return Control(f"{name} / {devices[d]} / [{xml[k][1]['tag']}]", "", track_el, xml[k][0], Rule("xml"), {})
        return None

# --- values along a lane ------------------------------------------------------------------------------

def _raws(view: SetView, c: Control, shown: list[str]) -> list[float]:
    """Live's raw value for each shown value, from what str_for_value (a read) shows across the range: 257 points,
    then 33 inside the bracket of each value, so two round trips whatever the count (40 bisection rounds took
    11 s while Live played)."""
    lp = c.lp
    if lp["quantized"]:
        items = [i.lower() for i in lp["items"]]
        return [float(lp["min"]) + items.index(s.strip().lower()) for s in shown]
    want = [display_number(s) for s in shown]
    if any(w is None for w in want):
        raise Refused(f"{c.target}: {shown} must be values Live shows, e.g. {lp['ends'][1]!r}")
    brackets = [(float(lp["min"]), float(lp["max"]))] * len(want)
    for n in (256, 32):
        grids = [[a + (b - a) * k / n for k in range(n + 1)] for a, b in brackets]
        texts = iter(view.live.shown([(c.path, x) for g in grids for x in g]))
        found = []
        for w, g in zip(want, grids):
            ys = [display_number(next(texts)) for _ in g]
            rising = ys[-1] >= ys[0]
            between = (lambda k: ys[k] <= w <= ys[k + 1]) if rising else (lambda k: ys[k] >= w >= ys[k + 1])
            k = next((k for k in range(n) if between(k)), 0 if (w <= ys[0]) == rising else n - 1)
            found.append((g[k], g[k + 1], ys[k], ys[k + 1]))
        brackets = [(x0, x1) for x0, x1, _, _ in found]
    out = []
    for w, (x0, x1, y0, y1) in zip(want, found):
        if w <= -1e9 or w >= 1e9:  # an end of the range ("-inf dB") is that end exactly
            out.append(x0 if (y0 == w) else x1)
        else:
            out.append(x0 if y1 == y0 else x0 + (x1 - x0) * (w - y0) / (y1 - y0))
    return out


def curve(view: SetView, c: Control, points: list, shape: str | None) -> list[tuple[float, object]]:
    """[(beat, XML value)] for a lane's points: exact at each point, and every STEP beats in between when the
    lane is eased or the XML's unit is not the one the line is straight in (a straight dB fade in linear gain)."""
    pts = sorted(((view.beat(b), v) for b, v in points), key=lambda p: p[0])
    texts = [v if isinstance(v, str) else None for _, v in pts]
    numbers = [v for _, v in pts if not isinstance(v, str)]
    if numbers:  # a bare number is Live's raw value
        shown_numbers = iter(view.live.shown([(c.path, float(v)) for v in numbers]))
        texts = [t if t is not None else next(shown_numbers) for t in texts]
    raws = [float(v) if not isinstance(v, str) else None for _, v in pts]
    infinite = [abs(display_number(t) or 0) >= 1e9 for t in texts]
    # raw values where the line runs in Live's raw value: every point of a raw control, and both ends of a fade
    # from or to -inf (it has no number to ease in, so the knob's position eases, as live_automate does)
    need = [k for k, r in enumerate(raws) if r is None and (c.rule.kind in ("raw", "bool") or infinite[k]
                                                           or (k and infinite[k - 1]) or (k + 1 < len(texts) and infinite[k + 1]))]
    for k, r in zip(need, _raws(view, c, [texts[k] for k in need])):
        raws[k] = r
    out, samples = [], []
    for k, ((t0, _), (t1, _)) in enumerate(zip(pts, pts[1:])):
        out.append((t0, k, None))
        straight = shape not in ("ease", "ease-log") and c.rule.kind in ("scale", "raw", "bool")
        if straight or t1 - t0 <= STEP:
            continue
        steps = int(round((t1 - t0) / STEP))
        for s in range(1, steps):
            x = s / steps
            e = x * x * (3 - 2 * x) if shape in ("ease", "ease-log") else x
            n0, n1 = (display_number(texts[k]), display_number(texts[k + 1])) if c.rule.kind != "raw" else (None, None)
            if c.rule.kind in ("raw", "bool") or abs(n0) >= 1e9 or abs(n1) >= 1e9:
                samples.append((t0 + (t1 - t0) * x, "raw", raws[k] + (raws[k + 1] - raws[k]) * e))
            elif shape == "ease-log" and n0 > 0 and n1 > 0:
                samples.append((t0 + (t1 - t0) * x, "number", n0 * math.exp(math.log(n1 / n0) * e)))
            else:
                samples.append((t0 + (t1 - t0) * x, "number", n0 + (n1 - n0) * e))
            out.append((t0 + (t1 - t0) * x, None, len(samples) - 1))
    out.append((pts[-1][0], len(pts) - 1, None))
    raw_texts = iter(view.live.shown([(c.path, v) for _, kind, v in samples if kind == "raw"])
                     if c.rule.kind not in ("raw", "bool") else [])
    values = []
    for t, k, s in out:
        if k is not None:
            values.append((t, c.rule.to_xml(texts[k], raws[k] if raws[k] is not None else 0.0)))
            continue
        _, kind, v = samples[s]
        if c.rule.kind in ("raw", "bool"):
            values.append((t, c.rule.to_xml("", v)))
        elif kind == "raw":
            values.append((t, c.rule.to_xml(next(raw_texts), v)))
        else:
            values.append((t, c.rule.to_xml(f"{v} {c.rule.unit}", 0.0)))
    return values


def _value_at(events: list[tuple[float, object]], t: float):
    """An envelope's value at beat t (after any jump there), the way Live plays it: straight between points."""
    before = [e for e in events if e[0] <= t]
    after = [e for e in events if e[0] > t]
    if not before:
        return events[0][1]
    t0, v0 = before[-1]
    if not after or str(v0) in ("true", "false"):
        return v0
    t1, v1 = after[0]
    return float(v0) + (float(v1) - float(v0)) * (t - t0) / (t1 - t0)


def _fmt(v) -> str:
    return v if isinstance(v, str) else repr(float(v))


def write_lane(view: SetView, c: Control, values: list[tuple[float, object]], clear_all: bool = False) -> str:
    """Put values into the control's lane. Outside the values' span the lane keeps what it had: an existing lane
    goes on as before (a jump back at each edge, as when Live records over part of a lane); a new lane holds the
    knob's value before and the last value after, as a recording leaves it."""
    envs = c.track_el.find("AutomationEnvelopes/Envelopes")
    if envs is None:
        raise Refused(f"{c.target}: the track has no automation envelopes in the saved set")
    old = next((e for e in envs if e.find("EnvelopeTarget/PointeeId").get("Value") == c.pointee), None)
    if clear_all:
        if old is not None:
            envs.remove(old)
        return "removed" if old is not None else "had no lane"
    a, b = values[0][0], values[-1][0]
    if old is None:
        events = [(BEFORE_START, c.el.find("Manual").get("Value"))] + values
        used = [int(e.get("Id")) for e in envs if (e.get("Id") or "").isdigit()]
        env = ET.SubElement(envs, "AutomationEnvelope", Id=str(max(used, default=-1) + 1))
        ET.SubElement(ET.SubElement(env, "EnvelopeTarget"), "PointeeId", Value=c.pointee)
        auto = ET.SubElement(env, "Automation")
        ET.SubElement(auto, "Events")
        view_state = ET.SubElement(auto, "AutomationTransformViewState")
        ET.SubElement(view_state, "IsTransformPending", Value="false")
        ET.SubElement(view_state, "TimeAndValueTransforms")
        how = "new lane"
    else:
        env = old
        evs = [(float(e.get("Time")), e.get("Value")) for e in env.find("Automation/Events")]
        evs = [(t, v if v in ("true", "false") else float(v)) for t, v in evs]
        before = [e for e in evs if e[0] < a]
        after = [e for e in evs if e[0] > b]
        events = before + [(a, _value_at(evs, a))] + values + [(b, _value_at(evs, b))] + after
        how = "spliced into the lane"
    holder = env.find("Automation/Events")
    for e in list(holder):
        holder.remove(e)
    for i, (t, v) in enumerate(events):
        ET.SubElement(holder, "FloatEvent", Id=str(i), Time=repr(round(float(t), 6)) if t != BEFORE_START
                      else "-63072000", Value=_fmt(v))
    return how


def validate(data: bytes, controls: list[Control]) -> None:
    """Read back what is about to replace the set: it parses, every lane points at a control of its track, each
    track's lane ids are unique, times rise, and every value is in its control's range."""
    root = ET.fromstring(gzip.decompress(data))
    by_id = {}
    for track in root.iter():
        if track.tag not in TRACK_TAGS + ("ReturnTrack", "MainTrack", "MasterTrack"):
            continue
        targets = {at.get("Id") for at in track.iter("AutomationTarget")}
        envs = track.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope")
        ids = [e.get("Id") for e in envs]
        if len(ids) != len(set(ids)):
            raise Refused(f"validation: two lanes share an id on {track.findtext('Name/EffectiveName')}")
        for env in envs:
            pointee = env.find("EnvelopeTarget/PointeeId").get("Value")
            by_id[pointee] = env
            if pointee not in targets:
                raise Refused(f"validation: a lane points at {pointee}, which is not on its track")
    for c in controls:
        env = by_id.get(c.pointee)
        if env is None:
            continue  # removed on purpose
        evs = list(env.find("Automation/Events"))
        times = [float(e.get("Time")) for e in evs]
        if times != sorted(times):
            raise Refused(f"validation: {c.target}'s points are out of order")
        for e in evs:
            v = e.get("Value")
            if c.rule.kind == "bool":
                if v not in ("true", "false"):
                    raise Refused(f"validation: {c.target} holds {v}, not true or false")
            elif not c.rule.lo - 1e-9 <= float(v) <= c.rule.hi + 1e-9:
                raise Refused(f"validation: {c.target} holds {v}, outside {c.rule.lo}..{c.rule.hi}")


# --- the three jobs -----------------------------------------------------------------------------------

def _lane_form(lane: dict) -> str:
    if lane.get("target"):
        return lane["target"]
    param = lane["param"]
    simple = param.strip().lower() in ("volume", "pan") or param.strip().lower().startswith("send ")
    return f"{lane['track']} / {param if simple else param.replace('.', ' / ', 1)}"


def patch(conn, lanes: list[dict], prefs: Path | None = None, now: float | None = None) -> dict:
    """Write lanes into the saved set of the set open in Live, at once. Lanes as live_automate takes them:
    {"target", "points": [[bar, value]], "shape"?} or {"target", "clear": "all"}. A mixer volume is refused
    (fades go on a Utility's Gain). Live shows the lanes after reopen()."""
    live = Live(conn)
    path = Path(live.get("file_path")[0] or "")
    if not path.name.endswith(".als") or not path.exists():
        raise Refused("the set open in Live has never been saved; save it first")
    st = state(path, prefs)
    if st == "unsaved":
        raise Refused(f"Live has changes since {path.name} was saved at {_clock(path.stat().st_mtime)} (last change "
                      f"{_clock(journal_time(prefs))}); save the set in Live and run this again. Nothing written.")
    seen = path.stat().st_mtime_ns
    root = load(path)
    view = SetView(live, root)
    done, controls = [], []
    for lane in lanes:
        c = view.control(_lane_form(lane))
        if c.lp["quantized"]:
            raise Refused(f"{c.target}: switch and menu lanes are not written into the saved set (their event "
                          "type there is unproven); use live_automate")
        if c.path.endswith("mixer_device.volume") and not lane.get("clear"):
            raise Refused(f"{c.target}: fades go on a Utility's Gain (Marc's rule): add a Utility and automate "
                          f"'{c.target.rsplit(' / ', 1)[0]} / Utility / Gain'")
        if lane.get("clear"):
            if str(lane["clear"]).strip().lower() == "all":
                how = write_lane(view, c, [], clear_all=True)
                done.append({"target": c.target, "lane": how})
                controls.append(c)
                continue
            a, _, b = str(lane["clear"]).partition("-")
            value = lane.get("value")
            if value is None:
                raise Refused(f"{c.target}: clear a span with a value to hold, e.g. \"value\": \"-4 dB\"")
            pts = [[float(a), value], [float(b or a) + 1, value]]
        else:
            pts = lane.get("points") or []
            if len(pts) < 1:
                raise Refused(f"{c.target}: give points or clear")
        values = curve(view, c, pts, lane.get("shape"))
        how = write_lane(view, c, values)
        done.append({"target": c.target, "lane": how, "points": len(values),
                     "bars": "%g-%g" % (view.bar(values[0][0]), view.bar(values[-1][0]))})
        controls.append(c)
    data = dump(root)
    validate(data, controls)
    stamp = time.strftime("%Y-%m-%d %H%M%S", time.localtime(now or time.time()))
    backup = path.parent / "Backup" / "Before automate" / f"{path.stem} [{stamp}].als"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    if path.stat().st_mtime_ns != seen or state(path, prefs) == "unsaved":
        raise Refused("Live saved or changed the set while the lanes were being written; nothing replaced. Run again.")
    tmp = path.with_name(f".{path.name}.automate")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return {"set": str(path), "backup": str(backup), "lanes": done,
            "next": "Live still plays the set as it was: reopen it (live_reopen / dj produce reopen) to hear the lanes. "
                    "Saving in Live before that keeps Live's version and drops these lanes."}


def reopen(conn, confirm: bool = False, prefs: Path | None = None,
           opener=lambda p: subprocess.run(["open", "-b", "com.ableton.live", str(p)], check=True)) -> dict:
    """Open the patched set again in Live, so it plays the written lanes. Refused when Live has changes the file
    lacks (they would be lost) or when the file is Live's own save (nothing patched to load)."""
    path = Path(Live(conn).get("file_path")[0] or "")
    st = state(path, prefs)
    if st == "unsaved":
        raise Refused("Live has changes since the patch; reopening would lose them. Nothing done. Save in Live "
                      "(which keeps Live's version and drops the patched lanes) and run automate again.")
    if st == "saved":
        raise Refused(f"{path.name} is Live's own save of what it holds; nothing patched waits to be loaded.")
    if not confirm:
        return {"set": str(path), "state": st, "would": "reopen it in Live (confirm to do it)"}
    opener(path)
    return {"set": str(path), "reopened": True,
            "note": "If Live asks to save changes, choose Cancel: the set changed after the check."}


def lanes(conn, prefix: str | None = None, tolerance: float = 0.5, prefs: Path | None = None) -> dict:
    """Every arrangement lane of the saved set, as targets and [bar, shown value] breakpoints, without playing.
    prefix keeps one track's lanes ("PAD"), one device's ("PAD / Utility") or one lane ("PAD / send A"). A lane
    recorded in real time holds a point every few ms; points within tolerance (dB, semitones, or % of the range)
    of the line through their neighbours are left out."""
    live = Live(conn)
    path = Path(live.get("file_path")[0] or "")
    root = load(path)
    view = SetView(live, root, strict=False)
    want = " / ".join(p.strip() for p in (prefix or "").split("/")).lower().strip(" /")
    out, skipped = [], []
    for name, track_path, track_el in view.all_tracks():
        if want and want.split(" / ")[0] != name.lower():
            continue
        for env in track_el.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
            try:
                c = view.control_of(track_path, track_el, env.find("EnvelopeTarget/PointeeId").get("Value"))
            except Refused as e:  # its devices changed since the save
                skipped.append(f"{name}: {e}")
                break
            t = (c.target.lower() if c else "")
            if c is None or (want and t != want and not t.startswith(want + " / ")):
                continue
            evs = [(float(e.get("Time")), e.get("Value")) for e in env.find("Automation/Events")]
            before = [v for t, v in evs if t <= BEFORE_START]
            pts = simplify([(t, v) for t, v in evs if t > BEFORE_START], c.rule, tolerance)
            texts = _texts(view, c, [v for _, v in pts] + before[:1])
            out.append({"target": c.target, "before": texts[-1] if before else None,
                        "points": [[round(view.bar(t), 2), s] for (t, _), s in zip(pts, texts)]})
    st = state(path, prefs)
    return {"set": str(path), "saved": _clock(path.stat().st_mtime), "state": st,
            "note": "Live has changes since this save; these are the saved lanes." if st == "unsaved" else None,
            "lanes": out, "skipped": skipped}


def _texts(view: SetView, c: Control, values: list) -> list[str]:
    if c.rule.kind == "bool":
        return ["On" if v == "true" else "Off" for v in values]
    if c.rule.kind == "raw":
        return view.live.shown([(c.path, float(v)) for v in values])
    return [c.rule.text(v) for v in values]


def simplify(points: list[tuple[float, str]], rule: Rule, tolerance: float) -> list[tuple[float, str]]:
    """The fewest points whose straight lines stay within tolerance of the lane (on the rule's axis)."""
    if len(points) <= 2:
        return points
    ys = [rule.axis(v) for _, v in points]
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (t0, y0), (t1, y1) = (points[i][0], ys[i]), (points[j][0], ys[j])
        worst, at = 0.0, None
        for k in range(i + 1, j):
            line = y0 if t1 == t0 else y0 + (y1 - y0) * (points[k][0] - t0) / (t1 - t0)
            d = abs(ys[k] - line)
            if d > worst:
                worst, at = d, k
        if at is not None and worst > tolerance:
            keep.add(at)
            stack += [(i, at), (at, j)]
    return [points[k] for k in sorted(keep)]
