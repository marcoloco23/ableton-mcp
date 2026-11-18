"""The live_* commands: whole jobs on the open set, by name and bar, one round trip each.

Each takes (song, p, ctrl) like every dispatched command, so it also runs inside lom_batch.
Conventions (see lom.py): tracks, devices and parameters are named; bars are 1-based and
song-absolute; values are what Live shows ("-6 dB", "110 Hz", "High Pass 48dB").
"""

from __future__ import absolute_import, print_function, unicode_literals

from . import lom, params
from .browser import select_track


def _concise(p):
    fmt = p.get("response_format", "concise")
    if fmt not in ("concise", "detailed"):
        raise ValueError("response_format is 'concise' or 'detailed', not '%s'" % fmt)
    return fmt == "concise"


def _kind(t):
    if getattr(t, "is_foldable", False):
        return "group"
    return "audio" if getattr(t, "has_audio_input", False) else "midi"


def _clips(t):
    try:
        return list(t.arrangement_clips)
    except Exception:  # the master and some group tracks have none
        return []


def _flags(t):
    out = []
    for name, attr in (("muted", "mute"), ("solo", "solo"), ("armed", "arm"), ("frozen", "is_frozen")):
        try:
            if getattr(t, attr):
                out.append(name)
        except Exception:  # arm on a track that cannot be armed
            pass
    return out


def _route(t, attr):
    try:
        return lom.label(getattr(t, attr))
    except Exception:
        return None


def _sends(song, t):
    try:
        return dict((r.name, lom.shown(s)) for r, s in zip(song.return_tracks, t.mixer_device.sends))
    except Exception:
        return {}


def _clip_rows(song, t, concise):
    clips = _clips(t)
    if concise:
        return lom.spans_text(song, [(c.start_time, c.end_time) for c in clips])
    return [dict({"name": c.name, "bars": lom.bars_text(song, c.start_time, c.end_time)},
                 **({"muted": True} if getattr(c, "muted", False) else {})) for c in clips]


# --- reading -------------------------------------------------------------------------------

def overview(song, p, ctrl):
    """The whole set: sections from the locators with what plays in each, every track in one line."""
    concise = _concise(p)
    tracks = []
    for t in song.tracks:
        row = {"name": t.name, "kind": _kind(t)}
        if getattr(t, "is_grouped", False):
            row["group"] = t.group_track.name
        row["flags"] = _flags(t)
        row["volume"] = lom.shown(t.mixer_device.volume)
        row["output"] = _route(t, "output_routing_type")
        row["devices"] = [d.name for d in t.devices]
        row["clips"] = _clip_rows(song, t, concise)
        if not concise:
            row["pan"] = lom.shown(t.mixer_device.panning)
            row["sends"] = _sends(song, t)
        tracks.append(dict((k, v) for k, v in row.items() if v or not concise))
    end = max([c.end_time for t in song.tracks for c in _clips(t)] or [lom.beats_per_bar(song)])
    cues = sorted((c.time, c.name) for c in song.cue_points)
    if not cues or cues[0][0] > 0:
        cues.insert(0, (0.0, "START"))
    sections = []
    for k, (s, name) in enumerate(cues):
        e = cues[k + 1][0] if k + 1 < len(cues) else max(end, s + lom.beats_per_bar(song))
        playing = [t.name for t in song.tracks if _kind(t) != "group" and not t.mute
                   and any(c.start_time < e and c.end_time > s and not getattr(c, "muted", False) for c in _clips(t))]
        sections.append({"name": name, "bars": lom.bars_text(song, s, e), "playing": playing})
    return {
        "tempo": song.tempo,
        "signature": "%d/%d" % (song.signature_numerator, song.signature_denominator),
        "loop": dict({"on": bool(song.loop)}, bars=lom.bars_text(song, song.loop_start, song.loop_start + song.loop_length)),
        "sections": sections,
        "tracks": tracks,
        "returns": [{"name": r.name, "volume": lom.shown(r.mixer_device.volume), "devices": [d.name for d in r.devices]}
                    for r in song.return_tracks],
        "master": {"volume": lom.shown(song.master_track.mixer_device.volume),
                   "devices": [d.name for d in song.master_track.devices]},
    }


