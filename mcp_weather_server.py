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

from supabase_client import insert_row, SupabaseError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sunsafe.mcp_weather_server")

mcp = FastMCP("weather-mcp-server")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


@mcp.tool()
async def geocode_city(city_name: str) -> dict:
    """
    מאתר קואורדינטות (lat, lon) עבור שם עיר, באמצעות שירות ה-Geocoding
    החינמי של Open-Meteo (ללא API key, כמו שאר הכלים בשרת הזה).

    יש לקרוא לכלי הזה תמיד *לפני* get_current_uv או get_uv_forecast,
    כדי לוודא שהעיר אכן קיימת ולקבל קואורדינטות מדויקות — ולא לנחש
    lat/lon עצמאית מתוך ידע כללי.

    מחזיר dict עם "found": bool.
    אם found=True: גם "name" (השם הרשמי/המתוקן), "country", "latitude",
    "longitude".
    אם found=False: לא נמצאה עיר מתאימה לשם שסופק — אין לנחש ערכים,
    יש לדווח על כך למשתמש.
    """
    logger.info("geocode_city(city_name=%s)", city_name)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            GEOCODING_URL,
            params={"name": city_name, "count": 1, "language": "he", "format": "json"},
            timeout=10.0,
        )
        response.raise_for_status()
        results = response.json().get("results") or []

        if not results:
            logger.info("geocode_city(%s) -> not found", city_name)
            return {"found": False, "query": city_name}

        top = results[0]
        result = {
            "found": True,
            "name": top.get("name"),
            "country": top.get("country"),
            "latitude": top.get("latitude"),
            "longitude": top.get("longitude"),
        }
        logger.info("geocode_city(%s) -> %s", city_name, result)
        return result


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
async def log_uv_reading(
    query_city: str,
    resolved_city: str,
    country: str | None,
    lat: float,
    lon: float,
    uv_index: float,
    temperature_2m: float | None = None,
    cloud_cover: int | None = None,
) -> dict:
    """
    שומר קריאת UV שבוצעה בפועל בטבלת uv_readings ב-Supabase, לצורך
    היסטוריה עתידית (Dashboard). יש לקרוא לכלי הזה תמיד אחרי
    get_current_uv כאשר יש תוצאה תקפה לדווח עליה.

    כשלון בשמירה (בעיית רשת/הרשאות מול Supabase) לא אמור לעצור את
    התשובה למשתמש — הכלי מחזיר {"logged": False, "error": ...}
    במקום לזרוק חריגה.
    """
    logger.info(
        "log_uv_reading(query_city=%s, resolved_city=%s, uv_index=%s)",
        query_city, resolved_city, uv_index,
    )
    row = {
        "query_city": query_city,
        "resolved_city": resolved_city,
        "country": country,
        "lat": lat,
        "lon": lon,
        "uv_index": uv_index,
        "temperature_2m": temperature_2m,
        "cloud_cover": cloud_cover,
    }
    try:
        inserted = insert_row("uv_readings", row)
        logger.info("log_uv_reading -> logged id=%s", inserted.get("id"))
        return {"logged": True, "id": inserted.get("id")}
    except (SupabaseError, RuntimeError) as e:
        logger.warning("log_uv_reading failed: %s", e)
        return {"logged": False, "error": str(e)}


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
