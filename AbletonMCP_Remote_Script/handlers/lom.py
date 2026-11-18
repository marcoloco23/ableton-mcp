"""Address Live by name, count in bars, read values the way Live shows them.

Two spellings reach the same objects:

- a target, for the live_* tools: "BASS MID / EQ Eight / 1 Frequency A",
  "BASS MID / volume", "Drum Rack / Kick / Simpler / Decay";
- a LOM path, for the escape hatch: "tracks[BASS MID].devices[EQ Eight].parameters.1",
  "tracks[SUB].arrangement_clips[@113].name", "song.tempo", "app.view".

Names match exactly, then ignoring case; "Name#2" picks the second of several with one
name. A miss lists the names there are. Bars are 1-based and song-absolute, in the
song's own signature. No Live imports outside the note helpers, so it tests without Live.
"""

from __future__ import absolute_import, print_function, unicode_literals

import difflib
import re

from . import params


# --- names -------------------------------------------------------------------------------

def label(item):
    """What Live shows for an item: its name, or a routing option's display name."""
    return str(item.name if hasattr(item, "name") else item.display_name)


def _norm(text):
    return re.sub(r"\s*/\s*", "/", str(text)).strip()


def _matches(items, key):
    k = _norm(key)
    hits = [x for x in items if _norm(label(x)) == k]
    return hits or [x for x in items if _norm(label(x)).lower() == k.lower()]


def names_hint(items, key, what, where=""):
    """"no track 'BASS MD' (did you mean 'BASS MID'?). Tracks: 'BASS MID', …" """
    names = [label(x) for x in items]
    close = difflib.get_close_matches(key, names, 3, 0.5)
    shown = ", ".join("'%s'" % n for n in names[:60]) + (" … (%d more)" % (len(names) - 60) if len(names) > 60 else "")
    return "no %s '%s'%s%s. %ss%s: %s" % (
        what, key, where, " (did you mean %s?)" % " or ".join("'%s'" % c for c in close) if close else "",
        what.capitalize(), where, shown or "(none)")


class Ambiguous(LookupError):
    pass


def find(items, key, what="item", where=""):
    """The one item called ``key``. 'Name#2' is the second of that name; digits alone are an index."""
    items = list(items)
    hits = _matches(items, key)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise Ambiguous("%d %ss%s are named '%s'; say '%s#1' … '%s#%d'"
                        % (len(hits), what, where, key, key, key, len(hits)))
    m = re.match(r"^(.*)#(\d+)$", key)
    if m:
        same = _matches(items, m.group(1))
        if 1 <= int(m.group(2)) <= len(same):
            return same[int(m.group(2)) - 1]
    if key.strip().isdigit() and int(key) < len(items):
        return items[int(key)]
    raise LookupError(names_hint(items, key, what, where))


def _take(parts, i, items, what, where=""):
    """The item named by the longest run of '/'-parts from i (names may hold a '/'), and where the rest starts."""
    items, error = list(items), None
    for k in range(len(parts), i, -1):
        try:
            return find(items, "/".join(parts[i:k]).strip(), what, where), k
        except Ambiguous:
            raise
        except LookupError as e:
            error = e  # the last one tried is the shortest name: the one worth reporting
    raise error


# --- targets: "Track / Device / Param" ------------------------------------------------------

TRACK_SETTINGS = ("volume", "pan", "mute", "solo", "arm", "output", "output channel",
                  "input", "input channel", "monitor")
ROUTING = {"output": "output_routing_type", "output channel": "output_routing_channel",
           "input": "input_routing_type", "input channel": "input_routing_channel"}
MONITOR = ["In", "Auto", "Off"]


def all_tracks(song):
    """Tracks, return tracks and the master, the master answering to 'Master'."""
    return list(song.tracks) + list(song.return_tracks) + [_Master(song.master_track)]


class _Master(object):
    """The master track under the name the mixer shows."""

    def __init__(self, track):
        self.track, self.name = track, "Master"