def _device(dev, concise):
    ps = list(dev.parameters)
    on = next((x for x in ps if x.name == "Device On"), None)
    out = {"name": dev.name, "type": getattr(dev, "class_name", type(dev).__name__)}
    if on is not None:
        out["on"] = bool(on.value)
    rest = [x for x in ps if x is not on]
    out["params"] = dict((x.name, lom.shown(x)) for x in rest) if concise else [lom.param_info(x) for x in rest]
    if getattr(dev, "can_have_chains", False):
        out["chains"] = [{"name": c.name, "devices": [_device(d, concise) for d in c.devices]} for c in dev.chains]
    return out


def track(song, p, ctrl):
    """One track in depth: mixer, sends, routing, every device with every parameter as shown, clips.
    p["track"] may go down to one device: 'SERUM BASS / Saturator'."""
    concise = _concise(p)
    tg = lom.target(song, p["track"])
    if tg.param is not None or tg.setting:
        raise ValueError("'%s' is a setting; live_track reads a track or a device on it" % p["track"])
    t = tg.track
    if tg.device is not None:
        return {"track": t.name, "device": _device(tg.device, concise)}
    out = {"name": t.name, "kind": _kind(t)}
    if getattr(t, "is_grouped", False):
        out["group"] = t.group_track.name
    out["flags"] = _flags(t)
    out["mixer"] = {"volume": lom.shown(t.mixer_device.volume), "pan": lom.shown(t.mixer_device.panning),
                    "sends": _sends(song, t)}
    out["routing"] = dict((k, _route(t, a)) for k, a in sorted(lom.ROUTING.items()))
    try:
        out["routing"]["monitor"] = lom.MONITOR[t.current_monitoring_state]
    except Exception:
        pass
    out["devices"] = [_device(d, concise) for d in t.devices]
    out["clips"] = _clip_rows(song, t, concise)
    return out


# --- setting -------------------------------------------------------------------------------

def _prepare(song, change):
    """Resolve one change without applying it: (label, read_before, apply, read_after)."""
    if not isinstance(change, dict) or "target" not in change or "value" not in change:
        raise ValueError("each change is {\"target\": \"Track / Device / Param\", \"value\": \"110 Hz\"}")
    tg, value = lom.target(song, change["target"]), change["value"]
    t = tg.track
    if tg.param is not None:
        prm = tg.param
        raw = params.resolve_value(prm, value)
        return (lambda: lom.shown(prm)), (lambda: setattr(prm, "value", raw)), (lambda: lom.shown(prm))
    if tg.device is not None:
        on = next((x for x in tg.device.parameters if x.name == "Device On"), None)
        if on is None:
            raise ValueError("'%s' is a device; set one of its parameters, or the device on/off with a value of on/off"
                             % change["target"])
        raw = 1.0 if lom.to_bool(value, change["target"]) else 0.0
        return (lambda: lom.shown(on)), (lambda: setattr(on, "value", raw)), (lambda: lom.shown(on))
    s = tg.setting
    if s in ("mute", "solo", "arm"):
        b = lom.to_bool(value, change["target"])
        read = lambda: "on" if getattr(t, s) else "off"
        return read, (lambda: setattr(t, s, b)), read
    if s in lom.ROUTING:
        attr = lom.ROUTING[s]
        option = lom.find(getattr(t, "available_%ss" % attr), str(value), "%s option" % s, " of '%s'" % t.name)
        read = lambda: _route(t, attr)
        return read, (lambda: setattr(t, attr, option)), read
    if s == "monitor":
        m = lom.MONITOR.index(lom.find([_Named(x) for x in lom.MONITOR], str(value), "monitor setting").name)
        read = lambda: lom.MONITOR[t.current_monitoring_state]
        return read, (lambda: setattr(t, "current_monitoring_state", m)), read
    raise ValueError("'%s' names a track; add a device, parameter or setting (volume, pan, send A, mute, solo, "
                     "arm, output, output channel, input, input channel, monitor)" % change["target"])


