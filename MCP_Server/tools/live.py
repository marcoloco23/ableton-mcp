"""The live_* tools: whole jobs on the open Live set, by name and bar.

Each tool is one socket command of the same name, run inside Live by
AbletonMCP_Remote_Script/handlers/live.py (the escape hatch live_lom by lom.py), so a job
is one round trip and Live's own error comes back word for word.
"""

import json
import time
from typing import Any, Callable, Literal, TypedDict

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from MCP_Server.connection import AbletonError

INSTRUCTIONS = """Ableton Live, the set that is open right now. Conventions every live_* tool shares:
- Things are named, never numbered. A target is "Track / Device / Param", e.g. "BASS MID / EQ Eight / 1 Frequency A";
  a track setting is "Track / volume" (also pan, send A or "send A-Reverb", mute, solo, arm, output, output channel,
  input, input channel, monitor). Return tracks by name, the master as "Master". Racks: "DRUMS / Drum Rack / Kick / Simpler / Decay".
  Names match exactly, then ignoring case; "EQ Eight#2" is the second of two with one name. A miss lists the names there are.
- Bars are 1-based and song-absolute (bar 1 starts at the song's start), ranges inclusive: "49-52" is four bars.
  Notes are [bar, beat, pitch, length, velocity]: beat 1 is the downbeat, 2.5 the eighth after beat 2, a 16th step s is beat 1 + s/4; length is in beats.
- Values are what Live shows: "-6 dB", "-inf dB", "110 Hz", "4.5 kHz", "1.80 s", "40 %", "25L", "C", "High Pass 48dB", "On".
  A bare number is Live's raw value; prefer the shown text.
- Session view: name the scene, or give its number (1 is the top scene). With scene=, the notes tools read and write
  that slot's clip, its bars counted from the clip's start; live_scenes lists them, live_clips("to_arrangement") copies them.
- Start with live_overview to know the set, live_track to see one track's devices and settings. Use live_lom only for what no other tool covers.
"""

Format = Literal["concise", "detailed"]


class Change(TypedDict):
    target: str
    value: str | float | bool


class Lane(TypedDict, total=False):
    target: str
    points: list[list[Any]]
    shape: Literal["linear", "ease", "ease-log"]


