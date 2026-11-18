# AbletonMCP - Ableton Live Model Context Protocol Integration
[![smithery badge](https://smithery.ai/badge/@ahujasid/ableton-mcp)](https://smithery.ai/server/@ahujasid/ableton-mcp)

AbletonMCP connects Ableton Live to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Ableton Live. This integration enables prompt-assisted music production, track creation, and Live session manipulation.

### Join the Community

Give feedback, get inspired, and build on top of the MCP: [Discord](https://discord.gg/3ZrMyGKnaU). Made by [Siddharth](https://x.com/sidahuj)

## Features

- **Two-way communication**: Connect Claude AI to Ableton Live through a socket-based server
- **Track manipulation**: Create, modify, and manipulate MIDI and audio tracks
- **Instrument and effect selection**: Claude can access and load the right instruments, effects and sounds from Ableton's library
- **Clip creation**: Create and edit MIDI clips with notes
- **Session control**: Start and stop playback, fire clips, and control transport

## Components

The system consists of two main components:

1. **Ableton Remote Script** (`Ableton_Remote_Script/__init__.py`): A MIDI Remote Script for Ableton Live that creates a socket server to receive and execute commands
2. **MCP Server** (`server.py`): A Python server that implements the Model Context Protocol and connects to the Ableton Remote Script

## Installation

### Installing via Smithery

To install Ableton Live Integration for Claude Desktop automatically via [Smithery](https://smithery.ai/server/@ahujasid/ableton-mcp):

```bash
npx -y @smithery/cli install @ahujasid/ableton-mcp --client claude
```

### Prerequisites

- Ableton Live 10 or newer
- Python 3.8 or newer
- [uv package manager](https://astral.sh/uv)

If you're on Mac, please install uv as:
```
brew install uv
```

Otherwise, install from [uv's official website][https://docs.astral.sh/uv/getting-started/installation/]

⚠️ Do not proceed before installing UV

### Claude for Desktop Integration

[Follow along with the setup instructions video](https://youtu.be/iJWJqyVuPS8)

1. Go to Claude > Settings > Developer > Edit Config > claude_desktop_config.json to include the following:

```json
{
    "mcpServers": {
        "AbletonMCP": {
            "command": "uvx",
            "args": [
                "ableton-mcp"
            ]
        }
    }
}
```

### Cursor Integration

Run ableton-mcp without installing it permanently through uvx. Go to Cursor Settings > MCP and paste this as a command:

```
uvx ableton-mcp
```

⚠️ Only run one instance of the MCP server (either on Cursor or Claude Desktop), not both

### Installing the Ableton Remote Script

[Follow along with the setup instructions video](https://youtu.be/iJWJqyVuPS8)

1. Download the `AbletonMCP_Remote_Script/__init__.py` file from this repo

2. Copy the folder to Ableton's MIDI Remote Scripts directory. Different OS and versions have different locations. **One of these should work, you might have to look**:

   **For macOS:**
   - Method 1: Go to Applications > Right-click on Ableton Live app → Show Package Contents → Navigate to:
     `Contents/App-Resources/MIDI Remote Scripts/`
   - Method 2: If it's not there in the first method, use the direct path (replace XX with your version number):
     `/Users/[Username]/Library/Preferences/Ableton/Live XX/User Remote Scripts`
   
   **For Windows:**
   - Method 1:
     C:\Users\[Username]\AppData\Roaming\Ableton\Live x.x.x\Preferences\User Remote Scripts 
   - Method 2:
     `C:\ProgramData\Ableton\Live XX\Resources\MIDI Remote Scripts\`
   - Method 3:
     `C:\Program Files\Ableton\Live XX\Resources\MIDI Remote Scripts\`
   *Note: Replace XX with your Ableton version number (e.g., 10, 11, 12)*

4. Create a folder called 'AbletonMCP' in the Remote Scripts directory and paste the downloaded '\_\_init\_\_.py' file

3. Launch Ableton Live

4. Go to Settings/Preferences → Link, Tempo & MIDI

5. In the Control Surface dropdown, select "AbletonMCP"

6. Set Input and Output to "None"

## Usage

### Starting the Connection

1. Ensure the Ableton Remote Script is loaded in Ableton Live
2. Make sure the MCP server is configured in Claude Desktop or Cursor
3. The connection should be established automatically when you interact with Claude

### Using with Claude

Once the config file has been set on Claude, and the remote script is running in Ableton, you will see a hammer icon with tools for the Ableton MCP.

## Tools

Seventeen `live_*` tools, each a whole job in one round trip (`MCP_Server/tools/live.py`; each runs
inside Live as the socket command of the same name, `AbletonMCP_Remote_Script/handlers/live.py`).
The conventions are stated once, in the server's instructions:

- **Named, never numbered.** A target is `"Track / Device / Param"` (`"BASS MID / EQ Eight / 1 Frequency A"`)
  or `"Track / <setting>"` (`volume`, `pan`, `send A` or `send A-Reverb`, `mute`, `solo`, `arm`, `output`,
  `output channel`, `input`, `input channel`, `monitor`). Racks: `"DRUMS / Drum Rack / Kick / Simpler / Decay"`.
  The master is `Master`. A name matches exactly, then ignoring case; `"EQ Eight#2"` picks the second of two.
  A miss lists the names there are and the close ones.
- **Bars are 1-based and song-absolute**, ranges inclusive (`"49-52"`). Notes are
  `[bar, beat, pitch, length, velocity]`, beat 1 the downbeat, length in beats. A looped clip reads
  unrolled; writes go into its own notes, never its repeats.
- **Session view by scene**: name or number (1 is the top scene). With `scene=`, the notes tools read and
  write that slot's clip, bars counted from the clip's start.
- **Values are what Live shows**: `"-6 dB"`, `"-inf dB"`, `"110 Hz"`, `"25L"`, `"High Pass 48dB"`, `"On"`.

| Tool | Job |
|------|-----|
| `live_overview(response_format)` | The whole set: sections from the locators with what plays in each, every track (group, flags, volume, output, devices, clip bars), returns, master |
| `live_track(track, response_format)` | One track, or one device on it (`"SERUM BASS / Saturator"`): mixer, sends, routing, every parameter as shown, clips |
| `live_set(changes)` | Any number of parameters and track settings by shown value; all checked before any is set, the rest put back if Live refuses one; returns before → after |
| `live_notes_read(track, bars, scene, as_grid, response_format)` | Notes in bars, from every clip on the track (or the session clip in scene) |
| `live_notes_write(track, start_bar, bars, notes \| grid, mode, clip_name, scene)` | Replace or add notes in bars; creates the clip when none covers them; all checked before anything changes |
| `live_clips(action, bars, scene, track, to_bar, times, name)` | duplicate, move, delete, mute, unmute, rename the arrangement clips that start in bars, or the session clips of a scene; to_arrangement fills arrangement bars with a scene, each clip repeated at its own length |
| `live_scenes(action, scene, name, response_format)` | list, create, rename, fire, delete scenes; detailed list gives each slot clip's name, length in bars and, for audio, file and pitch_coarse |
| `live_tracks(action, track, name, kind)` | create, duplicate, delete, rename |
| `live_transport(play, position_bar, loop_bars, loop, tempo, metronome, record)` | Check, then set any of them; report the transport |
| `live_locators(action, bar, name, locator)` | list, set (add or rename), delete |
| `live_browser_find(name, contains, roots, limit)` | Search Live's browser inside Live |
| `live_load(track, item, scene, bar, clip)` | Load a device, plugin, preset or sample by name at the end of the chain; with `scene` or `bar`, an audio file as a new clip in that session slot or at that arrangement bar, its clip properties (`pitch_coarse`, `warping`, `name`, …) set in the same call |
| `live_simpler(simpler, sample, start_ms, end_ms, …)` | Load a sample into a Simpler and set its region, warp, mode, fades |
| `live_automate(lanes)` / `live_automate_status()` | Record arrangement automation in real time |
| `live_lom(op, path, …)` | Escape hatch: get / set / call / batch on Live's object model by path, with names in brackets: `tracks[BASS MID].devices[EQ Eight].parameters`, `tracks[SUB].arrangement_clips[@113]` |
| `live_reload_handlers()` | Reload the Remote Script after `deploy.sh` |

The older index-based socket commands (`get_device_parameters`, `create_clip`, …) still answer for
scripts that use them; they no longer have MCP tools. Tests run without Live: `uv run pytest tests`.

### macwhite additions (handlers/dispatch.py)

Hot-reloadable commands added for the master-measurement / auto-mastering workflow
(see `production/README.md` in the parent repo):

| Command | Purpose |
|---------|---------|
| `get_input_routing` / `set_input_routing` | Read/set a track's `input_routing_type` (e.g. `Resampling`) + `monitor` (`in`/`auto`/`off`). |
| `fire_clip_slot` | Fire a clip slot regardless of `has_clip` — on an armed track with an empty slot this starts **recording** (used to capture the master via Resampling). |
| `stop_all_clips` | `song.stop_all_clips()` — commits any in-progress session recording. |
| `set_clip_quantization` | Set/restore global clip-launch quantization (returns previous). |

Also: `get_arrangement_clips` now includes `file_path` for audio clips.

## Example Commands

Here are some examples of what you can ask Claude to do:

- "Create an 80s synthwave track" [Demo](https://youtu.be/VH9g66e42XA)
- "Create a Metro Boomin style hip-hop beat"
- "Create a new MIDI track with a synth bass instrument"
- "Add reverb to my drums"
- "Create a 4-bar MIDI clip with a simple melody"
- "Get information about the current Ableton session"
- "Load a 808 drum rack into the selected track"
- "Add a jazz chord progression to the clip in track 1"
- "Set the tempo to 120 BPM"
- "Play the clip in track 2"


## Troubleshooting

- **Connection issues**: Make sure the Ableton Remote Script is loaded, and the MCP server is configured on Claude
- **Timeout errors**: Try simplifying your requests or breaking them into smaller steps
- **Have you tried turning it off and on again?**: If you're still having connection errors, try restarting both Claude and Ableton Live

## Technical Details

### Communication Protocol

The system uses a simple JSON-based protocol over TCP sockets:

- Commands are sent as JSON objects with a `type` and optional `params`
- Responses are JSON objects with a `status` and `result` or `message`

### Limitations & Security Considerations

- Creating complex musical arrangements might need to be broken down into smaller steps
- The tool is designed to work with Ableton's default devices and browser items
- Always save your work before extensive experimentation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Disclaimer

This is a third-party integration and not made by Ableton.