class _Named(object):
    def __init__(self, name):
        self.name = name


def set_values(song, p, ctrl):
    """Apply every change or none: all targets and values are checked before the first one is set."""
    prepared, errors = [], []
    for k, change in enumerate(p.get("changes") or []):
        try:
            prepared.append((change["target"], _prepare(song, change)))
        except Exception as e:
            errors.append("change %d (%s): %s" % (k + 1, change.get("target") if isinstance(change, dict) else change, e))
    if errors:
        raise ValueError("nothing was changed:\n" + "\n".join(errors))
    if not prepared:
        raise ValueError("changes is empty")
    out = []
    for text, (before, apply, after) in prepared:
        b = before()
        apply()
        out.append({"target": text, "before": b, "after": after()})
    return {"changed": out}


# --- notes ---------------------------------------------------------------------------------

_EXTRA = (("mute", False), ("probability", 1.0), ("velocity_deviation", 0.0), ("release_velocity", 64.0))
COLUMNS = ["bar", "beat", "pitch", "length", "velocity"]


def _midi_track(song, name):
    tg = lom.target(song, name)
    if tg.device is not None or tg.param is not None or tg.setting:
        raise ValueError("'%s': name just the track" % name)
    if getattr(tg.track, "has_audio_input", False) and not getattr(tg.track, "has_midi_input", True):
        raise ValueError("'%s' is an audio track; notes live on MIDI tracks" % tg.track.name)
    return tg.track


def _origin(clip):
    """Song beat of the clip's clip-time zero (the start marker plays at the clip's start)."""
    return clip.start_time - clip.start_marker


def read_notes(song, p, ctrl):
    """Notes on a track's arrangement clips, in bars: bar (1-based, song-absolute), beat (1-based
    in the bar: 1 the downbeat, 2.5 the eighth after beat 2), pitch, length (beats), velocity."""
    concise = _concise(p)
    t = _midi_track(song, p["track"])
    start, end = lom.parse_bars(song, p["bars"]) if p.get("bars") else (0.0, 1e9)
    bpb = lom.beats_per_bar(song)
    clips, notes = [], []
    for c in _clips(t):
        if not (c.start_time < end and c.end_time > start) or not getattr(c, "is_midi_clip", True):
            continue
        clips.append({"name": c.name, "bars": lom.bars_text(song, c.start_time, c.end_time)})
        o = _origin(c)
        a, b = max(start, c.start_time), min(end, c.end_time)
        for n in c.get_notes_extended(0, 128, a - o, b - a):
            at = o + n.start_time
            if not (a - 1e-6 <= at < b - 1e-6):
                continue
            bar = int(at / bpb + 1e-6) + 1
            beat = round(at - (bar - 1) * bpb + 1, 6)
            row = [bar, beat, n.pitch, round(n.duration, 6), n.velocity]
            if concise:
                notes.append(row)
            else:
                d = dict(zip(COLUMNS, row), clip=c.name)
                d.update((k, getattr(n, k)) for k, dflt in _EXTRA if getattr(n, k, dflt) != dflt)
                notes.append(d)
    notes.sort(key=lambda r: (r[0], r[1], r[2]) if concise else (r["bar"], r["beat"], r["pitch"]))
    out = {"track": t.name, "bars": lom.bars_text(song, start, end) if p.get("bars") else "all", "clips": clips}
    if concise:
        out["columns"] = COLUMNS
    out["notes"] = notes
    return out


def _note_in(song, n):
    """A note as [bar, beat, pitch, length(, velocity)] or a dict -> (song beat, note dict)."""
    if isinstance(n, (list, tuple)):
        if len(n) < 4:
            raise ValueError("note %s: give [bar, beat, pitch, length, velocity]" % (list(n),))
        n = dict(zip(COLUMNS, n))
    missing = [k for k in COLUMNS[:4] if k not in n]
    if missing:
        raise ValueError("note %s is missing %s" % (n, ", ".join(missing)))
    at = lom.beat_of(song, n["bar"]) + float(n["beat"]) - 1.0
    spec = {"pitch": int(n["pitch"]), "duration": float(n["length"]), "velocity": float(n.get("velocity", 100))}
    spec.update((k, n[k]) for k, _ in _EXTRA if k in n)
    return at, spec


