"""
SunSafe — Weather MCP Server
------------------------------
A standalone MCP server wrapping Open-Meteo (free, no API key) and exposing
weather tools as standard MCP Tools. The same server serves both the production
Agent Loop (via mcp_agent_loop.py) and Claude Desktop / Claude Code for manual
testing — without writing the weather logic twice.

Run standalone (for example, to connect from Claude Desktop):
    python mcp_weather_server.py

Usually you do not run this by hand — the MCP Client (e.g. mcp_agent_loop.py)
spawns this file as a subprocess automatically over stdio.
"""

import logging

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sunsafe.mcp_weather_server")

mcp = FastMCP("weather-mcp-server")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@mcp.tool()
async def get_current_uv(lat: float, lon: float) -> dict:
    """
    Return the current UV index, temperature and cloud cover for a given
    geographic location. Call this tool whenever up-to-date information about
    the UV radiation level at a specific place is needed.
    """
    logger.info("get_current_uv(lat=%s, lon=%s)", lat, lon)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "uv_index,temperature_2m,cloud_cover",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()["current"]


@mcp.tool()
async def get_uv_forecast(lat: float, lon: float, days: int = 3) -> dict:
    """
    Return an hourly UV Index forecast for the next N days (default: 3) for a
    given geographic location. Useful for planning exposure ahead of time, not
    just for the current conditions.
    """
    logger.info("get_uv_forecast(lat=%s, lon=%s, days=%s)", lat, lon, days)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "uv_index",
                "forecast_days": days,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()["hourly"]


if __name__ == "__main__":
    mcp.run()  # stdio transport כברירת מחדל
