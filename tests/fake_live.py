"""A small fake of Live's object model: enough Song, tracks, devices, parameters, clips and
notes to run the Remote Script's handlers without Live."""
import math
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "AbletonMCP_Remote_Script"))

class WarpMarker:
    """Live.Clip.WarpMarker: a warp marker specification, sample_time in seconds."""

    def __init__(self, beat_time=0.0, sample_time=0.0):
        self.beat_time, self.sample_time = beat_time, sample_time


# handlers build notes with Live.Clip.MidiNoteSpecification; this one keeps what it is given.
_live = types.ModuleType("Live")
_live.Clip = types.SimpleNamespace(MidiNoteSpecification=lambda **kw: dict(kw), WarpMarker=WarpMarker)
sys.modules.setdefault("Live", _live)

from handlers import live, lom  # noqa: E402,F401


class Param:
    def __init__(self, name, show=lambda v: "%.2f" % v, lo=0.0, hi=1.0, value=None, items=None):
        self.name, self.show, self.min, self.max = name, show, lo, hi
        self.value = lo if value is None else value
        self.is_quantized, self.is_enabled = items is not None, True
        self.value_items = list(items or [])
        self.touched, self.automation_state = False, 0

    def str_for_value(self, v):
        return self.value_items[int(v)] if self.is_quantized else self.show(v)

    def begin_gesture(self):
        self.touched = True

    def end_gesture(self):
        self.touched = False

    def re_enable_automation(self):
        self.automation_state = 1 if self.automation_state == 2 else self.automation_state


def db(v):  # a fader: 0 is -inf, 0.5 is 0 dB, 1 is +6 dB
    return "-inf dB" if v <= 0 else "%.1f dB" % (20 * math.log10(v / 0.5))


def pan(v):
    return "C" if abs(v) < 0.01 else ("%dL" % round(-v * 50) if v < 0 else "%dR" % round(v * 50))


def hz(v):  # 20 Hz .. 20 kHz, exponential like Live's filters
    f = 20 * 1000 ** v
    return "%.2f kHz" % (f / 1000) if f >= 1000 else "%.0f Hz" % f


class Device:
    def __init__(self, name, parameters=(), chains=None, class_name="Fake"):
        self.name, self.class_name = name, class_name
        self.parameters = [Param("Device On", items=["Off", "On"], value=1.0)] + list(parameters)
        self.can_have_chains = chains is not None
        self.chains = list(chains or [])


class Chain:
    def __init__(self, name, devices):
        self.name, self.devices = name, list(devices)


def eq_eight():
    return Device("EQ Eight", [Param("1 Filter Type A", items=["Low Cut 48dB", "Low Cut 12dB", "Bell", "High Pass 48dB"]),
                               Param("1 Frequency A", hz), Param("1 Gain A", lambda v: "%.1f dB" % (v * 30 - 15))],
                  class_name="Eq8")


class Note:
    def __init__(self, pitch, start_time, duration, velocity=100, **extra):
        self.pitch, self.start_time, self.duration, self.velocity = pitch, start_time, duration, velocity
        self.mute, self.probability, self.velocity_deviation, self.release_velocity = False, 1.0, 0.0, 64.0
        self.__dict__.update(extra)


class Clip:
    """An arrangement clip over [start, end) in song beats, or a session clip (start 0). With loop=(a, b)
    it loops clip time a..b after playing from its start marker to b."""

    def __init__(self, name, start, end, notes=(), start_marker=0.0, muted=False, loop=None):
        self.name, self.start_time, self.end_time, self.start_marker, self.muted = name, start, end, start_marker, muted
        self.notes, self.is_midi_clip = list(notes), True
        self.looping = loop is not None
        self.loop_start, self.loop_end = loop or (0.0, end - start + start_marker)
        self.end_marker = self.loop_end
        self.length = self.loop_end - self.loop_start
        self.warping, self.pitch_coarse = True, 0
        self.groove = None  # a Groove from the song's groove pool, or None as in Live

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self.notes if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        gone = self.get_notes_extended(from_pitch, pitch_span, from_time, time_span)
        self.notes = [n for n in self.notes if n not in gone]

    def add_new_notes(self, specs):
        self.notes += [Note(**s) for s in specs]


