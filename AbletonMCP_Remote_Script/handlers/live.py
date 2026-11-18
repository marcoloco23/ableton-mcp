"""The live_* commands: whole jobs on the open set, by name and bar, one round trip each.

Each takes (song, p, ctrl) like every dispatched command, so it also runs inside lom_batch.
Conventions (see lom.py): tracks, devices and parameters are named; bars are 1-based and
song-absolute; values are what Live shows ("-6 dB", "110 Hz", "High Pass 48dB").
"""

from __future__ import absolute_import, print_function, unicode_literals

import os

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

class _Named(object):
    def __init__(self, name):
        self.name = name


def _prepare(song, change, earlier):
    """Check one change without applying it: (before, apply, undo, after).

    ``earlier`` holds the (track, setting) pairs earlier changes in the same call set: a
    routing channel after its routing type is looked up when it is applied, among the
    channels the new type offers.
    """
    if not isinstance(change, dict) or "target" not in change or "value" not in change:
        raise ValueError("each change is {\"target\": \"Track / Device / Param\", \"value\": \"110 Hz\"}")
    text, value = change["target"], change["value"]
    tg = lom.target(song, text)
    t = tg.track
    if tg.param is not None or tg.device is not None:
        prm = tg.param
        if prm is None:
            prm = next((x for x in tg.device.parameters if x.name == "Device On"), None)
            if prm is None:
                raise ValueError("'%s' is a device; set one of its parameters, or switch it with on/off" % text)
            value = 1.0 if lom.to_bool(value, text) else 0.0
        if not getattr(prm, "is_enabled", True):
            raise ValueError("'%s' cannot be changed now: Live has it disabled (a macro or another control holds it)"
                             % text)
        raw, old = params.resolve_value(prm, value), prm.value
        return (lom.shown(prm), lambda: setattr(prm, "value", raw), lambda: setattr(prm, "value", old),
                lambda: lom.shown(prm))
    s = tg.setting
    if s is None:
        raise ValueError("'%s' names a track; add a device, parameter or setting (volume, pan, send A, mute, solo, "
                         "arm, output, output channel, input, input channel, monitor)" % text)
    is_master = t is song.master_track
    if is_master and s in ("mute", "solo", "arm", "monitor"):
        raise ValueError("'%s': the master has no %s" % (text, s))
    if s in ("arm", "monitor") and not getattr(t, "can_be_armed", False):
        raise ValueError("'%s': '%s' cannot be armed, so it has no %s (group and return tracks don't)" % (text, t.name, s))
    if s in ("mute", "solo", "arm"):
        b, old = lom.to_bool(value, text), bool(getattr(t, s))
        read = lambda: "on" if getattr(t, s) else "off"
        return (read(), lambda: setattr(t, s, b), lambda: setattr(t, s, old), read)
    if s == "monitor":
        m = lom.MONITOR.index(lom.find([_Named(x) for x in lom.MONITOR], str(value), "monitor setting").name)
        old = t.current_monitoring_state
        read = lambda: lom.MONITOR[t.current_monitoring_state]
        return (read(), lambda: setattr(t, "current_monitoring_state", m),
                lambda: setattr(t, "current_monitoring_state", old), read)
    attr = lom.ROUTING[s]
    pick = lambda: lom.find(getattr(t, "available_%ss" % attr), str(value), "%s option" % s, " of '%s'" % t.name)
    option = None if (s.endswith(" channel") and (id(t), s.split()[0]) in earlier) else pick()
    old = getattr(t, attr)
    read = lambda: _route(t, attr)
    return (_route(t, attr), lambda: setattr(t, attr, option or pick()), lambda: setattr(t, attr, old), read)