def track(song, name):
    t = find(all_tracks(song), name, "track")
    return t.track if isinstance(t, _Master) else t


class Target(object):
    """What a target names: a track, a device on it, one of its parameters, or a track setting."""

    def __init__(self, text, track, device=None, param=None, setting=None):
        self.text, self.track, self.device, self.param, self.setting = text, track, device, param, setting


def send_param(song, trk, which):
    """'A', 'A-Reverb' or the return's name -> that send."""
    returns = list(song.return_tracks)
    sends = list(trk.mixer_device.sends)
    w = which.strip()
    if len(w) == 1 and w.isalpha() and ord(w.upper()) - 65 < len(sends):
        return sends[ord(w.upper()) - 65]
    return sends[returns.index(find(returns, w, "return track"))]


def target(song, text):
    """Resolve 'Track', 'Track / Device[ / Chain / Device …][ / Param]' or 'Track / <setting>'.

    Settings: volume, pan, send <A | return name>, mute, solo, arm, output, output channel,
    input, input channel, monitor. A rack's chains are walked by name.
    """
    parts = text.split("/")
    t, i = _take(parts, 0, all_tracks(song), "track")
    trk = t.track if isinstance(t, _Master) else t
    if i == len(parts):
        return Target(text, trk)
    rest = _norm("/".join(parts[i:])).lower()
    if rest in TRACK_SETTINGS:
        md = trk.mixer_device
        if rest == "volume":
            return Target(text, trk, param=md.volume)
        if rest == "pan":
            return Target(text, trk, param=md.panning)
        return Target(text, trk, setting=rest)
    if rest.startswith("send "):
        return Target(text, trk, param=send_param(song, trk, "/".join(parts[i:]).strip()[5:]))
    holder, where = trk, " on '%s'" % trk.name
    while True:
        dev, i = _take(parts, i, holder.devices, "device", where)
        if i == len(parts):
            return Target(text, trk, device=dev)
        not_param = LookupError("'%s' goes past a parameter of '%s'" % (text, dev.name))
        try:
            prm, j = _take(parts, i, dev.parameters, "parameter", " on '%s'" % dev.name)
            if j == len(parts):
                return Target(text, trk, device=dev, param=prm)
        except Ambiguous:
            raise
        except LookupError as e:
            not_param = e
        if not getattr(dev, "can_have_chains", False):
            raise not_param
        try:
            chain, i = _take(parts, i, dev.chains, "chain", " in '%s'" % dev.name)
        except Ambiguous:
            raise
        except LookupError as e:
            raise LookupError("%s; and %s" % (not_param, e))
        if i == len(parts):
            raise LookupError("'%s' names a chain; add a device in it" % text)
        holder, where = chain, " in chain '%s'" % chain.name


# --- bars ----------------------------------------------------------------------------------

def beats_per_bar(song):
    return song.signature_numerator * 4.0 / song.signature_denominator


def beat_of(song, bar):
    return (float(bar) - 1.0) * beats_per_bar(song)


def bar_of(song, beat):
    return round(beat / beats_per_bar(song) + 1.0, 6)


def bars_text(song, start, end):
    """[start, end) in beats as Live's bars, last bar inclusive: 17-24."""
    return "%g-%g" % (bar_of(song, start), bar_of(song, end) - 1)


def parse_bars(song, bars):
    """'49-52', '49', 49 or [49, 52] (inclusive) -> [start, end) in beats."""
    if isinstance(bars, (list, tuple)):
        a, b = bars
    elif isinstance(bars, (int, float)):
        a = b = bars
    else:
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*$", str(bars))
        if not m:
            raise ValueError("bars '%s' should look like '49-52' or '49'" % bars)
        a, b = m.group(1), m.group(2) or m.group(1)
    a, b = float(a), float(b)
    if b < a or a < 1:
        raise ValueError("bars '%s': the first bar is 1 and comes before the last" % (bars,))
    return beat_of(song, a), beat_of(song, b + 1)


def spans_text(song, spans):
    """Merge touching [start, end) beat spans: '17-48, 65-208'."""
    merged = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return ", ".join(bars_text(song, s, e) for s, e in merged)


