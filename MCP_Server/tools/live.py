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

from MCP_Server import live_memory
from MCP_Server.connection import AbletonError

INSTRUCTIONS = """Ableton Live, the set that is open right now. Conventions every live_* tool shares:
- Things are named, never numbered. A target is "Track / Device / Param", e.g. "BASS MID / EQ Eight / 1 Frequency A";
  a track setting is "Track / volume" (also pan, send A or "send A-Reverb", mute, solo, arm, output, output channel,
  input, input channel, monitor). Return tracks by name, the master as "Master". Racks: "DRUMS / Drum Rack / Kick / Simpler / Decay".
  Names match exactly, then ignoring case; "EQ Eight#2" is the second of two with one name. A miss lists the names there are.
- Bars are 1-based and song-absolute (bar 1 starts at the song's start), ranges inclusive: "49-52" is four bars.
  A span that does not start and end on bar lines is shown as positions, 2 decimals, end exclusive: "97-97.07".
  Notes are [bar, beat, pitch, length, velocity]: beat 1 is the downbeat, 2.5 the eighth after beat 2, a 16th step s is beat 1 + s/4; length is in beats.
- Values are what Live shows: "-6 dB", "-inf dB", "110 Hz", "4.5 kHz", "1.80 s", "40 %", "25L", "C", "High Pass 48dB", "On".
  A bare number is Live's raw value; prefer the shown text.
- Session view: name the scene, or give its number (1 is the top scene). With scene=, the notes tools read and write
  that slot's clip, its bars counted from the clip's start; live_scenes lists them, live_clips("to_arrangement") copies them.
- Start with live_overview to know the set (its "audit" lines say what will trip the work up; live_audit adds the sidechain map),
  live_track to see one track's devices and settings. Use live_lom only for what no other tool covers.
"""

Format = Literal["concise", "detailed"]


class Change(TypedDict):
    target: str
    value: str | float | bool