def set_values(song, p, ctrl):
    """Apply every change or none. Each target and value is checked before the first is set; if Live
    still refuses one, the changes already made are put back from their recorded before-values."""
    prepared, errors, earlier = [], [], set()
    for k, change in enumerate(p.get("changes") or []):
        try:
            prepared.append((change["target"], _prepare(song, change, earlier)))
            tg = lom.target(song, change["target"])
            earlier.add((id(tg.track), tg.setting))
        except Exception as e:
            errors.append("change %d (%s): %s" % (k + 1, change.get("target") if isinstance(change, dict) else change, e))
    if errors:
        raise ValueError("nothing was changed:\n" + "\n".join(errors))
    if not prepared:
        raise ValueError("changes is empty")
    out, undo = [], []
    for k, (text, (before, apply, back, after)) in enumerate(prepared):
        try:
            apply()
        except Exception as e:
            for u in reversed(undo):
                try:
                    u()
                except Exception:
                    pass
            raise RuntimeError("change %d (%s) failed in Live: %s. The %d change(s) before it were put back"
                               % (k + 1, text, e, len(undo)))
        undo.append(back)
        out.append({"target": text, "before": before, "after": after()})
    return {"changed": out}


# --- notes ---------------------------------------------------------------------------------
#
# A note's position is (bar, beat). On an arrangement clip bars count from the song's start;
# on a session clip (track + scene) from the clip's start. A looped arrangement clip plays
# from its start marker to its loop end once, then loop start to loop end over and over:
# reads unroll that, writes go into the first pass only (later passes are repeats of it).

_EXTRA = (("mute", False), ("probability", 1.0), ("velocity_deviation", 0.0), ("release_velocity", 64.0))
COLUMNS = ["bar", "beat", "pitch", "length", "velocity"]


def _midi_track(song, name):
    tg = lom.target(song, name)
    if tg.device is not None or tg.param is not None or tg.setting:
        raise ValueError("'%s': name just the track" % name)
    if getattr(tg.track, "has_audio_input", False) and not getattr(tg.track, "has_midi_input", True):
        raise ValueError("'%s' is an audio track; notes live on MIDI tracks" % tg.track.name)
    return tg.track


def _segments(clip, session=False):
    """Where a clip's notes sound: [(from, to, clip time at from)] in positions (song beats, or clip
    beats for a session clip). The first segment is the clip's own notes; later ones repeat its loop."""
    looping = bool(getattr(clip, "looping", False))
    if session:
        return [(0.0, clip.loop_end if looping else clip.end_marker, 0.0)]
    s, e, m = clip.start_time, clip.end_time, clip.start_marker
    if not looping:
        return [(s, e, m)]
    first, length = max(0.0, clip.loop_end - m), clip.loop_end - clip.loop_start
    out = [(s, min(e, s + first), m)] if first > 0 else []
    at = s + first
    while length > 0 and at < e - 1e-9:
        out.append((at, min(e, at + length), clip.loop_start))
        at += length
    return out


def _slot(song, t, scene):
    sc = lom.scene(song, scene)
    return list(t.clip_slots)[list(song.scenes).index(sc)], list(song.scenes).index(sc) + 1


def _note_row(song, at, n, concise, clip_name):
    bpb = lom.beats_per_bar(song)
    bar = int(at / bpb + 1e-6) + 1
    row = [bar, round(at - (bar - 1) * bpb + 1, 6), n.pitch, round(n.duration, 6), n.velocity]
    if concise:
        return row
    d = dict(zip(COLUMNS, row), clip=clip_name)
    d.update((k, getattr(n, k)) for k, dflt in _EXTRA if getattr(n, k, dflt) != dflt)
    return d