def clip_at(song, clips, bar):
    beat = beat_of(song, bar)
    clips = list(clips)
    for c in clips:
        if c.start_time - 1e-6 <= beat < c.end_time - 1e-6:
            return c
    raise LookupError("no clip at bar %g; clips are at bars %s" % (
        bar, spans_text(song, [(c.start_time, c.end_time) for c in clips]) or "(none)"))


# --- LOM paths -----------------------------------------------------------------------------

def parse_path(path):
    """'tracks[BASS MID].devices.0' -> [('attr','tracks'), ('key','BASS MID'), ('attr','devices'), ('index',0)].
    A dot inside brackets is part of the name; brackets may nest: 'tracks[Bass [Sub]]'."""
    steps, word, i, n = [], "", 0, len(path)

    def flush(w):
        if w:
            steps.append(("index", int(w)) if w.isdigit() else ("attr", w))
        return ""

    while i < n:
        c = path[i]
        if c == ".":
            word = flush(word)
            i += 1
        elif c == "[":
            word = flush(word)
            depth, j = 1, i + 1
            while j < n and depth:
                depth += {"[": 1, "]": -1}.get(path[j], 0)
                j += 1
            if depth:
                raise ValueError("path '%s': a '[' is never closed" % path)
            key = path[i + 1:j - 1]
            if key.startswith("@"):
                try:
                    steps.append(("at", float(key[1:])))
                except ValueError:
                    raise ValueError("path '%s': [@…] takes a bar number, e.g. [@49]" % path)
            else:
                steps.append(("key", key))
            i = j
        else:
            word += c
            i += 1
    flush(word)
    return steps


def _is_list(obj):
    return not isinstance(obj, (str, dict)) and hasattr(obj, "__len__") and hasattr(obj, "__getitem__")


def _step(song, obj, kind, arg, what):
    if kind == "index":
        return list(obj)[arg]
    if kind == "key":
        return find(obj, arg, what)
    if kind == "at":
        return clip_at(song, obj, arg)
    try:
        return getattr(obj, arg)
    except AttributeError:
        if _is_list(obj):  # an attribute of a list is that attribute of each item: 'tracks.name'
            return [getattr(x, arg) for x in obj]
        raise AttributeError("%s has no '%s'" % (type(obj).__name__, arg))


def resolve(song, ctrl, path, start=None):
    """Walk a LOM path from the Song ('app.' starts at the Application), or from ``start``."""
    steps = parse_path(path)
    obj = song if start is None else start
    if steps[:1] == [("attr", "app")]:
        obj, steps = ctrl.application(), steps[1:]
    elif steps[:1] == [("attr", "song")]:
        steps = steps[1:]
    what = "item"
    for kind, arg in steps:
        obj = _step(song, obj, kind, arg, what)
        if kind == "attr":
            what = arg[:-1] if arg.endswith("s") else arg
    return obj


# --- values --------------------------------------------------------------------------------

NOTE_FIELDS = ("pitch", "start_time", "duration", "velocity", "mute",
               "probability", "velocity_deviation", "release_velocity")


def is_param(obj):
    return hasattr(obj, "str_for_value") and hasattr(obj, "value") and hasattr(obj, "is_quantized")


def shown(param):
    return str(param.str_for_value(param.value))


def param_info(param):
    out = {"name": str(param.name), "value": param.value, "display": shown(param),
           "min": param.min, "max": param.max, "quantized": bool(param.is_quantized)}
    if param.is_quantized:
        out["items"] = [str(i) for i in param.value_items]
    return out