class AudioClip(Clip):
    """An audio clip of `seconds` of audio, auto-warped at `tempo` (one real marker at (0 s, beat 0)) or
    warped with `markers` [(seconds, beat)]. Live 12's warp-marker rules, as measured: warp_markers ends
    with a shadow marker 1/32 beat after the last real one, which cannot be removed or moved; removing
    the only real marker does nothing; markers are addressed by beat; a marker added or moved must keep
    beats rising with samples, its neighbours the shadow included. Error texts are Live's."""

    SHADOW = 1 / 32.0

    def __init__(self, name, start, seconds, tempo=120.0, markers=None, warping=True, sample_rate=44100):
        self._warping = True
        Clip.__init__(self, name, start, start + seconds * tempo / 60.0)
        self.is_audio_clip, self.is_midi_clip = True, False
        self.sample_rate, self.sample_length, self.tempo = sample_rate, int(seconds * sample_rate), tempo
        self.real = [list(m) for m in markers] if markers else [[0.0, 0.0]]
        self._warping, self.calls = warping, []

    @property
    def warping(self):
        return self._warping

    @warping.setter
    def warping(self, on):
        if on and not self._warping:
            self.real = [[0.0, 0.0]]  # Live auto-warps a clip when warping is switched on
        self._warping = on

    def _shadow(self):
        (s, b), before = self.real[-1], self.real[-2] if len(self.real) > 1 else None
        spb = (s - before[0]) / (b - before[1]) if before else 60.0 / self.tempo
        return [s + self.SHADOW * spb, b + self.SHADOW]

    @property
    def warp_markers(self):
        return [WarpMarker(beat_time=b, sample_time=s) for s, b in self.real + [self._shadow()]]

    def _at(self, beat):
        for k, (_, b) in enumerate(self.real):
            if abs(b - beat) < 1e-9:
                return k
        if abs(self._shadow()[1] - beat) < 1e-9:
            return None
        raise RuntimeError("The specified warp marker doesn't exist")

    def add_warp_marker(self, m):
        if not isinstance(m, WarpMarker):
            raise TypeError("No registered converter was able to produce a C++ rvalue of type NApiHelpers::TWarpMarker "
                            "from this Python object of type %s" % type(m).__name__)
        self.calls.append(("add", m.sample_time, m.beat_time))
        if not self._warping:
            raise RuntimeError("Cannot add warp marker to an unwarped clip.")
        s, b = m.sample_time, m.beat_time
        if not 0 <= s <= self.sample_length / float(self.sample_rate):
            raise RuntimeError("Warp marker sample time is out of range.")
        every = sorted(self.real + [self._shadow()])
        left = [x for x in every if x[0] < s]
        right = [x for x in every if x[0] > s]
        if any(abs(x[0] - s) < 1e-9 for x in every):
            raise RuntimeError("Segment length out of range.")
        if left and b <= left[-1][1]:
            raise RuntimeError("Warp marker beat time is ahead of left adjacent marker's beat time.")
        if right and b >= right[0][1]:
            raise RuntimeError("Warp marker beat time is behind right adjacent marker's beat time.")
        self.real = sorted(self.real + [[s, b]])

    def remove_warp_marker(self, beat):
        self.calls.append(("remove", beat))
        k = self._at(beat)
        if k is None:
            raise RuntimeError("The shadow marker can't be deleted.")
        if len(self.real) > 1:
            del self.real[k]

    def move_warp_marker(self, beat, distance):
        self.calls.append(("move", beat, distance))
        k = self._at(beat)
        if k is None:
            raise RuntimeError("The shadow marker can't be moved.")
        b = beat + distance
        if (k > 0 and b <= self.real[k - 1][1]) or (k + 1 < len(self.real) and b >= self.real[k + 1][1]):
            raise RuntimeError("Segment length out of range.")
        self.real[k][1] = b


class Routing:
    """A routing type or channel. attached_object is the track a routing type names (Live 12: an input from
    another track, a group, a return or the Main), else None (Ext. In, All Ins, No Input, Resampling)."""

    def __init__(self, name, attached_object=None):
        self.display_name, self.attached_object = name, attached_object


class Mixer:
    def __init__(self, sends):
        self.volume, self.panning = Param("Track Volume", db, value=0.5), Param("Track Panning", pan, lo=-1.0)
        self.panning.value = 0.0
        self.sends = [Param(n, db) for n in sends]