def read_notes(song, p, ctrl):
    """Notes in bars: bar, beat (1 the downbeat, 2.5 the eighth after beat 2), pitch, length (beats),
    velocity. Arrangement clips by song bar, looped ones unrolled; with scene, the session clip there."""
    concise = _concise(p)
    t = _midi_track(song, p["track"])
    start, end = lom.parse_bars(song, p["bars"]) if p.get("bars") else (0.0, 1e9)
    session = p.get("scene") is not None
    if session:
        slot, number = _slot(song, t, p["scene"])
        if not slot.has_clip:
            raise LookupError("'%s' has no clip in scene %d" % (t.name, number))
        sources = [slot.clip]
    else:
        sources = [c for c in _clips(t) if c.start_time < end and c.end_time > start and getattr(c, "is_midi_clip", True)]
    clips, notes = [], []
    for c in sources:
        segs = _segments(c, session)
        clips.append(dict({"name": c.name, "bars": lom.bars_text(song, segs[0][0], segs[-1][1]) if segs else ""},
                          **({"loop_bars": lom.bars_text(song, *segs[0][:2])} if len(segs) > 1 else {})))
        for a0, b0, ct in segs:
            a, b = max(start, a0), min(end, b0)
            if b <= a:
                continue
            for n in c.get_notes_extended(0, 128, ct + (a - a0), b - a):
                at = a0 + (n.start_time - ct)
                if a - 1e-6 <= at < b - 1e-6:
                    notes.append(_note_row(song, at, n, concise, c.name))
    notes.sort(key=lambda r: (r[0], r[1], r[2]) if concise else (r["bar"], r["beat"], r["pitch"]))
    out = {"track": t.name, "bars": lom.bars_text(song, start, end) if p.get("bars") else "all", "clips": clips}
    if session:
        out["scene"] = number
    if concise:
        out["columns"] = COLUMNS
    out["notes"] = notes
    return out


def _note_in(song, n):
    """A note as [bar, beat, pitch, length(, velocity)] or a dict -> (position, note spec), checked."""
    if isinstance(n, (list, tuple)):
        if len(n) < 4:
            raise ValueError("note %s: give [bar, beat, pitch, length, velocity]" % (list(n),))
        n = dict(zip(COLUMNS, n))
    missing = [k for k in COLUMNS[:4] if k not in n]
    if missing:
        raise ValueError("note %s is missing %s" % (n, ", ".join(missing)))
    bpb = lom.beats_per_bar(song)
    pitch, length, vel, beat = int(n["pitch"]), float(n["length"]), float(n.get("velocity", 100)), float(n["beat"])
    problems = [w for w, bad in (("pitch is 0-127", not 0 <= pitch <= 127), ("length must be above 0", length <= 0),
                                 ("velocity is 1-127", not 1 <= vel <= 127),
                                 ("beat is 1 to below %g" % (bpb + 1), not 1 <= beat < bpb + 1)) if bad]
    if problems:
        raise ValueError("note %s: %s" % ([n[k] for k in COLUMNS if k in n], "; ".join(problems)))
    spec = {"pitch": pitch, "duration": length, "velocity": vel}
    spec.update((k, n[k]) for k, _ in _EXTRA if k in n)
    return lom.beat_of(song, n["bar"]) + beat - 1.0, spec