def plain(value):
    """A Live value as JSON: parameters with their shown value, notes as dicts, objects by name."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_param(value):
        return param_info(value)
    if hasattr(value, "pitch") and hasattr(value, "start_time"):
        return dict((f, getattr(value, f)) for f in NOTE_FIELDS + ("note_id",) if hasattr(value, f))
    if hasattr(value, "name"):
        return {"name": str(value.name), "class": type(value).__name__}
    if hasattr(value, "display_name"):
        return {"name": str(value.display_name), "class": type(value).__name__}
    try:
        return [plain(v) for v in value]
    except TypeError:
        return str(value)


def to_bool(value, what):
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("on", "true", "1", "yes"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise ValueError("%s takes on/off, got '%s'" % (what, value))


# --- the lom_* commands --------------------------------------------------------------------

def lom_get(song, p, ctrl):
    """{path, fields?, list_attrs?}. fields: read these (dotted, named) on each element or on the object."""
    obj = resolve(song, ctrl, p["path"])
    if p.get("list_attrs"):
        return {"path": p["path"], "class": type(obj).__name__,
                "attrs": sorted(a for a in dir(obj) if not a.startswith("_"))}
    fields = p.get("fields")
    if not fields:
        return {"path": p["path"], "value": plain(obj)}
    errors = []

    def row(item, where):
        out = {}
        for f in fields:
            try:
                out[f] = plain(resolve(song, ctrl, f, start=item))
            except Exception as e:
                out[f] = None
                errors.append("%s.%s: %s" % (where, f, e))
        return out

    value = [row(x, "%s.%d" % (p["path"], i)) for i, x in enumerate(obj)] if _is_list(obj) else row(obj, p["path"])
    out = {"path": p["path"], "value": value}
    if errors:
        out["errors"] = errors[:20]
    return out


def set_param_value(param, value):
    """Set a parameter by its shown value (or a raw number); returns before and after as shown."""
    before = shown(param)
    param.value = params.resolve_value(param, value)
    return {"param": str(param.name), "before": before, "display": shown(param), "value": param.value}


def lom_set(song, p, ctrl):
    """{path, value | value_path}. A parameter path takes what Live shows ("-6 dB", "110 Hz").
    An object property given a name picks that option: 'tracks[X].output_routing_type' = "Master"."""
    steps = parse_path(p["path"])
    target = resolve(song, ctrl, p["path"])
    if is_param(target) and "value_path" not in p:
        return dict(set_param_value(target, p["value"]), path=p["path"])
    head, last = p["path"], steps[-1]
    if last[0] != "attr":
        raise ValueError("set needs a path ending in a property or a parameter, not '%s'" % p["path"])
    parent = resolve(song, ctrl, head[:len(head) - len(last[1])].rstrip(".")) if len(steps) > 1 else song
    attr = last[1]
    if "value_path" in p:
        value = resolve(song, ctrl, p["value_path"])
    else:
        value = p["value"]
        options = getattr(parent, "available_%ss" % attr, None)
        if isinstance(value, str) and options is not None:
            value = find(options, value, attr.replace("_", " "))
    setattr(parent, attr, value)
    return {"path": p["path"], "value": plain(getattr(parent, attr))}


def lom_call(song, p, ctrl):
    method = resolve(song, ctrl, p["path"])
    args = [lom_arg(song, ctrl, a) for a in p.get("args", [])]
    return {"path": p["path"], "result": plain(method(*args))}


def lom_arg(song, ctrl, a):
    """{"$path": ...} is a Live object; {"$notes": [{pitch, start_time, duration, ...}]} is
    the tuple of MidiNoteSpecification that clip.add_new_notes takes; anything else as is."""
    if isinstance(a, dict) and "$path" in a:
        return resolve(song, ctrl, a["$path"])
    if isinstance(a, dict) and "$notes" in a:
        return note_specs(a["$notes"])
    return a


def lom_set_param(song, p, ctrl):
    """{path: device, param: name} or {path: parameter}; value as Live shows it or a raw number."""
    obj = resolve(song, ctrl, p["path"])
    param = params.find_parameter(obj, p["param"]) if p.get("param") else obj
    return set_param_value(param, p["value"])


def note_specs(notes):
    import Live
    return tuple(Live.Clip.MidiNoteSpecification(**dict((k, n[k]) for k in NOTE_FIELDS if k in n))
                 for n in notes)