def write_notes(song, p, ctrl):
    """Write notes into bars [start_bar, start_bar + bars) of a MIDI track.

    mode "replace" clears those bars first, "add" keeps what is there. Clips that cover the
    bars are written into; with no clip there, one is created over exactly those bars. A
    clip covering only part of them is an error (say which), never a guess.
    """
    t = _midi_track(song, p["track"])
    mode = p.get("mode", "replace")
    if mode not in ("replace", "add"):
        raise ValueError("mode is 'replace' or 'add', not '%s'" % mode)
    first, count = float(p["start_bar"]), float(p["bars"])
    start, end = lom.beat_of(song, first), lom.beat_of(song, first + count)
    if count <= 0:
        raise ValueError("bars must be at least 1")
    placed = []
    for n in p.get("notes") or []:
        at, spec = _note_in(song, n)
        if not (start - 1e-6 <= at < end - 1e-6):
            raise ValueError("a note at bar %g beat %g is outside bars %s" % (
                float(n["bar"] if isinstance(n, dict) else n[0]), float(n["beat"] if isinstance(n, dict) else n[1]),
                lom.bars_text(song, start, end)))
        placed.append((at, spec))
    clips = sorted((c for c in _clips(t) if c.start_time < end - 1e-6 and c.end_time > start + 1e-6),
                   key=lambda c: c.start_time)
    created = not clips
    if created:
        t.create_midi_clip(start, end - start)
        clips = [c for c in _clips(t) if abs(c.start_time - start) < 1e-6]
        if not clips:
            raise RuntimeError("Live did not create a clip at bar %g on '%s'" % (first, t.name))
    else:
        edge, gaps = start, []
        for c in clips:
            if c.start_time > edge + 1e-6:
                gaps.append(lom.bars_text(song, edge, c.start_time))
            edge = max(edge, c.end_time)
        if edge < end - 1e-6:
            gaps.append(lom.bars_text(song, edge, end))
        if gaps:
            raise ValueError("bars %s on '%s' are only partly in clips (%s); no clip covers bars %s. Write within "
                             "a clip, or delete those clips first (live_clips)" % (
                                 lom.bars_text(song, start, end), t.name,
                                 ", ".join("'%s' %s" % (c.name, lom.bars_text(song, c.start_time, c.end_time)) for c in clips),
                                 ", ".join(gaps)))
    removed = added = 0
    for c in clips:
        o = _origin(c)
        a, b = max(start, c.start_time), min(end, c.end_time)
        if mode == "replace":
            removed += len(list(c.get_notes_extended(0, 128, a - o, b - a)))
            c.remove_notes_extended(0, 128, a - o, b - a)
        mine = [dict(spec, start_time=at - o) for at, spec in placed if a - 1e-6 <= at < b - 1e-6]
        if mine:
            c.add_new_notes(lom.note_specs(mine))
            added += len(mine)
        if p.get("clip_name"):
            c.name = p["clip_name"]
    return {"track": t.name, "bars": lom.bars_text(song, start, end), "mode": mode, "removed": removed, "added": added,
            "clips": [dict({"name": c.name, "bars": lom.bars_text(song, c.start_time, c.end_time)},
                           **({"created": True} if created else {})) for c in clips]}


# --- clips, tracks, transport, locators ----------------------------------------------------