def write_notes(song, p, ctrl):
    """Write notes into bars [start_bar, start_bar + bars) of a MIDI track (or, with scene, of the
    session clip there, bar 1 its start).

    mode "replace" clears those bars first, "add" keeps what is there. Clips that cover the bars
    are written into; with no clip there, one is created. A clip covering only part of the bars,
    or bars that are a looped clip's repeats, is an error that says so. Everything is checked and
    every clip's notes are built before the first note is removed or a clip created.
    """
    t = _midi_track(song, p["track"])
    mode = p.get("mode", "replace")
    if mode not in ("replace", "add"):
        raise ValueError("mode is 'replace' or 'add', not '%s'" % mode)
    first, count = float(p["start_bar"]), float(p["bars"])
    if count <= 0 or first < 1:
        raise ValueError("start_bar is 1 or later and bars at least 1")
    start, end = lom.beat_of(song, first), lom.beat_of(song, first + count)
    span = lom.bars_text(song, start, end)
    placed = []
    for n in p.get("notes") or []:
        at, spec = _note_in(song, n)
        if not (start - 1e-6 <= at < end - 1e-6):
            bar = int(at / lom.beats_per_bar(song) + 1e-6) + 1
            raise ValueError("a note at bar %d beat %g is outside bars %s" % (
                bar, round(at - lom.beat_of(song, bar) + 1, 6), span))
        placed.append((at, spec))
    session = p.get("scene") is not None
    create = None
    if session:
        slot, number = _slot(song, t, p["scene"])
        clips = [slot.clip] if slot.has_clip else []
        if not clips:
            create = lambda: (slot.create_clip(end), [slot.clip])[1]
    else:
        clips = sorted((c for c in _clips(t) if c.start_time < end - 1e-6 and c.end_time > start + 1e-6),
                       key=lambda c: c.start_time)
        if not clips:
            def create():
                t.create_midi_clip(start, end - start)
                return [c for c in _clips(t) if abs(c.start_time - start) < 1e-6]
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
                                     span, t.name,
                                     ", ".join("'%s' %s" % (c.name, lom.bars_text(song, c.start_time, c.end_time))
                                               for c in clips), ", ".join(gaps)))
    # plan: (clip or None for the one to create, clip-time from, length, specs)
    plan = []
    for c in clips or [None]:
        a0, b0, ct = _segments(c, session)[0] if c is not None else ((0.0, end, 0.0) if session else (start, end, 0.0))
        a, b = (max(start, c.start_time), min(end, c.end_time)) if c is not None and not session else (start, end)
        if b > b0 + 1e-6:
            own, extra = lom.bars_text(song, a0, b0), lom.bars_text(song, max(a, b0), b)
            if getattr(c, "looping", False):
                raise ValueError("'%s' holds its notes in bars %s and %s; notes in bars %s would never play as "
                                 "written. Write within bars %s, or turn the clip's loop off" % (
                                     c.name, own, "loops them" if session else "repeats them to its end", extra, own))
            raise ValueError("'%s' plays bars %s; bars %s are past its end" % (c.name, own, extra))
        mine = [dict(spec, start_time=ct + (at - a0)) for at, spec in placed if a - 1e-6 <= at < b - 1e-6]
        plan.append((c, ct + (a - a0), b - a, lom.note_specs(mine) if mine else ()))
    if create is not None:
        made = create()
        if not made:
            raise RuntimeError("Live did not create a clip at bar %g on '%s'" % (first, t.name))
        plan = [(made[0],) + plan[0][1:]]
    removed = added = 0
    for c, frm, length, specs in plan:
        if mode == "replace":
            removed += len(list(c.get_notes_extended(0, 128, frm, length)))
            c.remove_notes_extended(0, 128, frm, length)
        if specs:
            c.add_new_notes(specs)
            added += len(specs)
        if p.get("clip_name"):
            c.name = p["clip_name"]
    out = {"track": t.name, "bars": span, "mode": mode, "removed": removed, "added": added,
           "clips": [dict({"name": c.name}, **({"created": True} if create is not None else {}),
                          **({} if session else {"bars": lom.bars_text(song, c.start_time, c.end_time)}))
                     for c, _, _, _ in plan]}
    if session:
        out["scene"] = number
    return out


# --- clips, scenes, tracks, transport, locators --------------------------------------------

CLIP_ACTIONS = ("duplicate", "move", "delete", "mute", "unmute", "rename", "to_arrangement")


