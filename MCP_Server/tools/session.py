"""Session tools: session info, tempo, playback."""

import json
import logging
from typing import Any, Callable

from mcp.server.fastmcp import Context

logger = logging.getLogger("AbletonMCPServer")


def register(mcp: Any, get_ableton_connection: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_session_info(ctx: Context) -> str:
        """Get detailed information about the current Ableton session."""
        try:
            ableton = get_ableton_connection()
            result = ableton.send_command("get_session_info")
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"Error getting session info from Ableton: {str(e)}")
            return f"Error getting session info: {str(e)}"

    @mcp.tool()
    def set_tempo(ctx: Context, tempo: float) -> str:
        """Set the tempo of the Ableton session. Parameters: tempo (BPM)."""
        try:
            ableton = get_ableton_connection()
            ableton.send_command("set_tempo", {"tempo": tempo})
            return f"Set tempo to {tempo} BPM"
        except Exception as e:
            logger.error(f"Error setting tempo: {str(e)}")
            return f"Error setting tempo: {str(e)}"

    @mcp.tool()
    def start_playback(ctx: Context) -> str:
        """Start playing the Ableton session."""
        try:
            ableton = get_ableton_connection()
            ableton.send_command("start_playback")
            return "Started playback"
        except Exception as e:
            logger.error(f"Error starting playback: {str(e)}")
            return f"Error starting playback: {str(e)}"

    @mcp.tool()
    def stop_playback(ctx: Context) -> str:
        """Stop playing the Ableton session."""
        try:
            ableton = get_ableton_connection()
            ableton.send_command("stop_playback")
            return "Stopped playback"
        except Exception as e:
            logger.error(f"Error stopping playback: {str(e)}")
            return f"Error stopping playback: {str(e)}"
