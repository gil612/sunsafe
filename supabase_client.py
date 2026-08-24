"""
SunSafe — Supabase Client
--------------------------
A thin wrapper around Supabase's PostgREST REST API for inserting rows,
built directly on httpx (no supabase-py SDK) — same approach as
telegram_client.py and mcp_weather_server.py's calls to Open-Meteo.

Install:
    pip install httpx python-dotenv

Run as a standalone check:
    python supabase_client.py
"""

import os
import logging
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sunsafe.supabase")
logging.basicConfig(level=logging.INFO)


class SupabaseError(Exception):
    """Raised when a call to the Supabase REST API fails."""


@dataclass
class SupabaseConfig:
    url: str
    service_role_key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL ו/או SUPABASE_SERVICE_ROLE_KEY לא מוגדרים. הוסיפו אותם ל-.env."
            )
        return cls(url=url, service_role_key=key)


def insert_row(table: str, row: dict, config: "SupabaseConfig | None" = None) -> dict:
    """
    Insert a single row into a Supabase table via PostgREST and return the
    inserted row (including its generated id).
    Raises SupabaseError on any failure — callers that must not let a
    logging failure break the main flow should catch this explicitly.
    """
    config = config or SupabaseConfig.from_env()
    url = f"{config.url}/rest/v1/{table}"
    headers = {
        "apikey": config.service_role_key,
        "Authorization": f"Bearer {config.service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        response = httpx.post(url, headers=headers, json=row, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Supabase insert into %s failed (%s): %s", table, e.response.status_code, e.response.text)
        raise SupabaseError(f"הוספת שורה ל-{table} נכשלה: {e.response.text}") from e
    except httpx.RequestError as e:
        logger.error("Supabase request to %s failed: %s", table, e)
        raise SupabaseError(f"בקשת רשת ל-Supabase נכשלה: {e}") from e

    inserted = response.json()
    return inserted[0] if isinstance(inserted, list) else inserted


if __name__ == "__main__":
    # בדיקה עצמאית מהירה — מריצים "python supabase_client.py"
    result = insert_row(
        "uv_readings",
        {
            "query_city": "test",
            "resolved_city": "Test City",
            "country": "Testland",
            "lat": 0.0,
            "lon": 0.0,
            "uv_index": 1.0,
            "temperature_2m": 20.0,
            "cloud_cover": 0,
        },
    )
    print(f"נכתבה שורה בהצלחה, id={result['id']}")