def clips(song, p, ctrl):
    """Act on arrangement clips that start in `bars`, or on the session clips in `scene`, on one
    track or every track: duplicate | move | delete | mute | unmute | rename | to_arrangement.

    to_arrangement fills the arrangement `bars` with the scene's clips, each repeated back to back
    at its own length; a copy that would run past the bars is left out and reported.
    """
    action = p.get("action")
    if action not in CLIP_ACTIONS:
        raise ValueError("action is %s, not '%s'" % (", ".join(CLIP_ACTIONS), action))
    tracks = [lom.track(song, p["track"])] if p.get("track") else list(song.tracks)
    where = "'%s'" % p["track"] if p.get("track") else "any track"
    session = p.get("scene") is not None
    if action in ("duplicate", "move") and session:
        raise ValueError("%s works on arrangement clips (give bars); to put session clips in the arrangement use "
                         "to_arrangement" % action)
    if action == "to_arrangement" and not (session and p.get("bars")):
        raise ValueError("to_arrangement fills arrangement bars with a scene's clips: give scene and bars, "
                         "e.g. scene=\"DROP 1\", bars=\"89-112\"")
    if action == "rename" and not p.get("name"):
        raise ValueError("rename needs name")
    if session:
        sc = lom.scene(song, p["scene"])
        number = list(song.scenes).index(sc) + 1
        slots = [(t, list(t.clip_slots)[number - 1]) for t in tracks]
        chosen = [(t, s.clip, s) for t, s in slots if s.has_clip]
        if not chosen:
            raise LookupError("no session clip in scene %d on %s" % (number, where))
        before = [{"track": t.name, "name": c.name, "scene": number} for t, c, _ in chosen]
    else:
        if not p.get("bars"):
            raise ValueError("%s needs bars (arrangement clips) or scene (session clips)" % action)
        start, end = lom.parse_bars(song, p["bars"])
        chosen = [(t, c, None) for t in tracks for c in _clips(t) if start - 1e-6 <= c.start_time < end - 1e-6]
        if not chosen:
            raise LookupError("no clip starts in bars %s on %s" % (lom.bars_text(song, start, end), where))
        before = [{"track": t.name, "name": c.name, "bars": lom.bars_text(song, c.start_time, c.end_time)}
                  for t, c, _ in chosen]
    if action == "to_arrangement":
        start, end = lom.parse_bars(song, p["bars"])
        left = []
        for t, c, _ in chosen:
            at = start
            while at + c.length <= end + 1e-6:
                t.duplicate_clip_to_arrangement(c, at)
                at += c.length
            if at < end - 1e-6:
                left.append({"track": t.name, "name": c.name, "bars": lom.bars_text(song, at, end),
                             "why": "a %g-beat clip does not fit" % c.length})
        return dict({"action": action, "clips": before, "to": lom.bars_text(song, start, end)},
                    **({"left_empty": left} if left else {}))
    if action in ("duplicate", "move"):
        if p.get("to_bar") is None:
            raise ValueError("%s needs to_bar" % action)
        to, times = lom.beat_of(song, p["to_bar"]), int(p.get("times", 1))
        if times < 1:
            raise ValueError("times is 1 or more")
        span = end - start
        if action == "move" and (times != 1 or to < end and to + span > start):
            raise ValueError("move goes once, to bars that do not overlap the clips' own")
        for k in range(times):
            for t, c, _ in chosen:
                t.duplicate_clip_to_arrangement(c, to + k * span + (c.start_time - start))
        if action == "move":
            for t, c, _ in chosen:
                t.delete_clip(c)
        return {"action": action, "clips": before, "to": lom.bars_text(song, to, to + times * span), "times": times}
    for t, c, slot in chosen:
        if action == "delete":
            slot.delete_clip() if slot is not None else t.delete_clip(c)
        elif action in ("mute", "unmute"):
            c.muted = action == "mute"
        else:
            c.name = p["name"]
    return {"action": action, "clips": before}


def _slot_clip(song, t, c):
    """A session clip in detail: its name, length in bars and, for audio, the file and transpose."""
    out = {"track": t.name, "name": c.name, "length_bars": round(c.length / lom.beats_per_bar(song), 4)}
    if getattr(c, "is_audio_clip", False):
        out.update(file=str(c.file_path), pitch_coarse=c.pitch_coarse)
    return out


def _scenes(song, detailed=False):
    rows = []
    for k, s in enumerate(song.scenes):
        filled = [(t, list(t.clip_slots)[k]) for t in song.tracks if list(t.clip_slots)[k].has_clip]
        rows.append({"scene": k + 1, "name": s.name,
                     "clips": [_slot_clip(song, t, sl.clip) if detailed else t.name for t, sl in filled]})
    return rows