def _read(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _write(title: str, destructive: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=False, destructiveHint=destructive, idempotentHint=idempotent,
                           openWorldHint=False)


def _grid_rows(grid: str, start_bar: int) -> list[list[float]]:
    """Grid notation (4/4, 16 steps a bar) -> note rows from start_bar."""
    from MCP_Server.grid_notation import parse_grid

    rows = []
    for n in parse_grid(grid):
        at = float(n["start_time"])
        rows.append([start_bar + int(at // 4), round(at % 4 + 1, 6), n["pitch"], n["duration"], n.get("velocity", 100)])
    return rows


def _grid_text(rows: list[list[Any]], first_bar: float) -> str:
    from MCP_Server.grid_notation import notes_to_grid

    notes = [{"pitch": p, "start_time": (bar - first_bar) * 4 + beat - 1, "duration": length, "velocity": vel}
             for bar, beat, p, length, vel in rows]
    return notes_to_grid(notes)


# Jobs Live may work on for up to a minute (the Remote Script's dispatch.timeout); the rest answer in 10 s.
LONG = ("lom_batch", "live_overview", "live_track", "live_notes_read", "live_notes_write", "live_clips", "live_load")


def register(mcp: Any, get_ableton_connection: Callable[[], Any]) -> None:
    def call(command: str, **params: Any) -> Any:
        body = {k: v for k, v in params.items() if v is not None}
        try:
            return get_ableton_connection().send_command(command, body, timeout=65.0 if command in LONG else 15.0)
        except AbletonError as e:
            msg = str(e)
            if msg.startswith("Timeout") and "read the state" not in msg:
                msg += ". Live may still be finishing it; read the state before retrying."
            raise ToolError(msg) from e
        except TimeoutError as e:
            raise ToolError(str(e)) from e
        except Exception as e:
            raise ToolError(f"Live did not answer ({e}). Is Live open with AbletonMCP on as a control surface?") from e

    def out(result: Any) -> str:
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool(annotations=_read("Read the whole set"))
    def live_overview(ctx: Context, response_format: Format = "concise") -> str:
        """The whole open set in one call: tempo, loop, the sections (from the locators) with the tracks
        playing in each, and every track with its group, mute/solo/arm/frozen flags, volume as shown,
        output, device names and the bars its clips cover; then return tracks and the master.
        Read this before saying anything about the set.

        response_format "concise" gives clip bars as merged spans ("17-48, 65-208"); "detailed" lists each
        clip by name and bars, plus pan and sends per track.

        Examples:
          live_overview()                              -> what plays in BREAK, which tracks are muted
          live_overview(response_format="detailed")    -> every clip's name and bars
        """
        return out(call("live_overview", response_format=response_format))

    @mcp.tool(annotations=_read("Read one track or device"))
    def live_track(ctx: Context, track: str, response_format: Format = "concise") -> str:
        """One track in depth: volume, pan and sends as shown, routing (output, input, monitor), every
        device with every parameter's shown value (racks with their chains), and its clips' bars.
        Name a device after the track to read only that device.

        "concise" gives each device's parameters as {name: shown value}; "detailed" adds raw value,
        min, max and, for switches and menus, the items to choose from.

        Examples:
          live_track("BASS MID")                                        -> EQ, mixer, routing, clips
          live_track("SERUM BASS / Saturator")                          -> every Saturator setting
          live_track("BASS MID / EQ Eight", response_format="detailed") -> filter types to choose from
        """
        return out(call("live_track", track=track, response_format=response_format))

    @mcp.tool(annotations=_write("Set parameters and track settings", destructive=False, idempotent=True))
    def live_set(ctx: Context, changes: list[Change]) -> str:
        """Set any number of parameters and track settings in one round trip, by the value Live shows.
        Every target and value is checked first; if one is wrong nothing is changed and each problem is
        listed. Returns each target's shown value before and after (keep it to undo).

        target is "Track / Device / Param" or "Track / <setting>" with setting volume, pan, send A (or the
        return's name), mute, solo, arm (on/off), output, output channel, input, input channel (an option
        Live lists, e.g. "Resampling", "🎸 BASS", "Post FX") or monitor (In/Auto/Off). "Track / Device"
        with on/off switches the device. Tempo and loop are in live_transport.

        Examples:
          live_set([{"target": "BASS MID / EQ Eight / 1 Filter Type A", "value": "High Pass 48dB"},
                    {"target": "BASS MID / EQ Eight / 1 Frequency A", "value": "110 Hz"},
                    {"target": "BASS MID / volume", "value": "-6 dB"}])
          live_set([{"target": "PAD / send A-Reverb", "value": "-12 dB"}, {"target": "PAD / pan", "value": "15L"}])
          live_set([{"target": "RESAMPLE / input", "value": "Resampling"}, {"target": "RESAMPLE / monitor", "value": "Off"}])
        """
        return out(call("live_set", changes=changes))

    @mcp.tool(annotations=_read("Read MIDI notes in bars"))
    def live_notes_read(ctx: Context, track: str, bars: str | None = None, scene: str | None = None,
                        as_grid: bool = False, response_format: Format = "concise") -> str:
        """The MIDI notes on a track's arrangement clips, in bars: rows of [bar, beat, pitch, length,
        velocity] (bar song-absolute, beat 1 the downbeat, length in beats), plus the clips they sit in.
        bars "49-52" limits the read (inclusive); without it, the whole track. A looped clip is unrolled:
        its repeats come back as the notes that play there.

        With scene (its name, or its number from 1) it reads the session clip in that slot instead, and
        bars count from the clip's start (bar 1).

        "detailed" gives each note as a dict with its clip and any non-default mute, probability,
        velocity_deviation or release_velocity. as_grid=True (needs bars, 4/4) draws the notes as grid
        notation instead, handy for drums.

        Examples:
          live_notes_read("SERUM BASS", bars="49-52")
          live_notes_read("DRUMS", bars="65-66", as_grid=True)
          live_notes_read("SUB", scene="Groove")         -> the session clip in scene Groove
        """
        if as_grid and not bars:
            raise ToolError("as_grid needs bars, e.g. bars=\"65-66\"")
        result = call("live_notes_read", track=track, bars=bars, scene=scene,
                      response_format="concise" if as_grid else response_format)
        if as_grid:
            first = float(str(bars).split("-")[0])
            return f"{result['track']} bars {result['bars']}\n\n{_grid_text(result['notes'], first)}"
        return out(result)

    @mcp.tool(annotations=_write("Write MIDI notes in bars", destructive=True, idempotent=False))
    def live_notes_write(ctx: Context, track: str, start_bar: int, bars: int,
                         notes: list[list[float] | dict[str, Any]] | None = None, grid: str | None = None,
                         mode: Literal["replace", "add"] = "replace", clip_name: str | None = None,
                         scene: str | None = None) -> str:
        """Write MIDI notes into bars start_bar … start_bar+bars-1 of a MIDI track, in one call.
        If no clip covers those bars, a clip is created over exactly them; clips that already cover them
        are written into. A clip covering only part of them is an error naming it (delete or move it with
        live_clips first), and so are bars that are only a looped clip's repeats. mode "replace" clears
        those bars before writing; "add" keeps what is there. clip_name names the clip(s). Every note must
        fall inside the bars, with pitch 0-127, length above 0 and velocity 1-127; nothing is changed
        until all of it checks out.

        With scene (name, or number from 1) it writes the session clip in that slot, creating one when the
        slot is empty; bars then count from the clip's start (bar 1).

        notes: rows [bar, beat, pitch, length, velocity] with song-absolute bars (what live_notes_read
        returns), or dicts with those keys. grid: grid notation instead of notes (4/4, starts at start_bar).

        Examples:
          live_notes_write("SUB", start_bar=33, bars=2, clip_name="Bass call/answer",
                           notes=[[33, 1, 38, 0.45, 110], [33, 1.75, 38, 0.2, 92], [33, 2.5, 33, 0.4, 100],
                                  [34, 1, 38, 0.45, 108], [34, 2.5, 36, 0.4, 100], [34, 4.5, 40, 0.4, 102]])
          live_notes_write("DRUMS", start_bar=65, bars=1, grid="KK|o---o---o---o---|\\nHC|--x---x---x---x-|")
          live_notes_write("BASS", start_bar=1, bars=2, scene="Groove", notes=[[1, 1, 38, 0.45, 110], [2, 2.5, 36, 0.4, 100]])
        """
        if (notes is None) == (grid is None):
            raise ToolError("give notes or grid, one of the two")
        rows = _grid_rows(grid, start_bar) if grid is not None else notes
        return out(call("live_notes_write", track=track, start_bar=start_bar, bars=bars, notes=rows, mode=mode,
                        clip_name=clip_name, scene=scene))

    @mcp.tool(annotations=_write("Edit clips", destructive=True, idempotent=False))
    def live_clips(ctx: Context,
                   action: Literal["duplicate", "move", "delete", "mute", "unmute", "rename", "to_arrangement"],
                   bars: str | None = None, scene: str | None = None, track: str | None = None,
                   to_bar: int | None = None, times: int = 1, name: str | None = None) -> str:
        """Act on clips on one track, or on every track when track is left out. With bars ("49-52",
        inclusive): the arrangement clips that START in those bars. With scene (name, or number from 1):
        the session clips in that scene.

        duplicate copies arrangement clips to to_bar, `times` copies back to back (the bars' length apart);
        move copies once to to_bar and deletes the originals (bars may not overlap); delete; mute / unmute;
        rename gives each clip `name`. to_arrangement takes scene and bars: it fills those arrangement bars
        with the scene's clips, each repeated back to back at its own length (exact, no real-time
        recording); a copy that would run past the bars is left out and listed. Returns the clips it acted
        on. Cmd+Z in Live undoes each step.

        Examples:
          live_clips("mute", bars="113-176", track="BASS STK")
          live_clips("duplicate", bars="1-8", to_bar=17, times=24)          -> every track's 8-bar loop over 17-208
          live_clips("to_arrangement", scene="DROP 1", bars="89-112")      -> the DROP 1 scene over its section
        """
        return out(call("live_clips", action=action, bars=bars, scene=scene, track=track, to_bar=to_bar, times=times,
                        name=name))

    @mcp.tool(annotations=_write("Session scenes", destructive=True, idempotent=False))
    def live_scenes(ctx: Context, action: Literal["list", "create", "rename", "fire", "delete"] = "list",
                    scene: str | None = None, name: str | None = None) -> str:
        """The session view's scenes, by name or by number as Live shows it (1 is the top scene).
        list gives each scene's number, name and the tracks with a clip in it; create adds a scene
        after `scene` (or at the end) named `name`; rename; fire launches it; delete. A session clip's
        notes are live_notes_read / live_notes_write with scene=; copying a scene into the arrangement
        is live_clips("to_arrangement").

        Examples:
          live_scenes()
          live_scenes("create", name="Groove")
          live_scenes("fire", scene="Groove")
        """
        return out(call("live_scenes", action=action, scene=scene, name=name))

    @mcp.tool(annotations=_write("Create, duplicate, delete or rename tracks", destructive=True, idempotent=False))
    def live_tracks(ctx: Context, action: Literal["create", "duplicate", "delete", "rename"], track: str | None = None,
                    name: str | None = None, kind: Literal["midi", "audio", "return"] = "midi") -> str:
        """Create, duplicate, delete or rename a track by name.

        create makes a `kind` track named `name`, right after `track` (or at the end); duplicate copies
        `track` with its devices and clips, the copy named `name`; delete removes `track`; rename names
        `track` `name`. Routing and mixer settings are in live_set. Live's API cannot group tracks.

        Examples:
          live_tracks("duplicate", track="BASS MID", name="BASS MID 2")
          live_tracks("create", kind="audio", track="DRUMS", name="RESAMPLE")
          live_tracks("rename", track="Audio 3", name="TOPS")
        """
        return out(call("live_tracks", action=action, track=track, name=name, kind=kind))

    @mcp.tool(annotations=_write("Transport, tempo and loop", destructive=False, idempotent=True))
    def live_transport(ctx: Context, play: bool | None = None, position_bar: float | None = None,
                       loop_bars: str | None = None, loop: bool | None = None, tempo: float | None = None,
                       metronome: bool | None = None, record: bool | None = None) -> str:
        """Set any of tempo, loop brace (bars, inclusive), loop on/off, metronome, playhead (bar), arrangement
        record on/off and play/stop, then report the transport: playing, recording, position bar, tempo,
        signature, loop, metronome. All arguments are checked before any is applied. Called with nothing,
        it only reports.

        Examples:
          live_transport()                                        -> is it playing, where, what tempo
          live_transport(loop_bars="17-208", loop=True, position_bar=17, play=True)
          live_transport(play=False, record=False)               -> stop, and Arrangement Record off
        """
        return out(call("live_transport", play=play, position_bar=position_bar, loop_bars=loop_bars, loop=loop,
                        tempo=tempo, metronome=metronome, record=record))

    @mcp.tool(annotations=_write("Arrangement locators", destructive=True, idempotent=True))
    def live_locators(ctx: Context, action: Literal["list", "set", "delete"] = "list", bar: float | None = None,
                      name: str | None = None, locator: str | None = None) -> str:
        """List, add, rename or delete the arrangement locators (the section markers live_overview reads).

        set with bar and name names the locator at that bar, adding one if there is none; set with
        locator (its current name) and name renames it. delete takes bar or locator. Live adds and removes
        locators at the playhead, so adding or deleting needs playback stopped. Returns all locators.

        Examples:
          live_locators()
          live_locators("set", bar=97, name="BREAK")
          live_locators("set", locator="BREAK", name="BREAK 1")
        """
        result = call("live_locators", action=action, bar=bar, name=name, locator=locator)
        if "pending" not in result:
            return out(result)
        time.sleep(0.3)
        listed = call("live_locators", action="list")
        here = [x for x in listed["locators"] if abs(x["bar"] - result["bar"]) < 1e-6]
        if (action == "set") != bool(here) or (action == "set" and name and here[0]["name"] != name):
            raise ToolError(f"Live did not {'add' if action == 'set' else 'delete'} the locator at bar "
                            f"{result['bar']:g}; locators now: {listed['locators']}")
        return out(listed)

    @mcp.tool(annotations=_read("Search Live's browser"))
    def live_browser_find(ctx: Context, name: str = "", contains: str = "", roots: list[str] | None = None,
                          limit: int = 10) -> str:
        """Search Live's browser inside Live: by exact name, or by a case-insensitive part of a name.
        roots: plugins, audio_effects, instruments, midi_effects, user_folders, packs (default all six).
        Each match gives a path that live_load takes as item.

        Examples:
          live_browser_find(name="Serum 2")
          live_browser_find(contains="tape", roots=["audio_effects", "plugins"])
        """
        return out(call("browser_find", name=name or None, contains=contains or None, roots=roots, limit=limit))

    @mcp.tool(annotations=_write("Load a device, plugin or preset", destructive=False, idempotent=False))
    def live_load(ctx: Context, track: str, item: str, scene: str | None = None, bar: float | None = None,
                  clip: dict[str, Any] | None = None) -> str:
        """Load something onto a track.

        Without scene or bar: a device, plugin, preset or sample from Live's browser, at the end of the
        track's chain (Live inserts after the selected device, so the last one is selected first). Found by
        exact name inside Live, searching the first few thousand items a few folders deep, or by any path
        from live_browser_find. Returns the track's devices after loading.

        With scene (name, or number from 1) or bar: item is the absolute path of an audio file, placed on
        an audio track as a new clip in that empty session slot, or in the arrangement starting at that bar.
        clip sets the new clip's properties in the same call, by their Live names: pitch_coarse
        (semitones), pitch_fine (cents), warping (true/false), warp_mode, gain, name, looping. If Live
        refuses one, the new clip is removed again and the error says which.

        Examples:
          live_load("BASS MID", "EQ Eight")
          live_load("PERC", "/Users/me/Splice/perc_loop_126.wav", scene="DROP 1", clip={"pitch_coarse": -2, "warping": True})
          live_load("VOX", "/Users/me/Stems/vox_chop.wav", bar=97, clip={"name": "Vox chop"})
        """
        return out(call("live_load", track=track, item=item, scene=scene, bar=bar, clip=clip))

    @mcp.tool(annotations=_write("Set up a Simpler", destructive=True, idempotent=True))
    def live_simpler(ctx: Context, simpler: str, sample: str | None = None, start_ms: float | None = None,
                     end_ms: float | None = None, warp: bool | None = None,
                     mode: Literal["classic", "one-shot", "slicing"] | None = None, fade_in: str | None = None,
                     fade_out: str | None = None, trigger: Literal["Trigger", "Gate"] | None = None) -> str:
        """Set what a Simpler plays: load a sample file into it, the visible start/end in ms (S Start and
        S Length go back to 0 / 100 % so nothing is cut twice), warp, mode, fades ("80 ms") and trigger.
        sample= replaces the Simpler's sample; Simpler keeps its own Volume. Returns what it now shows.

        Examples:
          live_simpler("KICK / Simpler", sample="/Users/me/Samples/kick 04.wav", mode="one-shot", fade_out="0.10 ms")
          live_simpler("VOX / Simpler", start_ms=1200, end_ms=2650, warp=False)
        """
        return out(call("simpler_region", target=simpler, sample=sample, start_ms=start_ms, end_ms=end_ms, warp=warp,
                        mode=mode, fade_in=fade_in, fade_out=fade_out, trigger=trigger))

    @mcp.tool(annotations=_write("Record arrangement automation", destructive=True, idempotent=True))
    def live_automate(ctx: Context, lanes: list[Lane]) -> str:
        """Record automation into the arrangement: each lane is a target (a parameter, "Track / volume",
        "Track / pan" or "Track / send A"), points [[bar, value], …] with values as Live shows them, and a
        shape: linear (default), ease, or ease-log (for frequencies). Live plays only the bars that move,
        with Arrangement Record and Automation Arm on, moving the knobs the way Push writes automation;
        Cmd+Z undoes it. Returns at once: poll live_automate_status until state is done.

        Examples:
          live_automate([{"target": "PAD / Auto Filter / Frequency", "points": [[89, "900 Hz"], [97, "20 kHz"]],
                          "shape": "ease-log"}])
          live_automate([{"target": "TOPS / volume", "points": [[96, "0 dB"], [97, "-inf dB"]]}])
        """
        return out(call("automate", lanes=lanes))

    @mcp.tool(annotations=_read("Automation recording progress"))
    def live_automate_status(ctx: Context) -> str:
        """How live_automate is doing: state running | done | error, the spans (bars) it records and which one
        is recording now.

        Example: live_automate_status()  -> {"state": "done", ...}
        """
        return out(call("automate_status"))

    @mcp.tool(annotations=_write("Anything else in Live's object model", destructive=True, idempotent=False))
    def live_lom(ctx: Context, op: Literal["get", "set", "call", "batch"], path: str | None = None,
                 value: Any = None, value_path: str | None = None, args: list[Any] | None = None,
                 fields: list[str] | None = None, ops: list[dict[str, Any]] | None = None,
                 continue_on_error: bool = False) -> str:
        """The escape hatch: Live's object model by path, for what no other live_* tool covers.

        A path walks from the Song ("app." from the Application): dotted attributes, numbers for
        positions, [Name] for an element by name (a dot or slash inside brackets is part of the name),
        [@49] for the arrangement clip at bar 49. An attribute of a list reads it on every element.
        get reads (a parameter comes with its shown value; fields reads several things per element);
        set writes a property (a parameter path takes a shown value; an option property such as
        output_routing_type takes the option's name; value_path assigns another object); call runs a
        method with args ({"$path": ...} passes an object); batch runs ops in one round trip, each op
        {"op": "get"|"set"|"call"|"set_param" or any live_* command name, ...its fields}.

        Examples:
          live_lom("get", path="tracks", fields=["name", "is_frozen", "output_routing_type.display_name"])
          live_lom("set", path="tracks[SUB].arrangement_clips[@113].color_index", value=12)
          live_lom("call", path="tracks[BASS MID].delete_device", args=[2])
          live_lom("batch", ops=[{"op": "call", "path": "undo"}, {"op": "get", "path": "tracks[PAD].devices.name"}])
        """
        if op == "batch":
            if not ops:
                raise ToolError("batch needs ops")
            return out(call("lom_batch", ops=ops, continue_on_error=continue_on_error))
        if not path:
            raise ToolError(f"{op} needs path")
        if op == "get":
            return out(call("lom_get", path=path, fields=fields))
        if op == "set":
            if value is None and not value_path:
                raise ToolError("set needs value (or value_path to assign another object)")
            body = {"path": path, **({"value_path": value_path} if value_path else {"value": value})}
            return out(call("lom_set", **body))
        return out(call("lom_call", path=path, args=args or []))

    @mcp.tool(annotations=_write("Reload the Remote Script handlers", destructive=False, idempotent=True))
    def live_reload_handlers(ctx: Context) -> str:
        """Reload the Remote Script's handler code inside Live after deploy.sh copied a new version,
        without restarting Live. Run it only when asked to deploy.

        Example: live_reload_handlers()
        """
        return out(call("reload_handlers"))
