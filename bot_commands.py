"""
SunSafe — Bot Command Listener (polling)
-----------------------------------------
מאזין (polling, לא webhook) לארבע פקודות בלבד: /dashboard, /set_skin_type,
/start_session, /end_session. זהו שלב ביניים מינימלי — לא זרימת שיחה
מלאה עם כפתורים (TODO #5), ולא webhook production (TODO #8) — רק מספיק
כדי לאפשר את פיצ'ר "האזור האישי" בלי להמתין לשניהם. שדרוג לכפתורים
אמיתיים בהמשך לא ידרוש לשנות את מודל הנתונים.

הרצה:
    python bot_commands.py
    (משאירים רץ ברקע; Ctrl+C לעצירה)

תלות: משתמש ב-supabase_client.py הקיים (insert_row/select_rows/
update_rows/upsert_row) — REST ישיר מול PostgREST דרך httpx, בלי
SDK נוסף, עקבי עם שאר הקוד.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from supabase_client import SupabaseError, insert_row, select_rows, update_rows, upsert_row

load_dotenv()

logger = logging.getLogger("sunsafe.bot_commands")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
DASHBOARD_BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "http://localhost:8080")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

LINK_TTL_MINUTES = 60 * 24  # 24 שעות — נוח לשימוש חוזר בלי לוותר על תפוגה

# זהה לנוסחה ב-README/דף ההדגמה/calculate_exposure_score.
SKIN_TYPE_FACTOR = {1: 0.5, 2: 0.75, 3: 1.0, 4: 1.5, 5: 2.5, 6: 4.0}


# ---------------------------------------------------------------------
# Exposure score (עותק מקומי טהור — בלי תלות ב-Agent Loop, כי הפקודות
# האלה דטרמיניסטיות ולא דורשות LLM כדי "להבין" אותן)
# ---------------------------------------------------------------------
def effective_spf(labeled_spf: int | None) -> float:
    if not labeled_spf:
        return 1.0
    return 1 + (labeled_spf - 1) * 0.4


def calculate_exposure_score(uv_index: float, duration_minutes: float, skin_type: int, spf: int | None) -> int:
    factor = SKIN_TYPE_FACTOR.get(skin_type, 1.0)
    protection = effective_spf(spf)
    safe_minutes = (200 / uv_index) * factor * protection
    return round((duration_minutes / safe_minutes) * 100)


# ---------------------------------------------------------------------
# Open-Meteo — geocoding + UV (עותק מקומי, מקביל ל-mcp_weather_server.py;
# הכלים שם עטופים ב-@mcp.tool ולא נוחים לייבוא ישיר מסקריפט חיצוני)
# ---------------------------------------------------------------------
def geocode_city(client: httpx.Client, city_name: str) -> dict:
    response = client.get(
        GEOCODING_URL,
        params={"name": city_name, "count": 1, "language": "he", "format": "json"},
        timeout=10.0,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return {"found": False}
    top = results[0]
    return {
        "found": True,
        "name": top.get("name"),
        "country": top.get("country"),
        "latitude": top.get("latitude"),
        "longitude": top.get("longitude"),
    }


def get_current_uv(client: httpx.Client, lat: float, lon: float) -> float:
    response = client.get(
        OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lon, "current": "uv_index"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["current"]["uv_index"]


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------
def send_message(chat_id: int, text: str) -> None:
    with httpx.Client() as client:
        response = client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
        response.raise_for_status()


# ---------------------------------------------------------------------
# /dashboard — Magic Link
# ---------------------------------------------------------------------
def create_magic_link(telegram_username: str) -> str:
    """
    יוצר טוקן אקראי חסין-ניחוש (32 בייטים), שומר אותו בטבלת magic_links
    יחד עם telegram_username ותאריך תפוגה, ומחזיר את ה-URL המלא לשליחה
    בטלגרם. הטבלה הזו נגישה רק ל-service_role — אין לה policies
    שמאפשרים גישה מ-anon/authenticated, כך שרק קוד שרת (הבוט הזה,
    ובהמשך ה-Edge Function) יכולים לגעת בה.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=LINK_TTL_MINUTES)

    insert_row(
        "magic_links",
        {
            "token": token,
            "telegram_username": telegram_username,
            "expires_at": expires_at.isoformat(),
            "used": False,
        },
    )

    logger.info("Created magic link for @%s (expires %s)", telegram_username, expires_at)
    return f"{DASHBOARD_BASE_URL}/?token={token}"


def handle_dashboard(chat_id: int, username: str) -> None:
    link = create_magic_link(username)
    send_message(chat_id, f"האזור האישי שלך (בתוקף ל-24 שעות):\n{link}")
    logger.info("Sent dashboard link to @%s", username)


# ---------------------------------------------------------------------
# /set_skin_type <1-6>
# ---------------------------------------------------------------------
def handle_set_skin_type(chat_id: int, username: str, args: str) -> None:
    args = args.strip()
    if not args.isdigit() or not (1 <= int(args) <= 6):
        send_message(chat_id, "שימוש: /set_skin_type <מספר 1 עד 6> (סולם Fitzpatrick).")
        return

    skin_type = int(args)
    upsert_row(
        "users",
        {"telegram_username": username, "skin_type": skin_type},
        on_conflict="telegram_username",
    )
    send_message(chat_id, f"נשמר: סוג עור {skin_type}.")
    logger.info("Set skin_type=%s for @%s", skin_type, username)


