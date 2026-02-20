"""Dynamic command dispatch — hot-reloadable without restarting Ableton.

Add new commands here instead of editing __init__.py.
Toggle control surface off/on to pick up changes.
"""

from __future__ import absolute_import, print_function, unicode_literals

from . import tracks, browser, devices, arrangement, audio


def _resolve_track(song, track_index, track_type):
    """Resolve a track object from index and type."""
    if track_type == "return":
        if track_index < 0 or track_index >= len(song.return_tracks):
            raise IndexError("Return track index out of range")
        return song.return_tracks[track_index]
    elif track_type == "master":
        return song.master_track
    else:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        return song.tracks[track_index]


def _get_track_routing(song, p, ctrl):
    """Get output routing info for a track."""
    track_index = p.get("track_index", 0)
    track_type = p.get("track_type", "track")
    track = _resolve_track(song, track_index, track_type)

    current_type = None
    current_channel = None
    try:
        rt = track.output_routing_type
        current_type = rt.display_name if rt else None
    except Exception:
        pass
    try:
        rc = track.output_routing_channel
        current_channel = rc.display_name if rc else None
    except Exception:
        pass

    available_types = []
    try:
        for rt in track.available_output_routing_types:
            available_types.append(rt.display_name)
    except Exception:
        pass

    available_channels = []
    try:
        for rc in track.available_output_routing_channels:
            available_channels.append(rc.display_name)
    except Exception:
        pass

    return {
        "index": track_index,
        "name": track.name,
        "track_type": track_type,
        "output_routing_type": current_type,
        "output_routing_channel": current_channel,
        "available_output_routing_types": available_types,
        "available_output_routing_channels": available_channels,
    }


def _set_track_routing(song, p, ctrl):
    """Set output routing for a track by display name."""
    track_index = p.get("track_index", 0)
    track_type = p.get("track_type", "track")
    output_type = p.get("output_type", None)
    output_channel = p.get("output_channel", None)
    track = _resolve_track(song, track_index, track_type)

    result = {"index": track_index, "name": track.name, "track_type": track_type}

    if output_type is not None:
        found = False
        for rt in track.available_output_routing_types:
            if rt.display_name == output_type:
                track.output_routing_type = rt
                result["output_routing_type"] = rt.display_name
                found = True
                break
        if not found:
            available = [rt.display_name for rt in track.available_output_routing_types]
            raise ValueError(
                "Output type '{0}' not found. Available: {1}".format(
                    output_type, ", ".join(available)
                )
            )

    if output_channel is not None:
        found = False
        for rc in track.available_output_routing_channels:
            if rc.display_name == output_channel:
                track.output_routing_channel = rc
                result["output_routing_channel"] = rc.display_name
                found = True
                break
        if not found:
            available = [rc.display_name for rc in track.available_output_routing_channels]
            raise ValueError(
                "Output channel '{0}' not found. Available: {1}".format(
                    output_channel, ", ".join(available)
                )
            )

    return result


def _move_device(song, p, ctrl):
    """Move a device to a new position on a track."""
    track_index = p.get("track_index", 0)
    device_index = p.get("device_index", 0)
    new_position = p.get("new_position", 0)
    track_type = p.get("track_type", "track")
    track = devices.resolve_track(song, track_index, track_type)
    device_list = list(track.devices)
    if device_index < 0 or device_index >= len(device_list):
        raise IndexError("Device index out of range")
    if new_position < 0 or new_position >= len(device_list):
        raise IndexError("New position out of range")
    device = device_list[device_index]
    name = device.name
    track.move_device(device, new_position)
    return {
        "moved": name,
        "from": device_index,
        "to": new_position,
        "track_type": track_type,
    }


# Registry of dynamically dispatched commands.
# Key: command name, Value: (handler_func, param_extractor, needs_main_thread)
def _get_registry():
    """Build registry fresh each call so hot-reload picks up changes."""
    return {
        "set_return_track_name": {
            "handler": lambda song, p, ctrl: tracks.set_return_track_name(
                song, p.get("return_index", 0), p.get("name", ""), ctrl
            ),
            "modifying": True,
        },
        "load_on_return_track": {
            "handler": lambda song, p, ctrl: browser.load_on_return_track(
                song, p.get("return_index", 0), p.get("uri", ""), ctrl
            ),
            "modifying": True,
        },
        "move_device": {
            "handler": lambda song, p, ctrl: _move_device(song, p, ctrl),
            "modifying": True,
        },
        "get_track_meters": {
            "handler": lambda song, p, ctrl: tracks.get_track_meters(
                song,
                p.get("track_indices", None),
                p.get("include_returns", False),
                p.get("include_master", False),
                ctrl,
            ),
            "modifying": False,
        },
        "inspect_arrangement_clip": {
            "handler": lambda song, p, ctrl: arrangement.inspect_arrangement_clip(
                song,
                p.get("track_index", 0),
                p.get("arrangement_clip_index", 0),
                ctrl,
            ),
            "modifying": False,
        },
        "get_all_clip_gains": {
            "handler": lambda song, p, ctrl: audio.get_all_clip_gains(
                song, p.get("track_indices", None), ctrl
            ),
            "modifying": False,
        },
        "set_clip_gain": {
            "handler": lambda song, p, ctrl: audio.set_clip_gain(
                song,
                p.get("track_index", 0),
                p.get("clip_index", 0),
                p.get("gain", 0.5),
                ctrl,
            ),
            "modifying": True,
        },
        "copy_arrangement_to_session": {
            "handler": lambda song, p, ctrl: arrangement.copy_arrangement_to_session(
                song,
                p.get("track_index", 0),
                p.get("arrangement_clip_index", 0),
                p.get("clip_slot_index", 0),
                ctrl,
            ),
            "modifying": True,
        },
        "get_group_structure": {
            "handler": lambda song, p, ctrl: tracks.get_group_structure(song, ctrl),
            "modifying": False,
        },
        "relocate_track": {
            "handler": lambda song, p, ctrl: tracks.relocate_track(
                song,
                p.get("source_index", 0),
                p.get("target_index", 0),
                p.get("device_uris", None),
                ctrl,
            ),
            "modifying": True,
        },
        "move_to_group": {
            "handler": lambda song, p, ctrl: tracks.move_to_group(
                song,
                p.get("track_name", None),
                p.get("track_index", None),
                p.get("group_name", ""),
                p.get("position", "last"),
                p.get("device_uris", None),
                ctrl,
            ),
            "modifying": True,
        },
        "get_track_routing": {
            "handler": lambda song, p, ctrl: _get_track_routing(song, p, ctrl),
            "modifying": False,
        },
        "set_track_routing": {
            "handler": lambda song, p, ctrl: _set_track_routing(song, p, ctrl),
            "modifying": True,
        },
    }


def is_known(command_type):
    """Check if this command is in the dynamic registry."""
    return command_type in _get_registry()


def is_modifying(command_type):
    """Check if this command modifies state (needs main thread)."""
    reg = _get_registry()
    if command_type in reg:
        return reg[command_type].get("modifying", False)
    return False


def execute(command_type, params, song, ctrl):
    """Execute a dynamically registered command."""
    reg = _get_registry()
    if command_type not in reg:
        raise ValueError("Unknown dynamic command: " + command_type)
    return reg[command_type]["handler"](song, params, ctrl)