def clips(song, p, ctrl):
    """duplicate | move | delete | mute | unmute | rename the arrangement clips that start in `bars`."""
    action = p.get("action")
    start, end = lom.parse_bars(song, p["bars"])
    tracks = [lom.track(song, p["track"])] if p.get("track") else list(song.tracks)
    chosen = [(t, c) for t in tracks for c in _clips(t) if start - 1e-6 <= c.start_time < end - 1e-6]
    if action not in ("duplicate", "move", "delete", "mute", "unmute", "rename"):
        raise ValueError("action is duplicate, move, delete, mute, unmute or rename, not '%s'" % action)
    if not chosen:
        raise LookupError("no clip starts in bars %s on %s" % (
            lom.bars_text(song, start, end), "'%s'" % p["track"] if p.get("track") else "any track"))
    rows = lambda pairs: [{"track": t.name, "name": c.name, "bars": lom.bars_text(song, c.start_time, c.end_time)}
                          for t, c in pairs]
    before = rows(chosen)
    if action in ("duplicate", "move"):
        if p.get("to_bar") is None:
            raise ValueError("%s needs to_bar" % action)
        to, times = lom.beat_of(song, p["to_bar"]), int(p.get("times", 1))
        span = end - start
        if action == "move" and (times != 1 or to < end and to + span > start):
            raise ValueError("move goes once, to bars that do not overlap the clips' own")
        for k in range(times):
            for t, c in chosen:
                t.duplicate_clip_to_arrangement(c, to + k * span + (c.start_time - start))
        if action == "move":
            for t, c in chosen:
                t.delete_clip(c)
        return {"action": action, "clips": before, "to": lom.bars_text(song, to, to + times * span), "times": times}
    for t, c in chosen:
        if action == "delete":
            t.delete_clip(c)
        elif action in ("mute", "unmute"):
            c.muted = action == "mute"
        else:
            if not p.get("name"):
                raise ValueError("rename needs name")
            c.name = p["name"]
    return {"action": action, "clips": before}


def tracks(song, p, ctrl):
    """create | duplicate | delete | rename tracks, by name."""
    action, name = p.get("action"), p.get("name")
    if action == "create":
        kind = p.get("kind", "midi")
        if kind == "return":
            song.create_return_track()
            new = list(song.return_tracks)[-1]
        else:
            ts = list(song.tracks)
            at = ts.index(lom.track(song, p["track"])) + 1 if p.get("track") else len(ts)
            {"midi": song.create_midi_track, "audio": song.create_audio_track}[kind](at)
            new = list(song.tracks)[at]
        if name:
            new.name = name
        return {"action": action, "track": new.name, "kind": kind}
    t = lom.track(song, p["track"])
    if action == "duplicate":
        ts = list(song.tracks)
        song.duplicate_track(ts.index(t))
        new = list(song.tracks)[ts.index(t) + 1]
        if name:
            new.name = name
        return {"action": action, "from": t.name, "track": new.name}
    if action == "rename":
        if not name:
            raise ValueError("rename needs name")
        old, t.name = t.name, name
        return {"action": action, "from": old, "track": t.name}
    if action == "delete":
        gone = t.name
        if t in list(song.return_tracks):
            song.delete_return_track(list(song.return_tracks).index(t))
        else:
            song.delete_track(list(song.tracks).index(t))
        return {"action": action, "track": gone}
    raise ValueError("action is create, duplicate, delete or rename, not '%s'" % action)


def transport(song, p, ctrl):
    """Change what is given (tempo, loop_bars, loop, position_bar, metronome, play), then report the state."""
    if p.get("tempo") is not None:
        song.tempo = float(p["tempo"])
    if p.get("loop_bars"):
        a, b = lom.parse_bars(song, p["loop_bars"])
        song.loop_start, song.loop_length = a, b - a
    if p.get("loop") is not None:
        song.loop = lom.to_bool(p["loop"], "loop")
    if p.get("metronome") is not None:
        song.metronome = lom.to_bool(p["metronome"], "metronome")
    if p.get("position_bar") is not None:
        song.current_song_time = lom.beat_of(song, p["position_bar"])
    if p.get("play") is not None:
        (song.start_playing if lom.to_bool(p["play"], "play") else song.stop_playing)()
    return {"playing": bool(song.is_playing), "position_bar": lom.bar_of(song, song.current_song_time),
            "tempo": song.tempo, "signature": "%d/%d" % (song.signature_numerator, song.signature_denominator),
            "loop": bool(song.loop), "loop_bars": lom.bars_text(song, song.loop_start, song.loop_start + song.loop_length),
            "metronome": bool(song.metronome)}


