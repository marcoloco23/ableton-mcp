"""MIDI: get notes, quantize, transpose, duplicate clip, capture, groove."""

from __future__ import absolute_import, print_function, unicode_literals


def get_clip_notes(song, track_index, clip_index, ctrl=None):
    """Read all MIDI notes from a clip (legacy format)."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip
        if clip.is_audio_clip:
            raise Exception("Clip is not a MIDI clip")
        notes_data = clip.get_notes(0, 0, clip.length, 128)
        notes_list = []
        if notes_data and len(notes_data) > 0:
            notes_tuple = notes_data[0]
            for note in notes_tuple:
                note_dict = {
                    "pitch": note[0],
                    "start_time": note[1],
                    "duration": note[2],
                    "velocity": note[3],
                    "muted": note[4] if len(note) > 4 else False,
                }
                notes_list.append(note_dict)
        return {
            "clip_name": clip.name,
            "clip_length": clip.length,
            "note_count": len(notes_list),
            "notes": notes_list,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting clip notes: " + str(e))
        raise


def get_notes_from_clip(song, track_index, clip_index, ctrl=None):
    """Get all MIDI notes from a clip (extended format)."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip
        notes_data = clip.get_notes_extended(
            from_pitch=0, pitch_span=128, from_time=0, time_span=clip.length
        )
        notes = []
        if notes_data:
            for note in notes_data:
                notes.append({
                    "pitch": note.pitch,
                    "start_time": note.start_time,
                    "duration": note.duration,
                    "velocity": note.velocity,
                    "mute": note.mute,
                })
        return {
            "clip_name": clip.name,
            "clip_length": clip.length,
            "note_count": len(notes),
            "notes": notes,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting notes from clip: " + str(e))
        raise


def quantize_clip(song, track_index, clip_index, quantize_to, ctrl=None):
    """Quantize notes in a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip
        clip.quantize(quantize_to, 1.0)
        return {"quantized": True, "quantize_to": quantize_to}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error quantizing clip: " + str(e))
        raise


def transpose_clip(song, track_index, clip_index, semitones, ctrl=None):
    """Transpose notes in a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip
        clip.select_all_notes()
        notes = clip.get_selected_notes()
        new_notes = []
        for note in notes:
            pitch, time_val, duration, velocity, mute = note
            new_pitch = max(0, min(127, pitch + semitones))
            new_notes.append((new_pitch, time_val, duration, velocity, mute))
        clip.replace_selected_notes(tuple(new_notes))
        clip.deselect_all_notes()
        return {
            "transposed": True,
            "semitones": semitones,
            "note_count": len(new_notes),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error transposing clip: " + str(e))
        raise


def duplicate_clip(
    song, source_track, source_clip, dest_track, dest_clip, ctrl=None
):
    """Duplicate a clip."""
    try:
        if source_track < 0 or source_track >= len(song.tracks):
            raise IndexError("Source track index out of range")
        if dest_track < 0 or dest_track >= len(song.tracks):
            raise IndexError("Destination track index out of range")
        src_track = song.tracks[source_track]
        dst_track = song.tracks[dest_track]
        if source_clip < 0 or source_clip >= len(src_track.clip_slots):
            raise IndexError("Source clip index out of range")
        if dest_clip < 0 or dest_clip >= len(dst_track.clip_slots):
            raise IndexError("Destination clip index out of range")
        src_slot = src_track.clip_slots[source_clip]
        if not src_slot.has_clip:
            raise Exception("No clip in source slot")
        src_track.duplicate_clip_slot(source_clip)
        try:
            song.view.highlighted_clip_slot = src_track.clip_slots[
                source_clip + 1
            ]
            song.view.highlighted_clip_slot.clip.duplicate_loop()
        except Exception:
            pass
        return {"duplicated": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating clip: " + str(e))
        raise


def capture_midi(song, track_index, clip_index, ctrl=None):
    """Capture recently played MIDI into a clip slot."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not hasattr(song, "capture_midi"):
            raise Exception(
                "Capture MIDI is not available (requires Live 11 or later)"
            )
        song.capture_midi()
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "captured": True,
            "has_clip": clip_slot.has_clip,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error capturing MIDI: " + str(e))
        raise


def apply_groove(song, track_index, clip_index, groove_amount, ctrl=None):
    """Apply groove to a MIDI clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip
        if clip.is_audio_clip:
            raise Exception("Cannot apply groove to audio clips")
        if hasattr(clip, "groove_amount"):
            clip.groove_amount = groove_amount
        else:
            raise Exception("Groove amount not available on this clip")
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "groove_amount": groove_amount,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error applying groove: " + str(e))
        raise