class Lane(TypedDict, total=False):
    target: str
    points: list[list[Any]]
    shape: Literal["linear", "ease", "ease-log"]
    clear: str
    value: str | float


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
LONG = ("lom_batch", "live_overview", "live_audit", "live_track", "live_notes_read", "live_notes_write", "live_clips", "live_load")


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

    def saved_set(job: Callable[..., Any], *args: Any) -> str:
        """A job on the saved set (MCP_Server/saved_set.py): it reads Live, never sets anything there."""
        from MCP_Server import saved_set as S

        try:
            return out(job(S, get_ableton_connection(), *args))
        except S.Refused as e:
            raise ToolError(str(e)) from e

    @mcp.tool(annotations=_read("Read the whole set"))
    def live_overview(ctx: Context, response_format: Format = "concise") -> str:
        """The whole open set in one call: tempo, loop, the sections (from the locators) with the tracks
        heard in each, and every track with its group, mute/solo/arm/frozen flags, volume as shown,
        output, device names and the bars its clips cover; then return tracks and the master.
        Read this before saying anything about the set.

        What sounds where, as Live plays it: a track whose input is another track and whose monitor is In
        (or Auto while armed, where it plays no clip) says plays_via, input and monitor, and is heard
        wherever its source plays (Resampling: wherever anything plays). Monitor In silences a track's own
        clips. A MIDI clip sounds only in the bars its notes play (an emptied clip plays nothing), an audio clip
        over its span; "detailed" gives each track's "sounds" in bars. Muted tracks and tracks in a muted group
        are not heard. When anything is soloed, "warning"
        and "soloed" come first, the tracks Live silences via solo carry the flag "silenced by solo", and
        each section lists them under silenced_by_solo, not playing. "audit" then lists, one line each, what
        live_audit flags (duplicate names, clips under a 16th or past END, leftovers, mutes, unused seats).

        response_format "concise" gives clip bars as merged spans ("17-48, 65-208"); "detailed" lists each
        clip by name and bars, plus pan and sends per track.

        Examples:
          live_overview()                              -> what plays in BREAK, which tracks are muted
          live_overview(response_format="detailed")    -> every clip's name and bars
        """
        return out(call("live_overview", response_format=response_format))

    @mcp.tool(annotations=_read("Audit the set before work"))
    def live_audit(ctx: Context) -> str:
        """What in the open set will trip the work up, in one read-only pass; run it before any work.
        issues, one line each: tracks sharing a name (live_track and the recorder refuse it; the line gives
        their positions and the 'HOOK#2' form that addresses each), arrangement clips under a 16th (all but
        silent), clips past the END locator and a missing START or END locator, leftover REC / CAND /
        BUILD LOOP tracks and CANDIDATES scenes, a solo and what it silences, muted tracks, and tracks with
        an instrument loaded (Simpler, Sampler, a rack, a plug-in instrument) but no clip in the arrangement:
        a seat to use before adding a track. sidechain: every Kickstart and every device with its sidechain
        on (S/C On), grouped by what keys it; keyed_by is null where Live's API hides the input (plug-ins,
        Glue Compressor). live_overview carries the same issue lines as "audit".

        Examples:
          live_audit()   -> {"issues": [{"check": "duplicate names", "text": "3 tracks named 'HOOK' (tracks 37, 38, 39): ..."}],
                             "sidechain": [{"device": "Compressor", "keyed_by": "👊 KICK / Post FX", "tracks": ["PAD"]}]}
        """
        return out(call("live_audit"))

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

    @mcp.tool(annotations=_write("Run a whole job in one undo step", destructive=True, idempotent=False))
    def live_batch(ctx: Context, jobs: list[dict], preview: bool = False) -> str:
        """Do a whole job in one call and one undo step: one Cmd+Z in Live takes all of it back.

        jobs, in order: {"do": "live_set" | "live_notes_write" | "live_clips" | "live_tracks" | "live_locators" |
        "live_load" | "live_warp" | "simpler_region" | "live_transport", ...that tool's own arguments}. Every job is
        checked first; if one is wrong nothing is done and each problem is listed. preview=True stops after the check
        and says what each job will do. If Live refuses a job midway, the jobs before it stay done (and named).

        Examples:
          live_batch([{"do": "live_notes_write", "track": "SUB", "start_bar": 33, "bars": 1, "notes": [[33, 1, 38, 0.5, 100]]},
                      {"do": "live_set", "changes": [{"target": "SUB / volume", "value": "-6 dB"}]}], preview=True)
        """
        return out(call("live_batch", jobs=jobs, preview=preview))

    @mcp.tool(annotations=_read("Read MIDI notes in bars"))
    def live_notes_read(ctx: Context, track: str, bars: str | None = None, scene: str | int | None = None,
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
                         scene: str | int | None = None) -> str:
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
                   action: Literal["duplicate", "move", "delete", "mute", "unmute", "rename", "groove",
                                   "to_arrangement"],
                   bars: str | None = None, scene: str | int | None = None, track: str | None = None,
                   to_bar: int | None = None, times: int = 1, name: str | None = None,
                   groove: str | None = None) -> str:
        """Act on clips on one track, or on every track when track is left out. With bars ("49-52",
        inclusive): the arrangement clips that START in those bars. With scene (name, or number from 1):
        the session clips in that scene.

        duplicate copies arrangement clips to to_bar, `times` copies back to back (the bars' length apart);
        move copies once to to_bar and deletes the originals (bars may not overlap); delete; mute / unmute;
        rename gives each clip `name`; groove gives each clip `groove`, a groove in the set's groove pool by
        name, or "none" to take the groove off, and returns each clip's groove before and after (if Live
        refuses one clip, the clips before it get their old groove back). to_arrangement takes scene and
        bars: it fills those arrangement bars with the scene's clips, each repeated back to back at its own
        length (exact, no real-time recording); a copy that would run past the bars is left out and listed.
        Returns the clips it acted on. Cmd+Z in Live undoes each step.

        Examples:
          live_clips("mute", bars="113-176", track="BASS STK")
          live_clips("duplicate", bars="1-8", to_bar=17, times=24)          -> every track's 8-bar loop over 17-208
          live_clips("to_arrangement", scene="DROP 1", bars="89-112")      -> the DROP 1 scene over its section
          live_clips("groove", track="CLOSED HAT", bars="1-208", groove="Swing MPC 3000 16ths 57")
          live_clips("groove", track="KICK", bars="1-208", groove="none")   -> the kick carries no groove
        """
        return out(call("live_clips", action=action, bars=bars, scene=scene, track=track, to_bar=to_bar, times=times,
                        name=name, groove=groove))

    @mcp.tool(annotations=_write("Session scenes", destructive=True, idempotent=False))
    def live_scenes(ctx: Context, action: Literal["list", "create", "rename", "fire", "delete"] = "list",
                    scene: str | int | None = None, name: str | None = None,
                    response_format: Format = "concise") -> str:
        """The session view's scenes, by name or by number as Live shows it (1 is the top scene).
        list gives each scene's number, name and the tracks with a clip in it; with "detailed", each
        clip's name and length in bars and, for audio, its file and pitch_coarse. create adds a scene
        after `scene` (or at the end) named `name`; rename; fire launches it; delete. A session clip's
        notes are live_notes_read / live_notes_write with scene=; copying a scene into the arrangement
        is live_clips("to_arrangement").

        Examples:
          live_scenes()
          live_scenes(response_format="detailed")   -> every slot's clip: name, length, file, transpose
          live_scenes("create", name="Groove")
          live_scenes("fire", scene="Groove")
        """
        return out(call("live_scenes", action=action, scene=scene, name=name, response_format=response_format))

    @mcp.tool(annotations=_write("Create, duplicate, delete or rename tracks", destructive=True, idempotent=False))
    def live_tracks(ctx: Context, action: Literal["create", "duplicate", "delete", "rename"], track: str | None = None,
                    name: str | None = None, kind: Literal["midi", "audio", "return"] = "midi",
                    in_group: str | None = None) -> str:
        """Create, duplicate, delete or rename a track by name.

        create makes a `kind` track named `name`, right after `track` (or at the end); with in_group
        (a group track's name) instead, inside that group, so it can route to the group's bus. Live's API
        cannot group tracks, and a track created after a group's last track lands outside it, so the new
        track goes just before the group's last track and is checked: if Live put it elsewhere it is
        deleted again and the error says where. duplicate copies `track`
        with its devices and clips, the copy named `name`, right after it; delete removes `track`;
        rename names `track` `name`. A created or copied track that sits in a group reports it as group.
        Routing and mixer settings are in live_set.

        Examples:
          live_tracks("duplicate", track="BASS MID", name="BASS MID 2")
          live_tracks("create", kind="audio", track="DRUMS", name="RESAMPLE")
          live_tracks("create", kind="audio", in_group="🥁 DRUMS", name="TOP CLAP")   -> {"group": "🥁 DRUMS", ...}
          live_tracks("rename", track="Audio 3", name="TOPS")
        """
        return out(call("live_tracks", action=action, track=track, name=name, kind=kind, in_group=in_group))

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
        for _ in range(15):  # Live needs about 4 ticks (move, settle, toggle, check); poll up to 3 s
            time.sleep(0.2)
            listed = call("live_locators", action="list")
            mine = [r for r in listed.get("recent", []) if abs(r["asked_bar"] - result["bar"]) < 1e-6]
            if mine:
                break
        else:
            raise ToolError(f"Live did not finish the locator at bar {result['bar']:g} within 3 s; "
                            f"locators now: {listed['locators']}")
        r = mine[-1]
        if r.get("error"):
            raise ToolError(f"locator at bar {result['bar']:g}: {r['error']}; locators now: {listed['locators']}")
        if action == "set" and abs(r["landed_bar"] - r["asked_bar"]) > 1e-3:
            listed["warning"] = (f"Live placed '{name}' at bar {r['landed_bar']:g}, not {r['asked_bar']:g} "
                                 "(Live snaps new locators; the locator is named where it landed)")
        return out(listed)

    @mcp.tool(annotations=_read("Search Live's browser"))
    def live_browser_find(ctx: Context, name: str = "", contains: str = "", roots: list[str] | None = None,
                          limit: int = 10) -> str:
        """Search Live's browser inside Live: by exact name, or by a case-insensitive part of a name.
        roots: plugins, audio_effects, instruments, midi_effects, user_folders, packs (default all six),
        searched breadth-first six levels down, which reaches a pack's presets (pack / Sounds / Suitcase
        Piano / preset). Each match gives a path that live_load takes as item. Stops at `limit` matches
        or after 4 s, and says how many items it searched and how long it took; naming fewer roots
        finds deeper things sooner.

        Examples:
          live_browser_find(name="Serum 2")
          live_browser_find(contains="tape", roots=["audio_effects", "plugins"])
          live_browser_find(contains="suitcase", roots=["packs"])
            -> {"path": "app.browser.packs.children.11.children.2.children.0.children.3", "name": "Suitcase Blender.adg"}
          then live_load("KEYS", "app.browser.packs.children.11.children.2.children.0.children.3")
        """
        return out(call("browser_find", name=name or None, contains=contains or None, roots=roots, limit=limit))

    @mcp.tool(annotations=_write("Load a device, plugin or preset", destructive=False, idempotent=False))
    def live_load(ctx: Context, track: str, item: str, scene: str | int | None = None, bar: float | None = None,
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
          live_load("KEYS", "app.browser.packs.children.11.children.2.children.0.children.3")   -> a pack preset, by its path
          live_load("PERC", "/Users/me/Splice/perc_loop_126.wav", scene="DROP 1", clip={"pitch_coarse": -2, "warping": True})
          live_load("VOX", "/Users/me/Stems/vox_chop.wav", bar=97, clip={"name": "Vox chop"})
        """
        before = live_memory.live_memory()
        try:
            result = call("live_load", track=track, item=item, scene=scene, bar=bar, clip=clip)
        except Exception as e:
            stop = live_memory.runaway(before)
            raise ToolError(f"{stop} ({e})" if stop else str(e)) from e
        stop = live_memory.runaway(before)
        if stop:
            raise ToolError(stop)
        return out(result)

    @mcp.tool(annotations=_write("Draw automation into a Session clip", destructive=True, idempotent=True))
    def live_clip_envelope(ctx: Context, target: str, scene: str | int, points: list[list[float | str]],
                           shape: Literal["linear", "hold"] = "linear", clear: bool = False) -> str:
        """Draw automation into the SESSION clip in scene on target's track, for jams and tests: live_clips("to_arrangement")
        does NOT carry the envelope (measured in Live 24.11.2026), so for an arranged track use live_automate(saved=True) on
        the instrument's Transpose. target "Track / Device / Param"; points [[beat, value], ...]
        with beats from the clip's start (0 = its first beat) and values as Live shows them. shape "linear" ramps in
        1/64-beat steps between the points, "hold" keeps each value until the next; the last value holds to the clip's end.
        clear empties the parameter's envelope first. Every point is read back; a difference is an error.
        A MIDI pitch bend is no parameter and can't be drawn: bend the instrument's Transpose instead.

        Example: a note at beat 2.5 bent up 1.79 st over a 16th and held, then back on beat 3 ->
          live_clip_envelope("BEND TEST / Operator / Transpose", scene=1, points=[[0, "0 st"], [2.5, "0 st"], [2.75, "+1.79 st"], [2.99, "+1.79 st"], [3, "0 st"]])
        (two points with one value make a hold; the step from 2.99 back to 0 is 0.01 beat long)
        """
        return out(call("live_clip_envelope", target=target, scene=scene, points=points, shape=shape, clear=clear))

    @mcp.tool(annotations=_write("Warp an audio clip straight", destructive=True, idempotent=True))
    def live_warp(ctx: Context, track: str, bpm: float, first_beat_s: float = 0.0, bar: float | None = None,
                  scene: str | int | None = None) -> str:
        """Warp an audio clip straight at one tempo from its first downbeat: what Clear All Warp Markers,
        Set 1.1.1 Here, Warp From Here (Straight) and Seg. BPM do by hand. The clip is the arrangement clip
        that STARTS at bar, or the session clip in scene (name, or number from 1). Every old warp marker
        goes; first_beat_s lands on beat 0 (the clip's start) and every later beat follows 60/bpm seconds
        apart: one marker on the first beat, one on the last whole beat in the file. The clip's start is
        set to the first beat, its end to that last beat. If Live refuses a step or puts a marker elsewhere,
        the old markers are put back and the error says so. Returns the markers before and after,
        [seconds, beat] each.

        first_beat_s is seconds into the file AS LIVE READS IT. Live does not skip an MP3's encoder delay
        when the file has no LAME tag, while Rekordbox grids, soundfile and librosa do: for such an MP3
        add ffprobe's start_time (e.g. 0.025057 s) to a Rekordbox first-beat time, or the kick lands
        ~25 ms late.

        Examples:
          live_warp("REF FULL", bar=1, bpm=126, first_beat_s=0.135)    -> a reference from its Rekordbox grid
          live_warp("PERC", scene="DROP 1", bpm=124)                     -> a loop that starts on its downbeat
          live_warp("VOX", bar=97, bpm=122.5, first_beat_s=1.48)         -> an acapella whose first beat is 1.48 s in
        """
        return out(call("live_warp", track=track, bpm=bpm, first_beat_s=first_beat_s, bar=bar, scene=scene))

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

    @mcp.tool(annotations=_write("Write arrangement automation", destructive=True, idempotent=True))
    def live_automate(ctx: Context, lanes: list[Lane], saved: bool = False, open_set: bool = True) -> str:
        """Record automation into the arrangement: each lane is a target (a parameter, "Track / pan" or
        "Track / send A"), points [[bar, value], …] with values as Live shows them, and a shape: linear
        (default), ease, or ease-log (for frequencies); a fade from or to "-inf dB" eases the knob's
        position. A mixer volume is refused: fades and rides go on a Utility's Output (its gain), so the fader stays
        free. Live plays only the bars that move, with Arrangement Record and Automation Arm on, moving
        the knobs the way Push writes automation; Cmd+Z undoes it. Returns at once: poll
        live_automate_status until state is done.

        A knob moves only on Live's ticks, about 10 a second: a jump (two points closer than a tick) lands on the
        last tick before its time, up to one tick (about 0.1 s) early, never late. For steps exact to the beat, use
        saved=True; to see where a pass's steps landed, save the set and call live_automation(plan=lanes).

        Refused while a track plays Session or Back to Arrangement is lit (Arrangement Record would write those
        tracks over their clips), or while Arrangement Record, MIDI Arrangement Overdub or Session Overdub is on.
        A pass that changed any arrangement clip anyway is undone until the clips match again and ends in state
        "error", naming the tracks in "touched", with "repaired" saying whether the undo got there.

        Live's API cannot delete arrangement automation. A lane with clear ("41-57", or "all" for the whole
        song) instead of points records a flat line over those bars, at value or at what the parameter
        holds now; it is allowed on a mixer volume, to undo an old fade there.

        Examples:
          live_automate([{"target": "PAD / Auto Filter / Frequency", "points": [[89, "900 Hz"], [97, "20 kHz"]],
                          "shape": "ease-log"}])
          live_automate([{"target": "TOPS / Utility / Output", "points": [[96, "0 dB"], [97, "-inf dB"]]}])
          live_automate([{"target": "PAD / volume", "clear": "41-57", "value": "-4 dB"}])   -> a flat line

        saved=True writes the same lanes in seconds and to the beat into a copy of the saved set,
        "<set> [auto <date time>].als" beside it, and opens that copy in Live, which keeps working there (Cmd+S
        saves into it; the set itself stays as it was, with a copy in Backup/Before automate). Refused unless the
        set was saved after its last change, as Live's undo journal shows: Live would ask "Save changes?" before
        switching. A set just opened counts as changed (its plug-ins record steps while loading), so save once
        first. open_set=False writes the copy without opening it. A lane may also be {"target": ..., "clear": "all"},
        which removes it. Devices inside racks, switches and menus stay with the recording.

          live_automate([{"target": "PAD / Utility / Output", "points": [[41, "-inf dB"], [57, "-4 dB"]]}], saved=True)
        """
        if saved:
            return saved_set(lambda S, conn: S.patch(conn, lanes, open_it=open_set))
        return out(call("automate", lanes=lanes))

    @mcp.tool(annotations=_read("Read arrangement automation"))
    def live_automation(ctx: Context, target: str | None = None, plan: list[Lane] | None = None) -> str:
        """Every arrangement automation lane, read from the saved set without playing: target, the value before
        the song, and its breakpoints as [bar, shown value]. target narrows it to a track ("PAD"), a device
        ("PAD / Utility") or one lane ("PAD / send A"). A lane recorded in real time is thinned to the points
        that shape it (within 0.5 dB, a quarter tone, or 0.5 % of the range). It reads the last save: "state"
        says "unsaved" when Live has changed the set since.

        plan= (the lanes given to live_automate) checks their steps instead (two points under a 16th apart): "off"
        names lane and bar of every step the saved lanes put more than 1/64 bar late, more than 0.25 s early, or
        nowhere.

        Examples:
          live_automation("PAD")         -> PAD / Utility / Output, PAD / send A, … with [[41, "-inf dB"], [57, "-4.0 dB"]]
          live_automation("VOX LEAD / send E")
          live_automation(plan=[{"target": "KICK / Auto Filter / Frequency", "points": [[47.99, "20 Hz"], [48, "150 Hz"]]}])
            -> {"off": ["KICK / Auto Filter / Frequency: the step at bar 48 lands 0.045 bars late, at 48.040"]}
        """
        if plan:
            return saved_set(lambda S, conn: S.steps(conn, plan))
        return saved_set(lambda S, conn: S.lanes(conn, target))

    @mcp.tool(annotations=_read("Automation recording progress"))
    def live_automate_status(ctx: Context) -> str:
        """How live_automate is doing: state running | done | error, the spans (bars) it records and which one
        is recording now, and tick_beats: Live's tick as the pass measured it, the most a step lands early.

        Example: live_automate_status()  -> {"state": "done", ...}
        """
        return out(call("automate_status"))

    @mcp.tool(annotations=_write("Meter every track over bars", destructive=False, idempotent=True))
    def live_meters(ctx: Context, bars: str, tracks: list[str] | None = None, follow: bool = False,
                    kick: str | None = None, response_format: Format = "concise") -> str:
        """Levels without recording: plays the bars once and reads every track's, group's, return's and the master's
        output meter (post-fader) inside Live 20-30 times a second, then stops and puts the playhead back. Takes as
        long as the bars play. Per track: level (the mean), peak and an envelope per beat ("concise": per bar), in dB
        on Live's meter scale (the fader's; not LUFS), in the mixer's hierarchy: groups and ungrouped tracks loudest
        first, each group's tracks under it. A track with Kickstart or a sidechained compressor gets its pump: the dip
        in dB on each kick and how long until it is back within 1 dB, against the kick group (else the kick track;
        or `kick`); the median over the kicks the track sounds across, with the IQR of the dips, and "not
        measurable" when it sounds across fewer than half (a sparse bass: its gaps are not a duck). tracks narrows the answer (a group brings its tracks); every track is metered either way.
        Refused while anything is soloed, and while the transport plays unless follow=True, which meters the bars
        as Live plays through them and touches nothing.

        Examples:
          live_meters("49-64")                                  -> the drop: what is loudest, what pumps how much
          live_meters("81-88", tracks=["🎸 BASS", "PAD"])
          live_meters("97-112", follow=True)                    -> while Marc plays the set
        """
        from MCP_Server import meters as M

        try:
            s = M.analyse(M.run(get_ableton_connection(), bars, follow=follow, tracks=tracks), tracks, kick)
        except M.MetersError as e:
            raise ToolError(str(e)) from e
        return out(M.concise(s) if response_format == "concise" else s)

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