# ---------------------------------------------------------------------
# /start_session <עיר>
# ---------------------------------------------------------------------
def handle_start_session(chat_id: int, username: str, args: str) -> None:
    city = args.strip()
    if not city:
        send_message(chat_id, "שימוש: /start_session <שם עיר>")
        return

    users = select_rows("users", {"telegram_username": f"eq.{username}"})
    if not users:
        send_message(chat_id, "קודם צריך להגדיר סוג עור: /set_skin_type <1-6>")
        return

    open_sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "end_time": "is.null"},
    )
    if open_sessions:
        send_message(chat_id, "כבר יש לך session פתוח. שלחו /end_session קודם.")
        return

    with httpx.Client() as client:
        geo = geocode_city(client, city)
        if not geo["found"]:
            send_message(chat_id, f'לא הצלחתי לזהות עיר בשם "{city}". בדקו את האיות ונסו שוב.')
            return
        uv_index = get_current_uv(client, geo["latitude"], geo["longitude"])

    now = datetime.now(timezone.utc)
    insert_row(
        "exposure_log",
        {
            "telegram_username": username,
            "city": geo["name"],
            "country": geo["country"],
            "start_time": now.isoformat(),
            "end_time": None,
            "uv_index": uv_index,
            "spf": None,
            "exposure_score": None,
        },
    )
    send_message(
        chat_id,
        f"התחלת session ב{geo['name']} (UV נוכחי: {uv_index:.1f}). "
        "כשתסיימו, שלחו /end_session (או /end_session <SPF> אם השתמשתם בקרם הגנה).",
    )
    logger.info("Started session for @%s in %s (UV=%s)", username, geo["name"], uv_index)


# ---------------------------------------------------------------------
# /end_session [SPF]
# ---------------------------------------------------------------------
def handle_end_session(chat_id: int, username: str, args: str) -> None:
    args = args.strip()
    spf = None
    if args:
        if not args.isdigit():
            send_message(chat_id, "שימוש: /end_session או /end_session <SPF כמספר, למשל 30>")
            return
        spf = int(args)

    open_sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "end_time": "is.null"},
    )
    if not open_sessions:
        send_message(chat_id, "אין לך session פתוח כרגע. שלחו /start_session <עיר> כדי להתחיל אחד.")
        return

    session = open_sessions[0]
    users = select_rows("users", {"telegram_username": f"eq.{username}"})
    skin_type = users[0]["skin_type"] if users else 3  # ברירת מחדל זהירה אם חסר, לא אמור לקרות

    start_time = datetime.fromisoformat(session["start_time"])
    end_time = datetime.now(timezone.utc)
    duration_minutes = (end_time - start_time).total_seconds() / 60

    score = calculate_exposure_score(session["uv_index"], duration_minutes, skin_type, spf)

    update_rows(
        "exposure_log",
        {"id": f"eq.{session['id']}"},
        {"end_time": end_time.isoformat(), "spf": spf, "exposure_score": score},
    )

    send_message(
        chat_id,
        f"session הסתיים — {round(duration_minutes)} דקות ב{session['city']}. "
        f"מדד חשיפה: {score}%.",
    )
    logger.info("Ended session id=%s for @%s: score=%s", session["id"], username, score)


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------
COMMAND_HANDLERS = {
    "/dashboard": lambda chat_id, username, args: handle_dashboard(chat_id, username),
    "/set_skin_type": handle_set_skin_type,
    "/start_session": handle_start_session,
    "/end_session": handle_end_session,
}


def handle_update(update: dict) -> None:
    """מטפל בעדכון בודד מ-getUpdates. מתעלם מכל דבר שאינו אחת מ-4 הפקודות."""
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username")

    if not text.startswith("/"):
        return

    command, _, args = text.partition(" ")
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        return  # פקודה לא מוכרת — מתעלמים, לא זרימת שיחה מלאה עדיין

    if not username:
        send_message(chat_id, "צריך שיהיה לך username מוגדר בהגדרות טלגרם כדי להשתמש בפקודות האלה.")
        return

    try:
        handler(chat_id, username, args)
    except SupabaseError as e:
        logger.error("Supabase error handling %s for @%s: %s", command, username, e)
        send_message(chat_id, "משהו השתבש בשמירת הנתונים. נסו שוב בעוד רגע.")


def poll_forever() -> None:
    """
    לולאת polling פשוטה מול getUpdates. long-polling של 30 שניות
    לכל בקשה — לא צורך CPU/רשת מיותרים בין עדכונים.
    """
    logger.info("Listening for commands (polling): %s", list(COMMAND_HANDLERS))
    offset = None
    with httpx.Client() as client:
        while True:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            response = client.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35.0)
            response.raise_for_status()
            updates = response.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception:
                    logger.exception("Failed to handle update: %s", update)


if __name__ == "__main__":
    poll_forever()