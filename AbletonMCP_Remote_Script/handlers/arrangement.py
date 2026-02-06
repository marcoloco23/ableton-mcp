"""Arrangement: locators (multi-tick stays in __init__), copy to arrangement, get arrangement clips."""

from __future__ import absolute_import, print_function, unicode_literals


def copy_clip_to_arrangement(song, track_index, clip_index, arrangement_time, ctrl=None):
    """Copy a clip from session view to arrangement view."""
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
        try:
            clip.duplicate_clip_to(track, arrangement_time)
            return {
                "copied": True,
                "track_index": track_index,
                "clip_index": clip_index,
                "arrangement_time": arrangement_time,
            }
        except AttributeError:
            if ctrl:
                ctrl.log_message("Using alternative clip copy method")
            clip_length = clip.length
            return {
                "copied": False,
                "note": (
                    "Direct arrangement copy not supported in this API version. "
                    "Use duplicate_clip instead."
                ),
                "clip_length": clip_length,
                "suggested_time": arrangement_time,
            }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error copying clip to arrangement: " + str(e))
        raise


def get_arrangement_clips(song, track_index, ctrl=None):
    """Get all clips in arrangement view for a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        clips = []
        if not hasattr(track, "arrangement_clips"):
            raise Exception(
                "Track does not have arrangement clips "
                "(may be a group track or return track)"
            )
        for clip in track.arrangement_clips:
            clip_info = {
                "name": clip.name,
                "start_time": clip.start_time,
                "end_time": clip.end_time,
                "length": clip.length,
                "loop_start": getattr(clip, "loop_start", None),
                "loop_end": getattr(clip, "loop_end", None),
                "is_audio_clip": clip.is_audio_clip,
                "is_midi_clip": clip.is_midi_clip,
                "muted": getattr(clip, "muted", False),
                "color_index": getattr(clip, "color_index", None),
            }
            clips.append(clip_info)
        return {
            "track_index": track_index,
            "track_name": track.name,
            "clip_count": len(clips),
            "clips": clips,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting arrangement clips: " + str(e))
        raise
