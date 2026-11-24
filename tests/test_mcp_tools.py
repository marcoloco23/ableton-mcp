"""The MCP face: few live_* tools, each annotated, taught by examples, forwarding one command."""
import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from MCP_Server.connection import AbletonError
from MCP_Server.server import mcp as server
from MCP_Server.tools import live


class Conn:
    def __init__(self, answer=None, error=None):
        self.sent, self.answer, self.error = [], answer, error

    def send_command(self, name, params=None, timeout=None):
        self.sent.append((name, params))
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.answer if self.answer is not None else {"ok": True}


def tools_with(conn):
    m = FastMCP("test")
    live.register(m, lambda: conn)
    return m


def call(m, name, **args):
    return asyncio.run(m.call_tool(name, args))


def test_the_server_offers_only_annotated_live_tools_taught_by_example():
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 22
    for t in tools:
        assert t.name.startswith("live_"), t.name
        a = t.annotations
        assert None not in (a.readOnlyHint, a.destructiveHint, a.idempotentHint), t.name
        assert "Example" in t.description, t.name
        assert "index" not in json.dumps(t.inputSchema), t.name  # addressed by name, never by number
    assert "1-based and song-absolute" in server.instructions


@pytest.mark.parametrize("name, read_only", [("live_overview", True), ("live_audit", True), ("live_track", True), ("live_notes_read", True),
                                             ("live_automation", True), ("live_set", False), ("live_notes_write", False),
                                             ("live_lom", False)])
def test_read_tools_say_so(name, read_only):
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == name)
    assert tool.annotations.readOnlyHint is read_only


def test_live_set_is_one_round_trip():
    conn = Conn({"changed": []})
    changes = [{"target": "BASS MID / EQ Eight / 1 Frequency A", "value": "110 Hz"},
               {"target": "BASS MID / volume", "value": "-6 dB"}]
    call(tools_with(conn), "live_set", changes=changes)
    assert conn.sent == [("live_set", {"changes": changes})]


def test_lives_message_comes_back_as_the_tool_error():
    m = tools_with(Conn(error=AbletonError("no track 'BASS MD'; did you mean 'BASS MID'?")))
    with pytest.raises(ToolError, match="did you mean 'BASS MID'"):
        call(m, "live_track", track="BASS MD")


def test_grid_notes_land_on_the_start_bar():
    conn = Conn({})
    call(tools_with(conn), "live_notes_write", track="DRUMS", start_bar=65, bars=1, grid="KK|o---o---o---o---|")
    name, body = conn.sent[0]
    assert name == "live_notes_write" and body["mode"] == "replace" and "clip_name" not in body
    assert [n[:3] for n in body["notes"]] == [[65, 1.0, 36], [65, 2.0, 36], [65, 3.0, 36], [65, 4.0, 36]]
    with pytest.raises(ToolError, match="notes or grid"):
        call(tools_with(Conn()), "live_notes_write", track="DRUMS", start_bar=65, bars=1)


def test_live_warp_is_destructive_and_forwards_one_command():
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "live_warp")
    assert tool.annotations.destructiveHint is True and "encoder delay" in tool.description
    conn = Conn({})
    call(tools_with(conn), "live_warp", track="REF FULL", bar=1, bpm=126, first_beat_s=0.135)
    assert conn.sent == [("live_warp", {"track": "REF FULL", "bpm": 126, "first_beat_s": 0.135, "bar": 1})]


def test_lom_escape_hatch_maps_to_the_lom_commands():
    conn = Conn({})
    m = tools_with(conn)
    call(m, "live_lom", op="get", path="tracks", fields=["name"])
    call(m, "live_lom", op="set", path="tracks[PAD].mute", value=True)
    call(m, "live_lom", op="batch", ops=[{"op": "get", "path": "tempo"}])
    assert [s[0] for s in conn.sent] == ["lom_get", "lom_set", "lom_batch"]
    assert conn.sent[1][1] == {"path": "tracks[PAD].mute", "value": True}


def test_live_clips_groove_forwards_the_groove_name():
    conn = Conn({})
    call(tools_with(conn), "live_clips", action="groove", track="KICK", bars="1-208", groove="none")
    assert conn.sent == [("live_clips", {"action": "groove", "bars": "1-208", "track": "KICK", "times": 1,
                                         "groove": "none"})]


def test_live_automate_takes_a_clear_lane():
    conn = Conn({})
    lane = {"target": "PAD / volume", "clear": "41-57", "value": "-4 dB"}
    call(tools_with(conn), "live_automate", lanes=[lane])
    assert conn.sent == [("automate", {"lanes": [lane]})]


def test_saved_set_jobs_go_to_the_saved_set_and_a_refusal_reads_as_the_tool_error(monkeypatch):
    from MCP_Server import saved_set

    seen = []
    monkeypatch.setattr(saved_set, "patch", lambda conn, lanes, open_it: seen.append(("patch", lanes, open_it)) or {})
    monkeypatch.setattr(saved_set, "lanes", lambda conn, target: seen.append(("lanes", target)) or {"lanes": []})
    monkeypatch.setattr(saved_set, "steps", lambda conn, plan: seen.append(("steps", plan)) or {"off": []})
    conn = Conn()
    m = tools_with(conn)
    lanes = [{"target": "PAD / send A", "points": [[96, "-6 dB"], [97, "-inf dB"]]}]
    call(m, "live_automate", lanes=lanes, saved=True)
    call(m, "live_automate", lanes=lanes, saved=True, open_set=False)
    call(m, "live_automation", target="PAD")
    call(m, "live_automation", plan=lanes)
    assert seen == [("patch", lanes, True), ("patch", lanes, False), ("lanes", "PAD"), ("steps", lanes)]
    assert conn.sent == []

    def refuse(conn, lanes, open_it):
        raise saved_set.Refused("Live counts Serenity.als as changed since it opened")
    monkeypatch.setattr(saved_set, "patch", refuse)
    with pytest.raises(ToolError, match="changed since it opened"):
        call(m, "live_automate", lanes=lanes, saved=True)
    call(m, "live_automate", lanes=lanes)
    assert conn.sent == [("automate", {"lanes": lanes})]  # the recording route is unchanged
