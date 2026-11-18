"""Register the Ableton MCP tools with the FastMCP instance."""

from typing import Any, Callable


def register_all(mcp: Any, get_ableton_connection: Callable[[], Any]) -> None:
    """Register the live_* tools with the given mcp and connection getter."""
    from . import live

    live.register(mcp, get_ableton_connection)
