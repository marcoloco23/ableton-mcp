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

    def send_command(self, name, params=None):
        self.sent.append((name, params))
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
    assert len(tools) == 17
    for t in tools:
        assert t.name.startswith("live_"), t.name
        a = t.annotations
        assert None not in (a.readOnlyHint, a.destructiveHint, a.idempotentHint), t.name
        assert "Example" in t.description, t.name
        assert "index" not in json.dumps(t.inputSchema), t.name  # addressed by name, never by number
    assert "1-based and song-absolute" in server.instructions


@pytest.mark.parametrize("name, read_only", [("live_overview", True), ("live_track", True), ("live_notes_read", True),
                                             ("live_set", False), ("live_notes_write", False), ("live_lom", False)])
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


def test_lom_escape_hatch_maps_to_the_lom_commands():
    conn = Conn({})
    m = tools_with(conn)
    call(m, "live_lom", op="get", path="tracks", fields=["name"])
    call(m, "live_lom", op="set", path="tracks[PAD].mute", value=True)
    call(m, "live_lom", op="batch", ops=[{"op": "get", "path": "tempo"}])
    assert [s[0] for s in conn.sent] == ["lom_get", "lom_set", "lom_batch"]
    assert conn.sent[1][1] == {"path": "tracks[PAD].mute", "value": True}