def scenes(song, p, ctrl):
    """list | create | rename | fire | delete session scenes, by name or number (1 the top one)."""
    action = p.get("action", "list")
    if action == "list":
        return {"scenes": _scenes(song, detailed=not _concise(p))}
    if action == "create":
        at = list(song.scenes).index(lom.scene(song, p["scene"])) + 1 if p.get("scene") is not None else len(song.scenes)
        song.create_scene(at)
        if p.get("name"):
            list(song.scenes)[at].name = p["name"]
        return {"action": action, "scene": at + 1, "scenes": _scenes(song)}
    if p.get("scene") is None:
        raise ValueError("%s needs scene" % action)
    sc = lom.scene(song, p["scene"])
    number = list(song.scenes).index(sc) + 1
    if action == "rename":
        if not p.get("name"):
            raise ValueError("rename needs name")
        sc.name = p["name"]
    elif action == "fire":
        sc.fire()
        return {"action": action, "scene": number, "name": sc.name}
    elif action == "delete":
        song.delete_scene(number - 1)
    else:
        raise ValueError("action is list, create, rename, fire or delete, not '%s'" % action)
    return {"action": action, "scene": number, "scenes": _scenes(song)}


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
    """Check every argument, then change what is given (tempo, loop_bars, loop, metronome,
    position_bar, record, play) and report the state."""
    todo = []
    if p.get("tempo") is not None:
        tempo = float(p["tempo"])
        if not 20 <= tempo <= 999:
            raise ValueError("tempo is 20-999 BPM, not %g" % tempo)
        todo.append(lambda: setattr(song, "tempo", tempo))
    if p.get("loop_bars"):
        a, b = lom.parse_bars(song, p["loop_bars"])
        todo.append(lambda: (setattr(song, "loop_start", a), setattr(song, "loop_length", b - a)))
    for key in ("loop", "metronome"):
        if p.get(key) is not None:
            v = lom.to_bool(p[key], key)
            todo.append(lambda k=key, v=v: setattr(song, k, v))
    if p.get("position_bar") is not None:
        if float(p["position_bar"]) < 1:
            raise ValueError("position_bar is 1 or later")
        beat = lom.beat_of(song, p["position_bar"])
        todo.append(lambda: setattr(song, "current_song_time", beat))
    if p.get("record") is not None:
        rec = lom.to_bool(p["record"], "record")
        todo.append(lambda: setattr(song, "record_mode", rec))
    if p.get("play") is not None:
        play = lom.to_bool(p["play"], "play")
        todo.append(lambda: (song.start_playing if play else song.stop_playing)())
    for step in todo:
        step()
    return {"playing": bool(song.is_playing), "recording": bool(song.record_mode),
            "position_bar": lom.bar_of(song, song.current_song_time),
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
    if action not in ("set", "delete"):
        raise ValueError("action is list, set or delete, not '%s'" % action)
    if action == "set" and not p.get("name"):
        raise ValueError("set needs name")
    cues = list(song.cue_points)
    if p.get("locator"):
        cue = lom.find(cues, p["locator"], "locator")
    elif p.get("bar") is not None:
        beat = lom.beat_of(song, p["bar"])
        cue = next((c for c in cues if abs(c.time - beat) < 1e-6), None)
    else:
        raise ValueError("%s needs bar or locator" % action)
    if action == "set" and cue is not None:
        cue.name = p["name"]
        return {"locators": _locators(song)}
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
LOAD_DEPTH, LOAD_ITEMS = 4, 5000  # devices and plugins sit 2-3 deep; this keeps the scan to a second or two


def _browser_item(ctrl, item):
    """A browser item by exact name, searched breadth-first inside Live (bounded), or by the path
    live_browser_find gave."""
    if item.startswith("app.browser"):
        return lom.resolve(None, ctrl, item)
    browser = ctrl.application().browser
    stack, near = [], []
    for r in LOAD_ROOTS:
        top = getattr(browser, r, None)
        if top is not None:
            stack.extend((it, 1) for it in (top if r == "user_folders" else top.children))
    want, seen = lom._norm(item).lower(), 0
    while stack and seen < LOAD_ITEMS:
        it, depth = stack.pop(0)
        seen += 1
        n = str(it.name)
        if lom._norm(n).lower() == want and it.is_loadable:
            return it
        if want in n.lower() and it.is_loadable and len(near) < 8:
            near.append(n)
        if it.is_folder and depth < LOAD_DEPTH:
            stack.extend((ch, depth + 1) for ch in it.children)
    raise LookupError("no loadable browser item '%s' in the first %d items, %d folders deep%s; live_browser_find "
                      "searches deeper by part of a name, and its path loads anything" % (
                          item, seen, LOAD_DEPTH, "; close: %s" % ", ".join("'%s'" % x for x in near) if near else ""))


def _load_audio(song, t, p):
    """An audio file into a session slot (scene) or onto the arrangement (bar), as a new clip, with
    the clip properties in p["clip"] set on it. A property Live refuses removes the clip again."""
    path, settings = p["item"], p.get("clip") or {}
    if p.get("scene") is not None and p.get("bar") is not None:
        raise ValueError("give scene (a session slot) or bar (the arrangement), not both")
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise ValueError("with scene or bar, item is the absolute path of an audio file; '%s' is not one" % path)
    if not getattr(t, "has_audio_input", False):
        raise ValueError("'%s' is a MIDI track; an audio file needs an audio track" % t.name)
    if p.get("scene") is not None:
        slot, number = _slot(song, t, p["scene"])
        if slot.has_clip:
            raise ValueError("'%s' already has '%s' in scene %d; delete it first (live_clips)"
                             % (t.name, slot.clip.name, number))
        slot.create_audio_clip(path)
        clip, where, remove = slot.clip, {"scene": number}, slot.delete_clip
    else:
        beat = lom.beat_of(song, p["bar"])
        taken = [c for c in _clips(t) if c.start_time - 1e-6 <= beat < c.end_time - 1e-6]
        if taken:
            raise ValueError("bar %g on '%s' is in '%s' (bars %s); delete it first (live_clips)" % (
                float(p["bar"]), t.name, taken[0].name, lom.bars_text(song, taken[0].start_time, taken[0].end_time)))
        t.create_audio_clip(path, beat)
        clip = next(c for c in _clips(t) if abs(c.start_time - beat) < 1e-6)
        remove = lambda: t.delete_clip(clip)
    try:
        for k in sorted(settings):
            if not hasattr(clip, k):
                raise AttributeError("a clip has no '%s'" % k)
            setattr(clip, k, settings[k])
    except Exception as e:
        remove()
        raise ValueError("clip setting %s: %s; the new clip was removed again" % (k, e))
    if p.get("bar") is not None:
        where = {"bars": lom.bars_text(song, clip.start_time, clip.end_time)}
    info = dict((k, getattr(clip, k)) for k in ["name", "warping", "pitch_coarse"] + sorted(settings) if hasattr(clip, k))
    return dict({"track": t.name, "loaded": os.path.basename(path), "clip": info}, **where)


def load(song, p, ctrl):
    """Load a device, plugin, preset or sample onto a track, at the end of its chain: Live inserts
    after the selected device, so the last device is selected first. With scene or bar, item is an
    audio file and becomes a new clip there (see _load_audio)."""
    t = lom.track(song, p["track"])
    if p.get("scene") is not None or p.get("bar") is not None:
        return _load_audio(song, t, p)
    it = _browser_item(ctrl, p["item"])
    select_track(song, t)
    devices = list(t.devices)
    if devices:
        song.view.select_device(devices[-1])
    ctrl.application().browser.load_item(it)
    return {"track": t.name, "loaded": str(it.name), "devices": [d.name for d in t.devices]}