class Track:
    def __init__(self, name, devices=(), clips=(), audio=False, sends=("A-Reverb", "B-Delay")):
        self.name, self.devices, self.arrangement_clips = name, list(devices), list(clips)
        self.has_audio_input, self.has_midi_input = audio, not audio
        self.is_foldable = self.is_grouped = self.is_frozen = False
        self.group_track = None
        self.mute = self.solo = self.arm = False
        self.mixer_device = Mixer(sends)
        self.available_output_routing_types = [Routing(n) for n in ("Master", "Sends Only", "🎸 BASS")]
        self.output_routing_type = self.available_output_routing_types[0]
        self.available_input_routing_types = [Routing(n) for n in ("Ext. In", "Resampling")]
        self.input_routing_type = self.available_input_routing_types[0]
        self.input_routing_channel = Routing("")
        self.available_output_routing_channels = self.available_input_routing_channels = []
        self.current_monitoring_state = 1  # 0 In, 1 Auto, 2 Off
        self.muted_via_solo = False        # Live sets it on every track another track's solo silences
        self.can_be_armed = True
        self.clip_slots = [Slot(), Slot()]

    def create_midi_clip(self, start, length):
        self.arrangement_clips.append(Clip("", start, start + length))
        self.arrangement_clips.sort(key=lambda c: c.start_time)

    def duplicate_clip_to_arrangement(self, clip, at):
        self.arrangement_clips.append(Clip(clip.name, at, at + clip.end_time - clip.start_time,
                                           [Note(n.pitch, n.start_time, n.duration, n.velocity) for n in clip.notes]))

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)

    def create_audio_clip(self, path, position):
        self.arrangement_clips.append(Clip(path.rsplit("/", 1)[-1].rsplit(".", 1)[0], position, position + 16.0))


def take_input(track, source, channel="Post FX", monitor=0):
    """Route `source` (a Track, or "Resampling") into `track`, as Live's input chooser does: monitor 0 is In."""
    track.input_routing_type = (Routing("Resampling") if source == "Resampling"
                                else Routing(source.name, source))
    track.input_routing_channel = Routing("" if source == "Resampling" else channel)
    track.current_monitoring_state = monitor


class Slot:
    def __init__(self, clip=None):
        self.clip = clip

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip("", 0.0, length)

    def create_audio_clip(self, path):
        self.clip = Clip(path.rsplit("/", 1)[-1].rsplit(".", 1)[0], 0.0, 8.0)
        self.clip.is_audio_clip, self.clip.file_path = True, path

    def delete_clip(self):
        self.clip = None


class Scene:
    def __init__(self, name):
        self.name, self.fired = name, 0

    def fire(self):
        self.fired += 1


class Groove:
    def __init__(self, name):
        self.name = name


class Cue:
    def __init__(self, name, time):
        self.name, self.time = name, time


class Song:
    def __init__(self, tracks, returns=(), cues=()):
        self.tracks, self.return_tracks, self.cue_points = list(tracks), list(returns), list(cues)
        self.master_track = Track("Master", [Device("Limiter")], sends=())
        self.signature_numerator, self.signature_denominator = 4, 4
        self.tempo, self.loop, self.loop_start, self.loop_length = 124.0, True, 0.0, 32.0
        self.current_song_time, self.is_playing, self.metronome, self.record_mode = 0.0, False, False, False
        self.session_automation_record, self.song_length = False, 1024.0
        self.scenes = [Scene("Intro"), Scene("Groove")]
        # as read from a real set, 20.11.2026: song.groove_pool.grooves, each with a name
        self.groove_pool = types.SimpleNamespace(grooves=[Groove(n) for n in (
            "Swing Logic 16ths 52", "Swing Logic 16ths 58", "Swing MPC 3000 16ths 57")])
        self.master_track.can_be_armed = False
        for r in self.return_tracks:
            r.can_be_armed = False
        self.view = types.SimpleNamespace(selected_track=None, selected_device=None)
        self.view.select_device = lambda d: setattr(self.view, "selected_device", d)

    def create_scene(self, i):
        self.scenes.insert(i, Scene(""))
        for t in self.tracks:
            t.clip_slots.insert(i, Slot())

    def delete_scene(self, i):
        del self.scenes[i]
        for t in self.tracks:
            del t.clip_slots[i]

    def create_midi_track(self, i):
        self.tracks.insert(i, self._placed(Track("MIDI"), i))

    def create_audio_track(self, i):
        self.tracks.insert(i, self._placed(Track("Audio", audio=True), i))

    def _placed(self, t, i):
        """As in Live (19.11.2026): a new track joins the group of the track it is inserted before, so
        one inserted right after a group's last track lands outside the group."""
        after = self.tracks[i] if i < len(self.tracks) else None
        if after is not None and after.is_grouped:
            t.is_grouped, t.group_track = True, after.group_track
        return t

    def duplicate_track(self, i):
        t = self.tracks[i]
        self.tracks.insert(i + 1, Track(t.name + " copy", t.devices, t.arrangement_clips))

    def delete_track(self, i):
        del self.tracks[i]

    def set_or_delete_cue(self):
        here = [c for c in self.cue_points if c.time == self.current_song_time]
        if here:
            self.cue_points.remove(here[0])
        else:
            self.cue_points.append(Cue("", self.current_song_time))

    def start_playing(self):
        self.is_playing = True

    def re_enable_automation(self):
        """Song-wide: every overridden parameter in the set follows its automation again."""
        for t in self.tracks + self.return_tracks:
            for prm in [t.mixer_device.volume] + [x for d in t.devices for x in d.parameters]:
                prm.re_enable_automation()

    def stop_playing(self):
        self.is_playing = False


