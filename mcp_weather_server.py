"""
SunSafe — Weather MCP Server
------------------------------
שרת MCP עצמאי שעוטף את Open-Meteo (חינמי, ללא API key) וחושף כלי מזג-אוויר
כ-MCP Tools סטנדרטיים. השרת הזה יכול לשמש גם ל-Agent Loop בפרודקשן (דרך
mcp_agent_loop.py) וגם ל-Claude Desktop / Claude Code לבדיקה ידנית —
בלי לכתוב את לוגיקת ה-Weather פעמיים.

הרצה עצמאית (למשל לחיבור מ-Claude Desktop):
    python mcp_weather_server.py

בדרך כלל לא מריצים את זה ידנית — ה-MCP Client (למשל mcp_agent_loop.py)
מפעיל את הקובץ הזה כתת-תהליך אוטומטית דרך stdio.
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
    מחזיר את מדד ה-UV הנוכחי, הטמפרטורה וכיסוי העננים למיקום גיאוגרפי נתון.
    יש לקרוא לכלי הזה כל פעם שצריך מידע עדכני על רמת קרינת UV במקום מסוים.
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
    מחזיר תחזית UV Index שעתית ל-N הימים הקרובים (ברירת מחדל: 3) עבור
    מיקום גיאוגרפי נתון. שימושי לתכנון חשיפה מראש, לא רק למצב הנוכחי.
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
