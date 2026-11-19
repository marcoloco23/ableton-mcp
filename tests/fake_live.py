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

    def str_for_value(self, v):
        return self.value_items[int(v)] if self.is_quantized else self.show(v)


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
    def __init__(self, name):
        self.display_name = name


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
        self.available_output_routing_channels = self.available_input_routing_channels = []
        self.current_monitoring_state = 1
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
        self.scenes = [Scene("Intro"), Scene("Groove")]
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
        """A new track joins the group of the track before it, as TOP CLAP did after TOP LOOP in Live."""
        before = self.tracks[i - 1] if i > 0 else None
        if before is not None and before.is_grouped:
            t.is_grouped, t.group_track = True, before.group_track
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

    def stop_playing(self):
        self.is_playing = False


class Ctrl:
    """Runs a scheduled step at once, like Live's next tick."""

    def schedule_message(self, ticks, fn):
        fn()


def demo_song():
    """BASS MID (EQ Eight), SERUM BASS (MIDI clip at bars 49-52), a rack, PAD / STAB, two Drums, returns."""
    bass = Track("SERUM BASS", [Device("Serum 2"), Device("Saturator", [Param("Drive", lambda v: "%.1f dB" % (v * 36))])],
                 [Clip("Bass A", 192.0, 208.0, [Note(38, 0.0, 0.45, 110), Note(38, 0.75, 0.2, 92), Note(33, 5.5, 0.4, 100)])])
    rack = Device("Drum Rack", [Param("Macro 1")], chains=[Chain("Kick", [Device("Simpler", [Param("Decay")])])])
    tracks = [Track("BASS MID", [eq_eight()]), bass, Track("DRUMS", [rack]), Track("PAD / STAB"),
              Track("Drums", audio=True), Track("Drums", audio=True)]
    returns = [Track("A-Reverb", sends=()), Track("B-Delay", sends=())]
    return Song(tracks, returns, [Cue("GROOVE A", 192.0), Cue("INTRO", 64.0)])