def _locators(song):
    return [{"name": c.name, "bar": lom.bar_of(song, c.time)} for c in sorted(song.cue_points, key=lambda c: c.time)]


def locators(song, p, ctrl):
    """list | set | delete. set names the locator at `bar`, or renames `locator`; a new one is
    added on the next tick (Live adds locators at the playhead), so the reply says pending."""
    action = p.get("action", "list")
    if action == "list":
        return {"locators": _locators(song)}
    cues = list(song.cue_points)
    if p.get("locator"):
        cue = lom.find(cues, p["locator"], "locator")
    elif p.get("bar") is not None:
        beat = lom.beat_of(song, p["bar"])
        cue = next((c for c in cues if abs(c.time - beat) < 1e-6), None)
    else:
        raise ValueError("%s needs bar or locator" % action)
    if action == "set" and cue is not None:
        if not p.get("name"):
            raise ValueError("set needs name")
        cue.name = p["name"]
        return {"locators": _locators(song)}
    if action not in ("set", "delete"):
        raise ValueError("action is list, set or delete, not '%s'" % action)
    if action == "delete" and cue is None:
        raise LookupError("no locator at bar %g; locators: %s" % (p["bar"], _locators(song)))
    if song.is_playing:
        raise RuntimeError("Live adds and removes locators at the playhead: stop playback first")
    beat = cue.time if cue is not None else lom.beat_of(song, p["bar"])
    song.current_song_time = beat

    def toggle():
        song.set_or_delete_cue()
        if action == "set":
            for c in song.cue_points:
                if abs(c.time - beat) < 1e-6:
                    c.name = p["name"]

    ctrl.schedule_message(1, toggle)
    return {"pending": action, "bar": lom.bar_of(song, beat)}


# --- loading -------------------------------------------------------------------------------

LOAD_ROOTS = ("audio_effects", "instruments", "midi_effects", "plugins", "drums", "user_folders", "packs")


def _browser_item(ctrl, item):
    """A browser item by exact name (searched inside Live), or by the path live_browser_find gave."""
    if item.startswith("app.browser"):
        return lom.resolve(None, ctrl, item)
    browser = ctrl.application().browser
    stack, near = [], []
    for r in LOAD_ROOTS:
        top = getattr(browser, r, None)
        if top is not None:
            stack.extend(top if r == "user_folders" else top.children)
    want, seen = lom._norm(item).lower(), 0
    while stack and seen < 20000:
        it = stack.pop(0)
        seen += 1
        n = str(it.name)
        if lom._norm(n).lower() == want and it.is_loadable:
            return it
        if want in n.lower() and it.is_loadable and len(near) < 8:
            near.append(n)
        if it.is_folder:
            stack.extend(it.children)
    raise LookupError("no loadable browser item '%s'%s; live_browser_find searches by part of a name" % (
        item, "; close: %s" % ", ".join("'%s'" % x for x in near) if near else ""))


def load(song, p, ctrl):
    """Load a device, plugin, preset or sample onto a track: find it, select the track, load."""
    t = lom.track(song, p["track"])
    it = _browser_item(ctrl, p["item"])
    select_track(song, t)
    ctrl.application().browser.load_item(it)
    return {"track": t.name, "loaded": str(it.name), "devices": [d.name for d in t.devices]}


def record_clip(song, p, ctrl):
    """Record a session clip into the arrangement from a bar (the slot by scene name or number)."""
    from . import session
    t = lom.track(song, p["track"])
    scene = lom.find(song.scenes, str(p["scene"]), "scene") if not str(p["scene"]).isdigit() \
        else list(song.scenes)[int(p["scene"]) - 1]
    return session.record_arrangement_clip(song, list(song.tracks).index(t), list(song.scenes).index(scene),
                                           lom.beat_of(song, p.get("start_bar", 1)), ctrl)
