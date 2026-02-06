"""Device tools: get/set device parameters."""

import json
import logging
from typing import Any, Callable

from mcp.server.fastmcp import Context

logger = logging.getLogger("AbletonMCPServer")


def register(mcp: Any, get_ableton_connection: Callable[[], Any]) -> None:
    @mcp.tool()
    def get_device_parameters(
        ctx: Context,
        track_index: int,
        device_index: int,
        track_type: str = "track",
    ) -> str:
        """Get all parameters for a device. Parameters: track_index, device_index, track_type ('track'|'return'|'master')."""
        try:
            ableton = get_ableton_connection()
            result = ableton.send_command("get_device_parameters", {
                "track_index": track_index,
                "device_index": device_index,
                "track_type": track_type,
            })
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"Error getting device parameters: {str(e)}")
            return f"Error getting device parameters: {str(e)}"

    @mcp.tool()
    def set_device_parameter(
        ctx: Context,
        track_index: int,
        device_index: int,
        parameter_index: int,
        value: float,
        track_type: str = "track",
    ) -> str:
        """Set a device parameter. Parameters: track_index, device_index, parameter_index, value (0.0-1.0), track_type."""
        try:
            ableton = get_ableton_connection()
            ableton.send_command("set_device_parameter", {
                "track_index": track_index,
                "device_index": device_index,
                "parameter_index": parameter_index,
                "value": value,
                "track_type": track_type,
            })
            return f"Set device {device_index} parameter {parameter_index} to {value}"
        except Exception as e:
            logger.error(f"Error setting device parameter: {str(e)}")
            return f"Error setting device parameter: {str(e)}"