class Ctrl:
    """Runs a scheduled step at once, like Live's next tick."""

    def schedule_message(self, ticks, fn):
        fn()


class Transport(Ctrl):
    """Live's transport and arrangement automation recording, one tick at a time (a model, not a
    measurement). A scheduled step runs on a later tick; playing moves the playhead STEP beats a tick.
    With Arrangement Record and Automation Arm on, a parameter inside a gesture is written at the
    playhead when its value has changed since the last write (Live records only a value that changes),
    replacing the points of its lane since that write; a lane holds its first point's value before it.
    lanes: {param: {beat: raw value}}."""

    STEP = 0.25

    def __init__(self, song, params):
        self.song, self.params, self.queue, self.tick_no = song, list(params), [], 0
        self.lanes, self.last = dict((id(prm), {}) for prm in self.params), {}

    def schedule_message(self, ticks, fn):
        self.queue.append((self.tick_no + ticks, fn))

    def run(self, limit=200000):
        while self.queue and limit:
            limit -= 1
            self.tick_no += 1
            song = self.song
            if song.is_playing:
                song.current_song_time += self.STEP
            if song.is_playing and song.record_mode and song.session_automation_record:
                now = song.current_song_time
                for prm in self.params:
                    lane, key = self.lanes[id(prm)], id(prm)
                    if not prm.touched:
                        self.last.pop(key, None)
                    elif key not in self.last or self.last[key][1] != prm.value:
                        since = self.last.get(key, (now, None))[0]
                        for b in [b for b in lane if since < b <= now]:
                            del lane[b]
                        lane[now] = prm.value
                        self.last[key] = (now, prm.value)
            due = [q for q in self.queue if q[0] <= self.tick_no]
            self.queue = [q for q in self.queue if q[0] > self.tick_no]
            for _, fn in due:
                fn()

    def at(self, prm, bar):
        """The lane's value at a bar, as Live plays it back: the last point at or before it."""
        lane, beat = self.lanes[id(prm)], (bar - 1) * 4.0
        before = [b for b in sorted(lane) if b <= beat + 1e-9]
        return lane[before[-1]] if before else lane[min(lane)]


def demo_song():
    """BASS MID (EQ Eight), SERUM BASS (MIDI clip at bars 49-52), a rack, PAD / STAB, two Drums, returns."""
    bass = Track("SERUM BASS", [Device("Serum 2"), Device("Saturator", [Param("Drive", lambda v: "%.1f dB" % (v * 36))])],
                 [Clip("Bass A", 192.0, 208.0, [Note(38, 0.0, 0.45, 110), Note(38, 0.75, 0.2, 92), Note(33, 5.5, 0.4, 100)])])
    rack = Device("Drum Rack", [Param("Macro 1")], chains=[Chain("Kick", [Device("Simpler", [Param("Decay")])])])
    tracks = [Track("BASS MID", [eq_eight()]), bass, Track("DRUMS", [rack]), Track("PAD / STAB"),
              Track("Drums", audio=True), Track("Drums", audio=True)]
    returns = [Track("A-Reverb", sends=()), Track("B-Delay", sends=())]
    return Song(tracks, returns, [Cue("GROOVE A", 192.0), Cue("INTRO", 64.0)])
