"""
SunSafe — Bot Command Listener (polling)
-----------------------------------------
מאזין (polling, לא webhook) לארבע פקודות: /dashboard, /set_skin_type,
/start_session, /end_session — ולתמונות בודדות (הצעת סוג עור, ראו
skin_type_classifier.py). זהו שלב ביניים מינימלי — לא זרימת שיחה
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

import io
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from skin_type_classifier import classify_skin_type_from_image, validate_classification
from skin_damage_classifier import (
    classify_skin_damage_from_image,
    validate_classification as validate_damage_classification,
)
from supabase_client import SupabaseError, delete_rows, insert_row, select_rows, update_rows, upsert_row

load_dotenv()

logger = logging.getLogger("sunsafe.bot_commands")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# httpx רושם ללוג (ברמת INFO) את ה-URL המלא של כל בקשה כברירת מחדל —
# ו-TELEGRAM_API למטה כולל את ה-BOT_TOKEN עצמו בתוך ה-URL (לא ב-header).
# בלי השורה הזו, כל send_message/send_photo/getUpdates מדליף את הטוקן
# ללוגים בטקסט גלוי — כולל ל-HF Space logs (Application Startup) שכבר
# נראים בפועל בהיסטוריה הזו. אותה בעיה קיימת גם ב-telegram_client.py
# וב-set_bot_profile.py, ותוקנה שם באותו אופן.
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
# chat_id פרטי (לא של משתמש קצה) לדיווחי-טלמטריה פנימיים בלבד — כרגע רק
# דיווח טוקנים שנוצלו בכל תמונה שמסווגים (ראו _notify_admin_token_usage
# למטה). אופציונלי במכוון (os.environ.get, לא os.environ[...] כמו
# BOT_TOKEN): זו תכונת-נחמד-להיות-לי, לא חובה לפעולת הבוט — אם לא
# מוגדר, פשוט מדלגים על השליחה בלי לקרוס. כדי לקבל chat_id שלכם: שלחו
# הודעה כלשהי לבוט ואז ראו את chat_id בטבלת users (עמודת chat_id) מול
# ה-telegram_username שלכם.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
DASHBOARD_BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "http://localhost:8080")
# ה-Mini App לתיעוד session אופליין (docs/session/index.html). ברירת
# מחדל localhost כדי לא לשבור בדיקות מקומיות, בדיוק כמו DASHBOARD_BASE_URL.
# ראו docs/2026-08-29-offline-session-miniapp-design.md.
SESSION_MINIAPP_URL = os.environ.get("SESSION_MINIAPP_URL", "http://localhost:8080/session")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Reverse geocoding (lat/lon -> שם עיר) לשיתוף מיקום מהטלפון. Open-Meteo
# (המקור ל-geocode_city למטה) תומך רק ב-forward geocoding — אין לו נתיב
# reverse, לכן Nominatim (OpenStreetMap): חינמי, בלי מפתח API. חובה
# User-Agent מזהה ומקסימום בקשה/שנייה לפי ה-Usage Policy הרשמי — לא
# בעיה בפועל כאן כי יש לכל היותר קריאה אחת לכל /start_session.
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Forward geocoding — שכבת גיבוי ל-geocode_city (ראו שם) כשל-Open-Meteo/
# GeoNames אין בכלל רשומת-שם בעברית לעיר (לא רק שגיאת איות: "סן חוזה"
# ו"סן חוסה" - שתי הצורות - נכשלו שתיהן ב-Open-Meteo ב-2026-09-08).
# OpenStreetMap הוא מאגר קהילתי גדול משמעותית מ-GeoNames, עם כיסוי
# שמות רב-לשוני עשיר יותר לערים זרות פחות-מוכרות — לא הבטחה, רק כיסוי
# רחב יותר. עדיין בלי תיקון-טעויות-הקלדה אמיתי (edit distance) כמו
# Google — זה נשאר מחוץ לתקציב של הפרויקט (ראה השיחה מ-2026-09-08).
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "SunSafe-Bot/1.0 (student course project)"

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
    # UV=0 (למשל session שנפתח בלילה) הוא ערך תקין לגמרי, לא שגיאה — אבל
    # 200/uv_index עם 0 קורס ב-ZeroDivisionError. בלי חשיפה ל-UV בכלל
    # הסיכון הוא אפס, ללא תלות במשך הזמן, אז מחזירים 0 ישירות. באג אמיתי
    # שתפס session תקוע (id=38, UV=0) — ראה השיחה מ-31.8.2026.
    if uv_index <= 0:
        return 0
    factor = SKIN_TYPE_FACTOR.get(skin_type, 1.0)
    protection = effective_spf(spf)
    safe_minutes = (200 / uv_index) * factor * protection
    return round((duration_minutes / safe_minutes) * 100)


# ---------------------------------------------------------------------
# /today — אנליזה יומית + "מה היה קורה עם קרם הגנה" (ראו handle_today
# למטה). קבוע, לא ניתן להגדרה ע"י המשתמש כרגע — הוחלט במפורש עם המשתמש
# ב-2026-09-08 (SPF 30 קבוע כברירת מחדל, לא פרמטר פתוח) כדי לשמור על
# MVP פשוט: השוואה אחידה, בלי צורך לפרש קלט חופשי.
# ---------------------------------------------------------------------
DAILY_SUMMARY_REFERENCE_SPF = 30


def _sessions_on_date(sessions: list[dict], target_date) -> list[dict]:
    """
    מסנן sessions לאלה שה-start_time שלהם (UTC, כמו כל שאר הזמנים באפליקציה)
    נופל בדיוק על target_date. פונקציה טהורה — בלי DB, קלה לבדיקה בנפרד
    מ-handle_today.
    """
    return [s for s in sessions if datetime.fromisoformat(s["start_time"]).date() == target_date]


def _daily_session_summary(session: dict, skin_type: int | None, reference_spf: int) -> dict:
    """
    עבור session סגור בודד (יש לו end_time+exposure_score): מחזירה dict
    עם הציון בפועל לצד ציון היפותטי אילו נעשה שימוש ב-reference_spf קבוע
    לאורך כל ה-session, במקום ה-spf שבאמת נרשם (כולל None). פונקציה
    טהורה — משתמשת רק ב-calculate_exposure_score הקיים, לא נוגעת ב-DB.
    """
    start_dt = datetime.fromisoformat(session["start_time"])
    end_dt = datetime.fromisoformat(session["end_time"])
    duration_minutes = (end_dt - start_dt).total_seconds() / 60
    hypothetical_score = calculate_exposure_score(session["uv_index"], duration_minutes, skin_type, reference_spf)
    return {
        "id": session["id"],
        "city": session["city"],
        "spf": session.get("spf"),
        "actual_score": session["exposure_score"],
        "hypothetical_score": hypothetical_score,
    }


def _peak_exposure_session(sessions: list[dict]) -> dict | None:
    """
    מחזירה את ה-session (מבין אלה עם exposure_score, כלומר סגורים) עם
    מדד החשיפה הגבוה ביותר, או None אם אין אף session סגור — "המיקום
    איפה שמד החשיפה היה הגבוה ביותר" (הוחלט עם המשתמש ב-2026-09-08).
    פונקציה טהורה, קלה לבדיקה בנפרד — משמשת גם את handle_today (שורת
    טקסט) וגם את render_daily_exposure_chart/send_daily_exposure_chart
    (הדגשה חזותית + כיתוב על הגרף). session פתוח (exposure_score=None)
    לא נכלל — אין לו עדיין ציון להשוות.
    """
    closed = [s for s in sessions if s.get("exposure_score") is not None]
    if not closed:
        return None
    return max(closed, key=lambda s: s["exposure_score"])


# ---------------------------------------------------------------------
# Open-Meteo — geocoding + UV (עותק מקומי, מקביל ל-mcp_weather_server.py;
# הכלים שם עטופים ב-@mcp.tool ולא נוחים לייבוא ישיר מסקריפט חיצוני)
# ---------------------------------------------------------------------
def _raw_geocode_search(client: httpx.Client, name: str, count: int = 1) -> list[dict]:
    """קריאה גולמית ל-Open-Meteo Geocoding — מחזירה עד `count` מועמדים גולמיים."""
    response = client.get(
        GEOCODING_URL,
        params={"name": name, "count": count, "language": "he", "format": "json"},
        timeout=10.0,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    return [
        {
            "name": r.get("name"),
            "country": r.get("country"),
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
        }
        for r in results
    ]


def _text_matches(hint: str, value: str | None) -> bool:
    """
    התאמת טקסט "רכה" בין country_hint שהמשתמש הקליד לבין שדה country
    שחזר מ-Open-Meteo — בלי תלות במקף/גרשיים, ובכיוון הכלה כלשהו (כך
    ש"ארה\"ב" יתאים גם ל"ארצות הברית" אם אחד מהם מוכל במשנהו, לא רק
    שוויון מדויק).
    """
    if not value or not hint:
        return False
    normalize = lambda s: s.strip().replace("-", " ").replace("״", "").replace('"', "")
    hint_n, value_n = normalize(hint), normalize(value)
    return bool(hint_n) and (hint_n in value_n or value_n in hint_n)


def _nominatim_forward_geocode(client: httpx.Client, city_name: str) -> dict:
    """
    שכבת גיבוי ל-geocode_city (למטה) — נקראת רק אחרי ש-Open-Meteo/
    GeoNames נכשל לגמרי (גם התאמה ישירה וגם כל פיצול עיר/מדינה). מריצה
    חיפוש טקסט-חופשי (q=) מול Nominatim, שם המחרוזת כולה (כולל ציון-
    מדינה אם יש, למשל "סן חוסה קוסטה ריקה") נכנסת כמו שהיא — Nominatim
    כבר יודע לפרש "עיר, מדינה" בעצמו, אז אין צורך בלוגיקת הפיצול
    שקיימת למעלה בשביל Open-Meteo.

    לא זורקת אם אין תוצאות/כתובת — מחזירה found=False, אותו חוזה בדיוק
    כמו geocode_city ו-reverse_geocode_location.
    """
    response = client.get(
        NOMINATIM_SEARCH_URL,
        params={"q": city_name, "format": "json", "accept-language": "he", "limit": 1, "addressdetails": 1},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=10.0,
    )
    response.raise_for_status()
    results = response.json() or []
    if not results:
        return {"found": False}

    result = results[0]
    address = result.get("address") or {}
    name = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("county")
        or result.get("name")
    )
    try:
        latitude, longitude = float(result["lat"]), float(result["lon"])
    except (KeyError, TypeError, ValueError):
        return {"found": False}
    if not name:
        return {"found": False}

    return {"found": True, "name": name, "country": address.get("country"), "latitude": latitude, "longitude": longitude}


def geocode_city(client: httpx.Client, city_name: str) -> dict:
    """
    מזהה עיר לפי שם חופשי. קודם מנסים את המחרוזת המלאה כמו שהיא — המקרה
    השכיח, שם עיר יחיד כמו "תל אביב". אם זה נכשל וישנן כמה מילים, כנראה
    שם העיר מלווה בציון מדינה (כמו "סן חוזה קוסטה ריקה" — כדי להבדיל
    מ-San Jose שבארה"ב, שהיא עיר גדולה יותר ותקבל עדיפות בברירת המחדל
    של Open-Meteo לפי אוכלוסייה). ל-Open-Meteo אין פרמטר סינון-לפי-מדינה
    נפרד, אז מפצלים את המחרוזת לחלק-עיר וחלק-מדינה (1 עד 3 המילים
    האחרונות — מכסה גם מדינות דו-מילתיות כמו "קוסטה ריקה"), שולפים כמה
    מועמדים לחלק-העיר, ובודקים איזה מהם ה-country שלו תואם את חלק-המדינה.

    אם גם זה נכשל — לפני שמוותרים לגמרי, מנסים Nominatim
    (_nominatim_forward_geocode) כשכבה שלישית: מקרה אמיתי שנתקלנו בו
    ב-2026-09-08 — "סן חוסה קוסטה ריקה" (וגם האיות "סן חוזה") לא נמצא
    ב-Open-Meteo/GeoNames בשום איות ובשום פיצול, כי ל-GeoNames פשוט אין
    בכלל שם עברי רשום לעיר הזו (לא רק בעיית-איות — Open-Meteo לא עושה
    שום fuzzy/typo matching, בשונה מ-Google). Nominatim (OpenStreetMap,
    כבר בשימוש כאן ל-reverse_geocode_location) הוא מאגר קהילתי גדול
    יותר עם כיסוי רב-לשוני עשיר יותר — לא הבטחה לכל עיר, אבל שכבת גיבוי
    חינמית וסבירה לפני "לא נמצא".

    בלי התאמה בשום שכבה — "לא נמצא", בלי לנחש עיר שגויה בשקט.
    """
    results = _raw_geocode_search(client, city_name, count=1)
    if results:
        return {"found": True, **results[0]}

    tokens = city_name.strip().split()
    for suffix_len in (1, 2, 3):
        if len(tokens) <= suffix_len:
            break
        city_part = " ".join(tokens[:-suffix_len])
        country_hint = " ".join(tokens[-suffix_len:])
        candidates = _raw_geocode_search(client, city_part, count=10)
        match = next((c for c in candidates if _text_matches(country_hint, c.get("country"))), None)
        if match:
            return {"found": True, **match}

    return _nominatim_forward_geocode(client, city_name)


def get_current_uv(client: httpx.Client, lat: float, lon: float) -> float:
    response = client.get(
        OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lon, "current": "uv_index"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["current"]["uv_index"]


def reverse_geocode_location(client: httpx.Client, lat: float, lon: float) -> dict:
    """
    הופך lat/lon (משיתוף מיקום בטלגרם) לשם עיר, דרך Nominatim. ה-address
    שחוזר משתנה לפי סוג המקום — לא תמיד יש city נקי (כפר קטן וכו') — אז
    בודקים כמה שדות בסדר עדיפות ונופלים חזרה ל-found=False אם אף אחד לא
    קיים, בדיוק כמו geocode_city למעלה כשלא נמצאה עיר.
    """
    response = client.get(
        NOMINATIM_REVERSE_URL,
        params={"lat": lat, "lon": lon, "format": "json", "accept-language": "he"},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=10.0,
    )
    response.raise_for_status()
    address = response.json().get("address") or {}
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("county")
    )
    if not city:
        return {"found": False}
    return {"found": True, "name": city, "country": address.get("country")}


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------
def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    with httpx.Client() as client:
        response = client.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()


def send_photo(chat_id: int, photo_bytes: bytes, caption: str | None = None) -> None:
    """
    שולח תמונה בודדת ל-Telegram (sendPhoto, multipart/form-data — בשונה
    מ-send_message למעלה שהוא JSON טהור). לא היה בשימוש עד כה בפרויקט
    (רק sendMessage); נדרש עבור תרשים תחזית ה-UV (send_uv_forecast_chart).
    """
    with httpx.Client() as client:
        response = client.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, **({"caption": caption} if caption else {})},
            files={"photo": ("uv_forecast.png", photo_bytes, "image/png")},
            timeout=15.0,
        )
        response.raise_for_status()


def prompt_location_share(chat_id: int) -> None:
    """
    שולח כפתור "שתפו מיקום" מובנה של טלגרם (request_location) — לחיצה
    עליו גורמת ללקוח לשלוח הודעת location עם lat/lon אמיתיים מה-GPS,
    בלי שום קוד custom בצד הלקוח (לא Mini App). ראו
    docs/2026-08-26-location-sharing-design.md.
    """
    send_message(
        chat_id,
        "אפשר להתחיל session ישירות מהמיקום שלכם — לחצו על הכפתור למטה, "
        "או שלחו /start_session <שם עיר> ידנית.",
        reply_markup={
            "keyboard": [[{"text": "📍 שתפו מיקום", "request_location": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
    )


def download_telegram_photo(client: httpx.Client, file_id: str) -> bytes:
    """
    מוריד את בייטס התמונה בפועל מטלגרם, לפי file_id. שני שלבים: getFile
    (מחזיר file_path זמני) ואז הורדה מ-.../file/bot<token>/<file_path>.
    לא שומר לדיסק בשום שלב — מחזיר bytes בזיכרון בלבד; קורא(י)ם ל-
    handle_skin_type_photo זורקים אותם מיד אחרי השימוש (ראו שם).
    """
    resp = client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=10.0)
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]

    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    file_resp = client.get(file_url, timeout=15.0)
    file_resp.raise_for_status()
    return file_resp.content


# ---------------------------------------------------------------------
# דיווח טוקנים ל-admin — לא ל-webhook נפרד (הבוט כבר מגיב מיידית לכל
# תמונה נכנסת דרך לולאת ה-polling עצמה, ראו handle_update למטה), אלא
# הודעת Telegram נוספת שנשלחת רק ל-ADMIN_CHAT_ID (לא למשתמש שהעלה את
# התמונה) בכל פעם שמתבצעת קריאת Gemini לסיווג תמונת-עור, כדי לעקוב אחרי
# עלות בזמן אמת. ראו _extract_usage ב-skin_type_classifier.py /
# skin_damage_classifier.py למקור המספרים.
# ---------------------------------------------------------------------
def _notify_admin_token_usage(feature: str, username: str, usage: dict | None) -> None:
    """
    best-effort בלבד, בכוונה: אם ADMIN_CHAT_ID לא מוגדר, אם ל-usage אין
    ערך (למשל ה-SDK לא החזיר usage_metadata), או אם השליחה עצמה נכשלת
    (Telegram API) — רק רושמים ללוג ולא זורקים. זו טלמטריה צדדית; אסור
    לה לשבש את התשובה שכבר נשלחה למשתמש עצמו (ולכן נקראת רק אחרי
    שהתקבלה תשובה תקינה מ-Gemini, לא בתוך אותו try/except שמטפל בכשל
    הסיווג עצמו).
    """
    if not ADMIN_CHAT_ID:
        logger.debug("ADMIN_CHAT_ID not set — skipping token-usage admin notification")
        return
    if not usage:
        logger.debug("No usage metadata available for %s/@%s — skipping admin notification", feature, username)
        return
    try:
        send_message(
            ADMIN_CHAT_ID,
            f"📊 {feature} | @{username} | טוקנים: קלט={usage.get('prompt_tokens')}, "
            f"פלט={usage.get('output_tokens')}, סה\"כ={usage.get('total_tokens')}",
        )
    except Exception as e:
        logger.warning("Failed to send admin token-usage notification (%s/@%s): %s", feature, username, e)


# ---------------------------------------------------------------------
# תמונה נכנסת — הצעת סוג עור (Fitzpatrick) בלבד, לא כתיבה ל-DB
# ---------------------------------------------------------------------
def handle_skin_type_photo(chat_id: int, username: str, photo_file_id: str) -> None:
    """
    מוריד תמונה שנשלחה לבוט, שולח אותה ל-Gemini להערכת סוג עור (הצעה
    בלבד — ראו skin_type_classifier.py ו-docs/2026-08-26-skin-type-photo
    -design.md), ומבקש מהמשתמש לאשר/לתקן דרך /set_skin_type הקיים.
    הפונקציה הזו **לא** כותבת ל-users בעצמה — בכוונה, כדי שערך בטיחותי
    (הבסיס ל-exposure_score) תמיד יעבור אישור אנושי מפורש.
    """
    with httpx.Client() as client:
        photo_bytes = download_telegram_photo(client, photo_file_id)

    try:
        raw = classify_skin_type_from_image(photo_bytes)
    except Exception as e:
        logger.warning("classify_skin_type_from_image failed for @%s: %s", username, e)
        send_message(
            chat_id,
            "לא הצלחתי לנתח את התמונה כרגע. נסו שוב, או השתמשו ב-/set_skin_type "
            "<1-6> ידנית.",
        )
        return

    _notify_admin_token_usage("skin_type", username, raw.pop("_usage", None))
    result = validate_classification(raw)
    if not result["ok"]:
        send_message(
            chat_id,
            f"לא הצלחתי להעריך סוג עור מהתמונה הזו ({result['reason']}). "
            "נסו תמונה ברורה יותר של העור, או השתמשו ב-/set_skin_type <1-6> ידנית.",
        )
        logger.info("Photo skin-type classification rejected for @%s: %s", username, result)
        return

    send_message(
        chat_id,
        f"לפי התמונה, נראה כמו סוג עור ~{result['skin_type']} (Fitzpatrick, "
        f"רמת ביטחון: {result['confidence']}). {result['reasoning']}\n\n"
        "שימו לב: זו הערכה חזותית משוערת בלבד, לא שאלון רשמי המבוסס על "
        f"היסטוריית שרפות-שמש — לאישור שלחו /set_skin_type {result['skin_type']}, "
        "או מספר אחר אם זה לא מדויק.",
    )
    logger.info("Photo skin-type suggestion for @%s: %s", username, result)


# ---------------------------------------------------------------------
# /diagnose_skin — הערכת נזק-שמש מתמונה, אחרי חשיפה
# ---------------------------------------------------------------------
# הבוט הזה חסר state-tracking אמיתי (כל handler בודד/stateless, נשען
# על ה-DB) — אין שום דרך קיימת "לזכור" בין הודעה להודעה. תמונה נכנסת
# הייתה עד עכשיו תמיד מנותבת ל-handle_skin_type_photo (ראו handle_update
# למטה). כדי ש-/diagnose_skin יוכל "לתפוס" את התמונה הבאה של המשתמש
# בלי לשבור את זה, יש כאן דגל pending קטן בזיכרון בלבד (לא ב-DB —
# זה מצב שיחה חולף בסדר גודל של דקות, לא נתון עסקי שצריך לשרוד
# restart של ה-process; אם ה-Space נופל/קם בדיוק בין הפקודה לתמונה,
# המשתמש פשוט חוזר להתנהגות ברירת המחדל הקיימת — הצעת סוג עור, לא
# קריסה). תוקף קצר (10 דקות) כדי שדגל ישן לא "יתפוס" תמונה לא קשורה
# ששולחים הרבה יותר מאוחר.
_PENDING_DIAGNOSE_SKIN_TTL_MINUTES = 10
_pending_diagnose_skin: dict[str, datetime] = {}


def handle_diagnose_skin(chat_id: int, username: str, args: str) -> None:
    _pending_diagnose_skin[username] = datetime.now(timezone.utc) + timedelta(
        minutes=_PENDING_DIAGNOSE_SKIN_TTL_MINUTES
    )
    send_message(
        chat_id,
        "☀️ שלחו עכשיו תמונה ברורה של האזור בעור שנחשף לשמש (התמונה משמשת "
        "רק להערכה הזו ולא נשמרת בשום מקום).\n\n"
        "⚠️ חשוב: זו הערכה חזותית של בינה מלאכותית בלבד — לא אבחנה רפואית "
        "ולא תחליף לרופא. אם משהו מדאיג אתכם (כאב חזק, שלפוחיות, חום), "
        "פנו לרופא/מיון גם בלי לחכות לתשובה כאן.",
    )


def _most_recent_session_id(username: str) -> int | None:
    """session_id לקישור תוצאת האבחון (open או closed, הכי עדכני) — או None אם אין בכלל."""
    sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "order": "start_time.desc", "limit": "1"},
    )
    return sessions[0]["id"] if sessions else None


def handle_skin_damage_photo(chat_id: int, username: str, photo_file_id: str) -> None:
    """
    מוריד תמונה שנשלחה כתגובה ל-/diagnose_skin, שולח אותה ל-Gemini
    להערכת חומרת נזק-שמש (ראו skin_damage_classifier.py), שולח למשתמש
    תשובה עם ניסוח זהיר (לא ייעוץ רפואי; המלצה מפורשת לפנות לרופא
    ב-moderate/severe), וכותב שורת תוצאה ל-skin_damage_log — התמונה
    עצמה נזרקת מיד אחרי הקריאה ל-Gemini, לא נשמרת בשום מקום.
    """
    with httpx.Client() as client:
        photo_bytes = download_telegram_photo(client, photo_file_id)

    try:
        raw = classify_skin_damage_from_image(photo_bytes)
    except Exception as e:
        logger.warning("classify_skin_damage_from_image failed for @%s: %s", username, e)
        send_message(chat_id, "לא הצלחתי לנתח את התמונה כרגע. נסו שוב עם /diagnose_skin.")
        return

    _notify_admin_token_usage("diagnose_skin", username, raw.pop("_usage", None))
    result = validate_damage_classification(raw)
    if not result["ok"]:
        send_message(
            chat_id,
            f"לא הצלחתי להעריך את התמונה הזו ({result['reason']}). נסו תמונה "
            "ברורה יותר של האזור עם /diagnose_skin.",
        )
        logger.info("Photo skin-damage classification rejected for @%s: %s", username, result)
        return

    severity = result["severity"]
    severity_labels = {
        "none": "לא נראים סימני נזק",
        "mild": "אודם קל",
        "moderate": "אודם משמעותי",
        "severe": "אודם עז / חשד לכוויה משמעותית",
    }
    lines = [f"הערכה: {severity_labels[severity]} (ביטחון: {result['confidence']}).", result["reasoning"]]
    if severity in ("moderate", "severe"):
        lines.append(
            "⚠️ מומלץ לפנות לרופא/מיון, בייחוד אם יש שלפוחיות, חום, או הרגשה רעה כללית."
        )
    lines.append("\nתזכורת: זו הערכה חזותית של בינה מלאכותית בלבד, לא אבחנה רפואית.")
    send_message(chat_id, "\n".join(lines))

    try:
        insert_row(
            "skin_damage_log",
            {
                "telegram_username": username,
                "session_id": _most_recent_session_id(username),
                "severity": severity,
                "confidence": result["confidence"],
                "reasoning": result["reasoning"],
            },
        )
    except SupabaseError as e:
        # התשובה כבר נשלחה למשתמש — כשל בשמירה ללוג/היסטוריה לא אמור
        # לגרום להודעת שגיאה נוספת שרק תבלבל (התוצאה עצמה כבר נמסרה).
        logger.error("Failed to save skin_damage_log for @%s: %s", username, e)

    logger.info("Skin-damage assessment for @%s: %s", username, result)


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
# /offline_session — פותח את ה-Mini App לתיעוד session בלי קליטה
# (docs/session/index.html). כפתור web_app, לא קישור רגיל: מריץ את
# הדף בתוך ה-WebView של טלגרם, מה שנותן לו את initData לזיהוי המשתמש
# (ראו docs/2026-08-29-offline-session-miniapp-design.md).
# ---------------------------------------------------------------------
def handle_offline_session(chat_id: int, username: str, args: str) -> None:
    # web_app buttons חייבים HTTPS — טלגרם דוחה כל URL אחר עם 400 Bad
    # Request על ה-sendMessage עצמו (לפני שההודעה בכלל נשלחת). בלי הבדיקה
    # הזו, אם SESSION_MINIAPP_URL לא הוגדר (עדיין על ברירת המחדל
    # http://localhost), הבקשה הייתה נכשלת עם exception לא מטופל וממש
    # שום דבר לא קורה אצל המשתמש — בדיוק המצב שקרה כאן.
    if not SESSION_MINIAPP_URL.startswith("https://"):
        send_message(
            chat_id,
            "התכונה הזו עוד לא מוגדרת אצל מפעיל הבוט (SESSION_MINIAPP_URL "
            "חסר/לא HTTPS). נסו שוב מאוחר יותר.",
        )
        logger.warning(
            "SESSION_MINIAPP_URL is not HTTPS (%r) — refusing to send web_app button to @%s",
            SESSION_MINIAPP_URL, username,
        )
        return

    send_message(
        chat_id,
        "תיעוד session בלי קליטה — פתחו את זה עכשיו, כשיש לכם אינטרנט, "
        "כדי שהעמוד יישמר במכשיר וימשיך לעבוד גם בלי חיבור:",
        reply_markup={
            "inline_keyboard": [[
                {"text": "☀️ פתיחת SunSafe אופליין", "web_app": {"url": SESSION_MINIAPP_URL}},
            ]],
        },
    )
    logger.info("Sent offline-session Mini App link to @%s", username)


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
        # chat_id נשמר יחד עם skin_type — זו נקודת ה-INSERT הראשונה
        # האפשרית של שורת users (skin_type הוא NOT NULL ב-DB), אז זה
        # המקום הכי מוקדם ששומרים בו chat_id למשתמש חדש. ראו
        # docs/2026-08-26-multi-user-broadcast-design.md.
        {"telegram_username": username, "skin_type": skin_type, "chat_id": chat_id},
        on_conflict="telegram_username",
    )
    send_message(chat_id, f"נשמר: סוג עור {skin_type}.")
    logger.info("Set skin_type=%s for @%s", skin_type, username)


# ---------------------------------------------------------------------
# /start_session <עיר> — וגם שיתוף מיקום ישיר (ראו handle_start_session_location)
# ---------------------------------------------------------------------
def _can_start_session(chat_id: int, username: str) -> bool:
    """
    הבדיקות המשותפות לשני נתיבי ההתחלה (הקלדת עיר / שיתוף מיקום): יש
    סוג עור מוגדר, ואין session פתוח כבר. שולחת הודעת שגיאה בעברית
    ומחזירה False אם אחת הבדיקות נכשלה — כדי שלא נבקש מהמשתמש לשתף
    מיקום רק כדי לדחות אותו מיד אחר כך.
    """
    users = select_rows("users", {"telegram_username": f"eq.{username}"})
    if not users:
        send_message(chat_id, "קודם צריך להגדיר סוג עור: /set_skin_type <1-6>")
        return False

    open_sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "end_time": "is.null"},
    )
    if open_sessions:
        send_message(chat_id, "כבר יש לך session פתוח. שלחו /end_session קודם.")
        return False

    return True


# ---------------------------------------------------------------------
# תרשים תחזית UV להמשך היום — נשלח כתוספת best-effort אחרי הודעת האישור
# הטקסטואלית ב-_begin_session. שלושה שלבים נפרדים (fetch/render/send)
# כדי שכל שלב יהיה קל לבדוק/להחליף בנפרד; send_uv_forecast_chart היא
# העטיפה היחידה שבפועל נקראת מבחוץ, וזו שאחראית לכשל-בלי-לקרוס.
# ---------------------------------------------------------------------
def fetch_uv_forecast_next_24h(
    client: httpx.Client, lat: float, lon: float, from_time: datetime
) -> tuple[list[str], list[float]]:
    """
    שולף תחזית UV שעתית ל-24 השעות הבאות *בזמן המקומי של המיקום עצמו*,
    החל מהשעה שבה נפתח ה-session (from_time — datetime עם tzinfo, לרוב
    UTC; לא "עכשיו" כללי בזמן קריאת הפונקציה, אלא הרגע שנשמר בפועל
    ב-exposure_log ב-_begin_session). forecast_days=2 מבטיח מספיק שעות
    גם כש-from_time קרוב לחצות המקומית (חלון 24 שעות עלול לחצות יום
    יומן מקומי אחד). timezone=auto -> hourly.time כבר בזמן המקומי של
    המיקום, לא UTC (תוקן אחרי שהתחזית לניו יורק הוצגה לפי שעון UTC
    ולא לפי השעון המקומי שם); utc_offset_seconds שחוזר בתשובה ממיר את
    from_time לזמן המקומי המתאים באותו מיקום.
    """
    response = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "uv_index",
            "forecast_days": 2,
            "timezone": "auto",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    body = response.json()
    hourly = body["hourly"]
    times: list[str] = hourly["time"]
    uvs: list[float] = hourly["uv_index"]
    utc_offset_seconds = body.get("utc_offset_seconds", 0)

    local_start_hour = (from_time + timedelta(seconds=utc_offset_seconds)).replace(
        minute=0, second=0, microsecond=0, tzinfo=None
    )

    window = [(t, uv) for t, uv in zip(times, uvs) if datetime.fromisoformat(t) >= local_start_hour][:24]
    if not window:
        return [], []
    window_times, window_uvs = zip(*window)
    return list(window_times), list(window_uvs)


def _build_uv_risk_cmap(y_max: float):
    """
    Colormap רציף (ירוק->צהוב->כתום->אדום) שממופה על טווח [0, y_max],
    עם עוגנים באותם ספים בדיוק כמו WHO/פלטת הסטטוס של SunSafe (good/
    warning/serious/critical ב-3/6/8). "רציף" בכוונה — לא 4 פסים
    שטוחים: ראו ה-docstring של render_uv_forecast_chart להסבר למה.
    """
    from matplotlib.colors import LinearSegmentedColormap

    stops_raw = [
        (0, STATUS_COLORS["good"]),
        (3, STATUS_COLORS["warning"]),
        (6, STATUS_COLORS["serious"]),
        (8, STATUS_COLORS["critical"]),
    ]
    positions: list[float] = []
    colors: list[str] = []
    last_pos = -1.0
    for value, color in stops_raw:
        pos = min(value / y_max, 1.0)
        if pos <= last_pos:  # y_max קטן מדי כדי להכיל את כל הספים (למשל UV אפסי) -> מדלגים על כפילויות
            continue
        positions.append(pos)
        colors.append(color)
        last_pos = pos
    if positions[0] > 0:
        positions.insert(0, 0.0)
        colors.insert(0, colors[0])
    if positions[-1] < 1.0:
        positions.append(1.0)
        colors.append(colors[-1])
    return LinearSegmentedColormap.from_list("uv_risk", list(zip(positions, colors)))


STATUS_COLORS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}


def render_uv_forecast_chart(hourly_times: list[str], hourly_uv: list[float], city_name: str) -> bytes:
    """
    מרנדר תרשים PNG (matplotlib, in-memory — io.BytesIO, בלי כתיבה לדיסק)
    של תחזית UV ל-24 השעות הבאות: שטח אחד רציף מתחת לקו, עם גרדיאנט
    צבע רציף (ירוק->צהוב->כתום->אדום) שמשקף את רמת ה-UV באותה שעה —
    לא 4 פסים שטוחים/חתוכים.

    זו הגרסה הרביעית אחרי סבב משוב מהמשתמש (כולם על אותו ציר-זמן,
    שלא השתנה מהתחלה): 1) עמודות צבעוניות -> נדחה ("אני לא אוהב את
    העמודות... עדיף פיתרון ויזואלי אחר"); 2) קו + שטח חתוך ל-4 פסי
    WHO -> אושר על הדוגמה ששלחתי ("אני מעוניין בכזה"); 3) המשתמש ביקש
    "שכל השטח מתחת יהיה זהה" -> הוחלף ל-צבע אחיד שטוח לכל השטח; 4)
    המשתמש ביקש שעדיין "יהיה רלוונטי לשעה" / "שהצבע... יהיה רלוונטי
    לשעה" -> צבע שטוח אחד לא הספיק (לא נושא מידע על השעה) אבל גם לא
    לחזור ל-4 פסים חתוכים ("זהה" מרמז על שטח אחד רציף) — הפתרון: שטח
    רציף אחד (לא מחולק ל-blocks) שהצבע *בתוכו* זורם באופן חלק לפי
    ערך ה-UV של אותה שעה. ממומש ע"י imshow עם גרדיאנט אופקי (עמודה
    לכל שעה, צבועה לפי hourly_uv[i] דרך _build_uv_risk_cmap), עם
    interpolation="bilinear" לבלנד חלק בין שעות סמוכות, clipped
    לפוליגון "מתחת לעקומה" (מ-fill_between עם color="none", רק בשביל
    ה-Path שלו) — כך שהצבע נשאר בתחום שמתחת לקו, לא מלבן מלא.

    הכותרת כוללת את שם העיר (city_name), מוכנס כמו שהוא ל-title בלי
    שום עיבוד bidi. היסטוריה שכדאי לתעד כאן כי היא לא אינטואיטיבית:
    ניסינו לעטוף עם get_display() של python-bidi (ה"תיקון" המקובל
    ל-Hebrew-in-matplotlib), המשתמש חשד שזה הפוך, בדקנו עם השוואת A/B
    מפורשת (עם get_display מול בלי) ובהתחלה אישר את הגרסה עם
    get_display כנכונה — אבל כשזה נבדק מול מה שבאמת מוצג בבוט החי,
    המשתמש קבע במפורש: "אני מעוניין בפיתרון של שורה עליונה" — כלומר
    הגרסה *בלי* עיבוד bidi (Option A) היא הנכונה בפועל אצלו, למרות
    שזה סותר את התיעוד הכללי על matplotlib+RTL. לא לשנות את זה שוב
    בלי אימות ישיר מול תוצאה אמיתית מהבוט החי (לא רק תמונת-השוואה).

    import מקומי (לא בראש הקובץ) בכוונה: אם matplotlib חסר בסביבת
    ה-deploy, רק הפיצ'ר הזה נכשל (ונתפס ב-send_uv_forecast_chart) —
    שאר הבוט (כולל /start_session עצמו) ממשיך לעבוד כרגיל.
    """
    import matplotlib
    matplotlib.use("Agg")  # רינדור ל-buffer בלבד, בלי חלון/תצוגה — נדרש בסביבת שרת
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import Normalize

    hours = [datetime.fromisoformat(t).strftime("%H:%M") for t in hourly_times]
    x = list(range(len(hours)))
    # תקרה ל-y: קצת מעל השיא, אבל לפחות 3 (כדי שהגרף לא יהיה שטוח
    # לגמרי גם בימים עם UV אפסי, למשל session שנפתח בלילה).
    y_max = max(max(hourly_uv, default=0) * 1.15, 3)

    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=150)

    # פוליגון "מתחת לעקומה" — בלי צבע משלו (color="none"), רק כדי
    # לקחת ממנו את ה-Path ולהשתמש בו כ-clip mask לגרדיאנט למטה.
    invisible_fill = ax.fill_between(x, 0, hourly_uv, color="none")
    area_under_curve = invisible_fill.get_paths()[0]

    # גרדיאנט אופקי — עמודה אחת לכל שעה, צבועה לפי ה-UV של אותה שעה,
    # עם בלנד חלק בין שעות (bilinear). clipped לשטח שמתחת לקו בלבד.
    if x:
        cmap = _build_uv_risk_cmap(y_max)
        gradient = np.array(hourly_uv, dtype=float).reshape(1, -1)
        image = ax.imshow(
            gradient, extent=[x[0] - 0.5, x[-1] + 0.5, 0, y_max], origin="lower",
            aspect="auto", cmap=cmap, norm=Normalize(vmin=0, vmax=y_max),
            interpolation="bilinear", alpha=0.6, zorder=2,
        )
        image.set_clip_path(area_under_curve, transform=ax.transData)

    # הקו עצמו — צבע ניטרלי כהה, שיבלוט מעל הגרדיאנט; marker בכל שעה
    # כדי ש-24 נקודות הנתונים יישארו קריאות.
    ax.plot(
        x, hourly_uv, color="#2b2b2b", linewidth=2,
        marker="o", markersize=4, markerfacecolor="#2b2b2b", zorder=3,
    )

    ax.set_title(f"UV Forecast — {city_name} — Next 24 Hours", fontsize=13, pad=12)
    ax.set_ylabel("UV Index")
    ax.set_ylim(0, y_max)
    if x:
        ax.set_xlim(-0.5, len(x) - 0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(hours, rotation=60, ha="right", fontsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)

    # תווית ישירה רק על שיא ה-UV (הערך הכי שימושי, לא על כל 24 השעות —
    # זה היה עמוס מדי לקריאה). ראו dataviz skill: "selective direct labels".
    if hourly_uv:
        peak_idx = max(range(len(hourly_uv)), key=lambda i: hourly_uv[i])
        ax.annotate(
            f"peak {hourly_uv[peak_idx]:.1f}",
            (peak_idx, hourly_uv[peak_idx]),
            textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=8, color="#333333", fontweight="bold", zorder=4,
        )

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def send_uv_forecast_chart(chat_id: int, city_name: str, lat: float, lon: float, session_start: datetime) -> None:
    """
    שולף+מרנדר+שולח את תרשים תחזית ה-UV ל-24 השעות הבאות, החל משעת
    פתיחת ה-session (session_start — לא "עכשיו" בזמן קריאת הפונקציה,
    ראו fetch_uv_forecast_next_24h) — best-effort בכוונה: כל הפונקציה
    עטופה ב-try/except רחב. session כבר נכתב ל-DB ואושר למשתמש בטקסט
    לפני שהפונקציה הזו נקראת (ראו _begin_session) — כשל כאן (רשת, חבילה
    חסרה, תגובה לא צפויה מ-Open-Meteo) לא אמור לעולם להיראות למשתמש
    כתקלה ב-/start_session עצמו, רק להירשם ללוג.
    """
    try:
        with httpx.Client() as client:
            hourly_times, hourly_uv = fetch_uv_forecast_next_24h(client, lat, lon, session_start)
        if not hourly_uv:
            logger.info("send_uv_forecast_chart: no forecast hours available for %s, skipping", city_name)
            return
        chart_png = render_uv_forecast_chart(hourly_times, hourly_uv, city_name)
        send_photo(chat_id, chart_png, caption=f"📊 תחזית UV ל-24 השעות הבאות ב{city_name}")
        logger.info("Sent UV forecast chart to chat_id=%s for %s (%d hours)", chat_id, city_name, len(hourly_uv))
    except Exception:
        logger.exception("send_uv_forecast_chart failed for chat_id=%s city=%s", chat_id, city_name)


# ---------------------------------------------------------------------
# גרף UV יומי ל-/today — עקומת UV מלאה ליום קלנדרי (00:00-23:00 UTC)
# עם חלונות ה-sessions של אותו יום מסומנים עליה. הוחלט עם המשתמש
# ב-2026-09-08 (במקום למשל בר-גרף actual-מול-SPF30): "עקומת UV של היום
# + חלונות ה-sessions מסומנים עליה", נשלח אוטומטית יחד עם הטקסט של
# /today (לא כפקודה נפרדת). ראו handle_today / send_daily_exposure_chart.
# ---------------------------------------------------------------------
def fetch_day_uv_curve(client: httpx.Client, lat: float, lon: float, target_date) -> tuple[list[str], list[float]]:
    """
    שולף את עקומת ה-UV השעתית המלאה (00:00–23:00 UTC) של יום קלנדרי שלם
    (target_date). timezone=UTC בכוונה (לא auto כמו fetch_uv_forecast_next_24h)
    כדי שהאינדקס i יתאים ישירות לשעה i ב-UTC — אותו יום קלנדרי בדיוק
    ש-_sessions_on_date בודקת מולו (start_time.date() ב-UTC), כדי
    שחלונות ה-sessions ב-render_daily_exposure_chart ייושרו נכון מול
    העקומה. past_days/forecast_days נגזרים מההפרש בין today ל-target_date;
    מחוץ לטווח הנתמך של Open-Meteo (עבר רחוק/עתיד רחוק) מחזירים ([], [])
    ומשאירים לקורא (send_daily_exposure_chart) לדלג על הגרף בלי לקרוס.
    """
    today = datetime.now(timezone.utc).date()
    days_diff = (today - target_date).days
    if days_diff > 92 or days_diff < -14:
        return [], []
    if days_diff >= 0:
        past_days, forecast_days = days_diff, 1
    else:
        past_days, forecast_days = 0, min(-days_diff + 1, 16)

    response = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "uv_index",
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "UTC",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    times: list[str] = hourly["time"]
    uvs: list[float] = hourly["uv_index"]

    day_prefix = target_date.isoformat()
    day_hours = [(t, uv) for t, uv in zip(times, uvs) if t.startswith(day_prefix)]
    if not day_hours:
        return [], []
    day_times, day_uvs = zip(*day_hours)
    return list(day_times), list(day_uvs)


def render_daily_exposure_chart(
    hourly_times: list[str], hourly_uv: list[float], sessions: list[dict], city_name: str, target_date,
    utc_offset_seconds: int = 0,
) -> bytes:
    """
    מרנדר PNG (matplotlib, in-memory) של עקומת ה-UV ליום שלם (target_date)
    עם חלונות ה-sessions של אותו יום מסומנים עליה כרצועות אנכיות —
    התוספת הגרפית ל-/today (ראו handle_today/send_daily_exposure_chart).

    עותק עצמאי בכוונה מ-render_uv_forecast_chart, לא חולק איתה קוד
    (מלבד _build_uv_risk_cmap המשותף): לזו יש היסטוריית משוב מפורטת
    משלה (ראו ה-docstring שלה) ולא רוצים ששינוי כאן ישפיע על גרסה
    שכבר עובדת ואושרה. מאותה סיבה: הכותרת/legend/annotations כאן
    בטקסט אנגלי בלבד (רק שם-העיר עצמו, שם פרטי, יכול להיות בעברית,
    בדיוק כמו ברכיב הקיים) — נמנעים במכוון מלהכניס בלוק טקסט עברי חדש
    ל-matplotlib בלי אימות ישיר מול תוצאה אמיתית מהבוט החי (ראו את
    הדיון על get_display()/bidi ברכיב הקיים).

    session פתוח (end_time=None) מוצג עד "עכשיו" בפועל, לא עד סוף היום —
    הגרף תמיד משקף חשיפה שכבר קרתה, לא ניחוש לעתיד.

    utc_offset_seconds (נוסף ב-2026-09-09, ראו fetch_utc_offset_seconds):
    hourly_times מגיע מ-fetch_day_uv_curve ב-UTC ("בכוונה", ראו שם) —
    בלי ההזחה הזו, תוויות ציר ה-X הוצגו כשעון UTC גולמי בלי שום סימון,
    ומשתמש שראה "02:00" חשב שזה השעון המקומי שלו (תקלה אמיתית: משתמש
    בקריית ים, UTC+3, ראה את "עכשיו" מסומן ב-02:00-03:00 בזמן שהשעון
    אצלו הראה 5:00). כאן מזיזים רק את *התוויות המוצגות* לפי הזמן המקומי
    של reference — המיקום המספרי של כל רצועה/סימון (למטה) נשאר מחושב
    לפי UTC פנימית, עקבי עם _sessions_on_date/fetch_day_uv_curve, כך
    שאף רצועה לא זזה בפועל, רק הכיתוב שמעליה.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import Normalize

    hours = [
        (datetime.fromisoformat(t) + timedelta(seconds=utc_offset_seconds)).strftime("%H:%M")
        for t in hourly_times
    ]
    x = list(range(len(hours)))
    y_max = max(max(hourly_uv, default=0) * 1.15, 3)

    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=150)

    invisible_fill = ax.fill_between(x, 0, hourly_uv, color="none")
    area_under_curve = invisible_fill.get_paths()[0]

    if x:
        cmap = _build_uv_risk_cmap(y_max)
        gradient = np.array(hourly_uv, dtype=float).reshape(1, -1)
        image = ax.imshow(
            gradient, extent=[x[0] - 0.5, x[-1] + 0.5, 0, y_max], origin="lower",
            aspect="auto", cmap=cmap, norm=Normalize(vmin=0, vmax=y_max),
            interpolation="bilinear", alpha=0.6, zorder=2,
        )
        image.set_clip_path(area_under_curve, transform=ax.transData)

    ax.plot(
        x, hourly_uv, color="#2b2b2b", linewidth=2,
        marker="o", markersize=4, markerfacecolor="#2b2b2b", zorder=3,
    )

    # רצועות ה-sessions: קידוד חזותי נפרד מהגרדיאנט (hatch + קו-מתאר, לא
    # רק שקיפות-צבע) כדי שיישאר קריא גם ב-colorblind/הדפסה — ראו dataviz
    # skill. session פתוח מוצג עד "עכשיו" בפועל. ה-session עם מדד החשיפה
    # הגבוה ביותר (_peak_exposure_session, ראו שם) מודגש בצבע חם נפרד
    # ומתויג עם שם-העיר — "המיקום איפה שמד החשיפה היה הגבוה ביותר",
    # הוחלט עם המשתמש ב-2026-09-08.
    midnight = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    x_min, x_max = (x[0] - 0.5, x[-1] + 0.5) if x else (0, 24)
    peak_session = _peak_exposure_session(sessions)
    has_session_band = False
    has_peak_band = False
    for session in sessions:
        if not session.get("start_time"):
            continue
        start_dt = datetime.fromisoformat(session["start_time"])
        end_dt = datetime.fromisoformat(session["end_time"]) if session.get("end_time") else now
        x_start = max((start_dt - midnight).total_seconds() / 3600, x_min)
        x_end = min((end_dt - midnight).total_seconds() / 3600, x_max)
        if x_end <= x_start:
            continue
        is_peak = peak_session is not None and session is peak_session
        color = "#c0392b" if is_peak else "#2b6cb0"
        if is_peak:
            band_label = None if has_peak_band else "highest exposure"
            has_peak_band = True
        else:
            band_label = None if has_session_band else "session"
            has_session_band = True
        ax.axvspan(
            x_start, x_end, facecolor=color, alpha=0.22 if is_peak else 0.16, hatch="//",
            edgecolor=color, linewidth=1.4 if is_peak else 1.0,
            zorder=2.6 if is_peak else 2.5, label=band_label,
        )
        if is_peak:
            label = f"{session.get('city', '')} {session['exposure_score']}%"
        else:
            label = f"{session['exposure_score']}%" if session.get("exposure_score") is not None else "open"
        ax.annotate(
            label, ((x_start + x_end) / 2, y_max * 0.95), ha="center", va="top",
            fontsize=8, color=color, fontweight="bold", zorder=4,
        )

    ax.set_title(f"Daily UV Exposure — {city_name} — {target_date.strftime('%d.%m.%Y')}", fontsize=13, pad=12)
    ax.set_ylabel("UV Index")
    # מציינים "local time" במפורש על הציר עצמו — לא רק שהשעות מוזחות
    # (utc_offset_seconds למעלה), אלא כדי שלא ייראה כמו UTC סתום שוב
    # בעתיד אם ה-offset הזה ייכשל-בשקט ויחזור ל-0 (fetch_utc_offset_seconds
    # נכשל best-effort).
    ax.set_xlabel("Hour (local time)", fontsize=9, color="#555555")
    ax.set_ylim(0, y_max)
    if x:
        ax.set_xlim(-0.5, len(x) - 0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(hours, rotation=60, ha="right", fontsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)

    if hourly_uv:
        peak_idx = max(range(len(hourly_uv)), key=lambda i: hourly_uv[i])
        ax.annotate(
            f"peak {hourly_uv[peak_idx]:.1f}",
            (peak_idx, hourly_uv[peak_idx]),
            textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=8, color="#333333", fontweight="bold", zorder=4,
        )

    if has_session_band or has_peak_band:
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _notify_admin_chart_skip(target_date, reason: str) -> None:
    """
    best-effort בלבד: מדווחת ל-ADMIN_CHAT_ID (אם מוגדר, ראו ADMIN_CHAT_ID
    למעלה) מתי ולמה גרף /today לא נשלח בפועל. נוספה ב-2026-09-08 אחרי
    שהתברר שהמשתמש הריץ /today וקיבל טקסט תקין אבל בלי תמונה בכלל —
    send_daily_exposure_chart בולעת כל כשל בכוונה (כדי שכשל בגרף לעולם
    לא ישבור את הטקסט של /today עצמו), אבל זה הפך את הכשל ל"שקט" לגמרי
    ובלתי-ניתן-לאבחון בלי גישה ללוגים של ה-Space. לא זורקת ולא מעכבת
    את /today עצמו במקרה של כשל בשליחה גם כאן.
    """
    if not ADMIN_CHAT_ID:
        return
    try:
        send_message(ADMIN_CHAT_ID, f"⚠️ גרף /today לא נשלח (תאריך {target_date}): {reason[:500]}")
    except Exception as e:
        # תוקן ב-2026-09-08 אחרי הפעלה ראשונה בפרודקשן: השליחה עצמה נכשלה
        # (WARNING בלוג) אבל בלי סיבה — כי ה-except הישן בלע את e ולא הדפיס
        # אותו, כמו _notify_admin_token_usage למעלה שכן עושה זאת. עכשיו
        # מדפיסים את e בפועל כדי שכשל הבא יהיה ניתן-לאבחון בלי ניחושים.
        logger.warning("Failed to send admin chart-skip notification for target_date=%s: %s", target_date, e)


def send_daily_exposure_chart(chat_id: int, todays_sessions: list[dict], target_date) -> None:
    """
    שולח (best-effort) את גרף ה-UV היומי עם חלונות ה-sessions מסומנים —
    תוספת גרפית ל-/today (ראו handle_today). עוטף הכל ב-try/except רחב:
    הטקסט של /today כבר נשלח למשתמש לפני שהפונקציה הזו נקראת (אותו
    דפוס בדיוק כמו send_uv_forecast_chart) — כשל כאן (רשת, matplotlib
    חסר, אין session עם lat/lon) לעולם לא אמור להיראות למשתמש כתקלה
    ב-/today עצמו. כל דילוג/כשל גם מדווח ל-_notify_admin_chart_skip
    (best-effort נוסף, ADMIN_CHAT_ID בלבד) — כדי שהדילוג לא יהיה שקט
    לגמרי (ראו שם לרציונל).

    בוחרים את מיקום-הייחוס לעקומת ה-UV לפי ה-session המוקדם ביותר היום
    שיש לו lat/lon שמור (sessions ישנים/לפני migration ה-lat/lon עשויים
    להיות בלי lat/lon בכלל — אז מדלגים על הגרף כליל, לא מנחשים מיקום).
    """
    try:
        reference = min(
            (s for s in todays_sessions if s.get("lat") is not None and s.get("lon") is not None),
            key=lambda s: s["start_time"],
            default=None,
        )
        if reference is None:
            logger.info("send_daily_exposure_chart: no session today has lat/lon yet, skipping chart")
            _notify_admin_chart_skip(target_date, "אף session באותו יום לא כולל lat/lon שמור")
            return

        with httpx.Client() as client:
            hourly_times, hourly_uv = fetch_day_uv_curve(client, reference["lat"], reference["lon"], target_date)
            if not hourly_uv:
                logger.info("send_daily_exposure_chart: no UV curve available for %s, skipping", target_date)
                _notify_admin_chart_skip(target_date, "fetch_day_uv_curve לא החזירה נתונים (טווח לא נתמך/תשובה ריקה)")
                return
            # תוקן ב-2026-09-09: ציר-השעות של הגרף הוצג לפי UTC גולמי בלי
            # שום סימון (ראו render_daily_exposure_chart) — תקלה אמיתית
            # שדווחה: משתמש בקריית ים (UTC+3) ראה את "עכשיו" מסומן סביב
            # 02:00-03:00 בזמן שהשעון אצלו הראה 5:00, ונראה כמו תקלה.
            # אותו מנגנון fetch_utc_offset_seconds בדיוק כמו ב-/add_session
            # ו-/edit_session (ראו שם) — best-effort, נופל חזרה ל-0 (UTC
            # כפי שהיה) אם הקריאה נכשלת.
            utc_offset_seconds = fetch_utc_offset_seconds(client, reference["lat"], reference["lon"])

        chart_png = render_daily_exposure_chart(
            hourly_times, hourly_uv, todays_sessions, reference["city"], target_date,
            utc_offset_seconds=utc_offset_seconds,
        )
        caption = f"📊 עקומת UV ל-{target_date.strftime('%d.%m.%Y')} עם ה-sessions שלך מסומנים עליה"
        peak = _peak_exposure_session(todays_sessions)
        if peak is not None:
            caption += f"\nהחשיפה הגבוהה ביותר: {peak['city']} ({peak['exposure_score']}%)"
        send_photo(chat_id, chart_png, caption=caption)
        logger.info("Sent daily exposure chart to chat_id=%s for %s", chat_id, target_date)
    except Exception as e:
        logger.exception("send_daily_exposure_chart failed for chat_id=%s target_date=%s", chat_id, target_date)
        _notify_admin_chart_skip(target_date, f"חריגה: {e}")


def _begin_session(
    chat_id: int,
    username: str,
    city_name: str,
    country: str | None,
    uv_index: float,
    lat: float,
    lon: float,
    clear_keyboard: bool = False,
) -> None:
    """כתיבת exposure_log + הודעת אישור — משותף לנתיב הקלדת-עיר ונתיב-מיקום."""
    now = datetime.now(timezone.utc)
    insert_row(
        "exposure_log",
        {
            "telegram_username": username,
            "city": city_name,
            "country": country,
            "start_time": now.isoformat(),
            "end_time": None,
            "uv_index": uv_index,
            "lat": lat,
            "lon": lon,
            "spf": None,
            "exposure_score": None,
        },
    )
    # מציגים גם country בהודעת האישור — כדי שאם geocode_city פענח עיר לא
    # נכונה (למשל "סן חוזה" -> ארה"ב במקום קוסטה ריקה) המשתמש יבחין מיד
    # ולא רק כשה-UV/מזג האוויר לא הגיוני.
    location_label = f"{city_name}, {country}" if country else city_name
    send_message(
        chat_id,
        f"התחלת session ב{location_label} (UV נוכחי: {uv_index:.1f}). "
        "כשתסיימו, שלחו /end_session (או /end_session <SPF> אם השתמשתם בקרם הגנה).",
        reply_markup={"remove_keyboard": True} if clear_keyboard else None,
    )
    logger.info("Started session for @%s in %s (UV=%s)", username, city_name, uv_index)
    send_uv_forecast_chart(chat_id, city_name, lat, lon, now)


def handle_start_session(chat_id: int, username: str, args: str) -> None:
    city = args.strip()
    if not _can_start_session(chat_id, username):
        return

    if not city:
        # בלי ארגומנט — מציעים כפתור מיקום במקום רק להחזיר שגיאת שימוש.
        prompt_location_share(chat_id)
        return

    with httpx.Client() as client:
        geo = geocode_city(client, city)
        if not geo["found"]:
            send_message(chat_id, f'לא הצלחתי לזהות עיר בשם "{city}". בדקו את האיות ונסו שוב.')
            return
        uv_index = get_current_uv(client, geo["latitude"], geo["longitude"])

    _begin_session(chat_id, username, geo["name"], geo["country"], uv_index, geo["latitude"], geo["longitude"])


def handle_start_session_location(chat_id: int, username: str, lat: float, lon: float) -> None:
    """
    מטפל בהודעת location שמגיעה משיתוף מיקום (כפתור request_location) —
    ראו docs/2026-08-26-location-sharing-design.md. שימוש ב-lat/lon
    המדויקים מהטלפון (לא מרכז-עיר משוער) גם עבור קריאת ה-UV.
    """
    if not _can_start_session(chat_id, username):
        return

    with httpx.Client() as client:
        geo = reverse_geocode_location(client, lat, lon)
        if not geo["found"]:
            send_message(
                chat_id,
                "לא הצלחתי לזהות עיר מהמיקום ששיתפתם. נסו /start_session <שם עיר> ידנית.",
            )
            return
        uv_index = get_current_uv(client, lat, lon)

    _begin_session(chat_id, username, geo["name"], geo["country"], uv_index, lat, lon, clear_keyboard=True)


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

    # ברירת מחדל: הדגימה הבודדת שנשמרה ב-_begin_session (התנהגות ישנה).
    # אם יש lat/lon שמורים (שורות אחרי migration ה-lat/lon) מנסים לרענן
    # לממוצע-משוקלל-משך על פני כל ה-session בפועל — ראו weighted_average_uv
    # לרציונל המלא (תיקון לתקלת מצפה רמון, 2026-09-08: session ארוך עם
    # דגימה בודדת ליד חצות הציג UV=0.0 במקום שיא אמיתי בצהריים). כשל
    # ברענון (רשת, lat/lon חסרים בשורות ישנות מלפני ה-migration, וכו')
    # נופל בחזרה בבטחה לדגימה המקורית — אף פעם לא מונע מ-/end_session
    # לסיים בהצלחה.
    uv_index = session["uv_index"]
    lat, lon = session.get("lat"), session.get("lon")
    if lat is not None and lon is not None:
        try:
            with httpx.Client() as client:
                refreshed_uv = fetch_historical_uv(client, lat, lon, start_time, end_time)
            if refreshed_uv is not None:
                uv_index = refreshed_uv
        except Exception:
            logger.exception(
                "handle_end_session: failed to refresh weighted-average UV for session id=%s — "
                "falling back to the original single-snapshot value",
                session["id"],
            )

    score = calculate_exposure_score(uv_index, duration_minutes, skin_type, spf)

    update_rows(
        "exposure_log",
        {"id": f"eq.{session['id']}"},
        {"end_time": end_time.isoformat(), "spf": spf, "exposure_score": score, "uv_index": uv_index},
    )

    send_message(
        chat_id,
        f"session הסתיים — {round(duration_minutes)} דקות ב{session['city']}. "
        f"מדד חשיפה: {score}%.",
    )
    logger.info("Ended session id=%s for @%s: score=%s", session["id"], username, score)


# ---------------------------------------------------------------------
# /add_session — רישום ידני מלא של session שכבר הסתיים, בפקודת טקסט
# אחת (בשונה מ-/offline_session שפותח Mini App). UV Index נשלף אוטומטית
# מההיסטוריה של Open-Meteo לפי העיר והשעה שצוינו; uv=<מספר> הוא escape
# hatch ידני למקרה שההיסטוריה לא זמינה (Open-Meteo תומך עד 92 יום אחורה,
# ולפעמים אין נתון גם בטווח הזה).
# ---------------------------------------------------------------------
def _parse_kv_fields(tokens: list[str], allowed_keys: set[str]) -> tuple[list[str], dict]:
    """
    מפריד רשימת טוקנים לחלק "טקסט חופשי" (בהתחלה, למשל שם עיר עם רווחים)
    ואחריו זוגות key=value. עוצר בטוקן הראשון עם "=" שהמפתח שלו מוכר,
    ואוסף את כל ה-key=value מאותה נקודה והלאה. מחזיר (free_text_tokens, fields).
    """
    for i, tok in enumerate(tokens):
        key, sep, _ = tok.partition("=")
        if sep and key in allowed_keys:
            fields = {}
            for t in tokens[i:]:
                k, s, v = t.partition("=")
                if s and k in allowed_keys:
                    fields[k] = v
            return tokens[:i], fields
    return tokens, {}


def weighted_average_uv(
    hourly_times: list[str], hourly_uv: list[float | None], start_time: datetime, end_time: datetime
) -> float | None:
    """
    ממוצע UV משוקלל-משך על פני כל טווח ה-session [start_time, end_time) —
    מחליף התאמה לשעה בודדת (הגרסה הקודמת של fetch_historical_uv), שנתנה
    תמונה שגויה ל-sessions ארוכים/רב-שעתיים. התיקון נובע מתקלה אמיתית
    ב-2026-09-08: session של 11 שעות במצפה רמון הוצג עם UV=0.0 בדשבורד,
    כי הדגימה הבודדת (בזמן פתיחת ה-session) נפלה על שעת לילה, בעוד
    שהיה שיא UV מעל 7 בצהריים אותו יום.

    כל bucket שעתי של Open-Meteo (hourly_times[i]) מייצג את הטווח
    [t, t+1h). מחשבים לכל bucket את החפיפה (בדקות) עם [start_time,
    end_time), ומחזירים ממוצע UV משוקלל לפי משך-החפיפה. זה מתמטית שקול
    לסכימת "מנת חשיפה" לפי-שעה ואז חלוקה בסך-הכל, כי calculate_exposure_score
    לינארית ב-uv_index עבור duration/skin_type/spf קבועים — כלומר אפשר
    להזין את הממוצע-המשוקלל פעם אחת לנוסחה הקיימת בלי לשנות אותה או את
    חוזה ה-DB בכלל. bucket-ים עם uv=None מדולגים. בלי שום חפיפה (או כל
    ה-UV חסר) מחזירים None ומשאירים לקורא (fetch_historical_uv) להחליט.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for t, uv in zip(hourly_times, hourly_uv):
        if uv is None:
            continue
        bucket_start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        bucket_end = bucket_start + timedelta(hours=1)
        overlap_start = max(bucket_start, start_time)
        overlap_end = min(bucket_end, end_time)
        overlap_minutes = (overlap_end - overlap_start).total_seconds() / 60
        if overlap_minutes <= 0:
            continue
        weighted_sum += uv * overlap_minutes
        total_weight += overlap_minutes
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def fetch_historical_uv(
    client: httpx.Client, lat: float, lon: float, start_time: datetime, end_time: datetime
) -> float | None:
    """
    שולף UV Index היסטורי כממוצע-משוקלל-משך על פני כל טווח ה-session
    [start_time, end_time) — ראו weighted_average_uv לרציונל המלא (כולל
    התקלה האמיתית שהובילה לתיקון הזה). מקבלת start_time/end_time כ-UTC
    "אמיתי" (aware, tzinfo=UTC) — מי שקורא לפונקציה הזו (handle_add_session/
    handle_edit_session) כבר אחראי להמיר את הזמן המקומי שהמשתמש הקליד
    ל-UTC לפני כן (ראו fetch_utc_offset_seconds למטה; תוקן ב-2026-09-08,
    לפני כן הייתה כאן הנחה שגויה ש-HH:MM שהמשתמש מקליד הוא כבר UTC).
    timezone=UTC (לא auto כמו fetch_uv_forecast_next_24h) בדיוק כדי
    ש-hourly.time יתאים ישירות בלי המרה נוספת. past_days מחושב מתאריך
    ההתחלה (start_time, לא end_time —
    ה-session כולו כבר בעבר, ו-forecast_days=1 מכסה את כל שעות "היום"
    הנוכחי, כולל אם end_time הוא today). Open-Meteo תומך עד 92 יום
    אחורה; מעבר לזה (או שהתאריך בעתיד) מחזירים None ומשאירים לקורא
    להחליט איך להגיב (הודעת שגיאה / נפילה חזרה לדגימה הישנה, לפי הנתיב).
    """
    today = datetime.now(timezone.utc).date()
    days_back = (today - start_time.date()).days
    if not (0 <= days_back <= 92):
        return None

    response = client.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "uv_index",
            "past_days": days_back,
            "forecast_days": 1,
            "timezone": "UTC",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    times: list[str] = hourly["time"]
    uvs: list[float] = hourly["uv_index"]
    return weighted_average_uv(times, uvs, start_time, end_time)


def fetch_utc_offset_seconds(client: httpx.Client, lat: float, lon: float) -> int:
    """
    נוספה ב-2026-09-08: מחזירה את הפרשי-השעות מ-UTC (בשניות) של lat/lon
    נתון, לפי ה-timezone (IANA) שבו Open-Meteo מזהה את המיקום בעצמו —
    timezone=auto גורם לתשובה לכלול utc_offset_seconds, בדיוק אותו
    mechanism שכבר בשימוש ותקין ב-fetch_uv_forecast_next_24h למעלה (ראו
    שם: זה גם מה שתיקן בעבר תחזית שהוצגה לפי UTC לניו יורק במקום השעון
    המקומי שם).

    התקלה שהובילה לפונקציה הזו: /add_session ו-/edit_session פירשו
    start=/end=HH:MM כ-UTC "כמו שהוא" בלי שום קשר למיקום בפועל — מי
    שהקליד "מצפה רמון start=5:00" התכוון ל-5 בבוקר שעון ישראל (UTC+2/+3),
    לא ל-5 בבוקר UTC (=7/8 בבוקר בישראל, כבר לא "בוקר מוקדם" בכלל).
    זה גם גרם לבדיקת "שעת הסיום לא יכולה להיות בעתיד" להיכשל/להצליח
    לפי מקרה שרירותי, כי היא השוותה UTC אמיתי מול "UTC" שגוי.

    best-effort: כשל (רשת/תשובה לא תקינה) -> מחזירה 0 (UTC), כלומר
    נופלים בחזרה בבטחה להתנהגות הישנה במקום לקרוס.
    """
    try:
        response = client.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "uv_index",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json().get("utc_offset_seconds", 0)
    except Exception:
        logger.warning("fetch_utc_offset_seconds failed for lat=%s lon=%s — falling back to UTC", lat, lon)
        return 0


def handle_add_session(chat_id: int, username: str, args: str) -> None:
    """
    /add_session <עיר> start=HH:MM end=HH:MM [spf=<מספר>] [date=D.M] [uv=<מספר>]
    לדוגמה: /add_session תל אביב start=14:00 end=16:30 spf=30

    בלי date — מניחים היום (UTC). start=/end=HH:MM מתפרשים כזמן *מקומי
    של העיר* (fetch_utc_offset_seconds, ראו שם) — לא UTC — כי ככה משתמש
    מתכוון: "מצפה רמון start=5:00" זה 5 בבוקר שעון ישראל, לא 5 בבוקר UTC.
    תוקן ב-2026-09-08 (ראו fetch_utc_offset_seconds לרציונל המלא/לתקלה
    האמיתית). end<=start מתפרש כחציית חצות (יום למחרת, בזמן המקומי),
    כמו ב-/edit_session. uv= הוא override ידני; בלעדיו שולפים UV היסטורי
    אוטומטית (fetch_historical_uv, שמקבלת כבר UTC אמיתי).
    """
    ALLOWED = {"start", "end", "spf", "date", "uv"}
    tokens = args.strip().split()
    city_tokens, fields = _parse_kv_fields(tokens, ALLOWED)
    city = " ".join(city_tokens).strip()

    usage = (
        "שימוש: /add_session <עיר> start=HH:MM end=HH:MM [spf=<מספר>] [date=D.M]\n"
        "לדוגמה: /add_session תל אביב start=14:00 end=16:30 spf=30\n"
        "(שעות בזמן המקומי של העיר; בלי date מניחים היום; UV Index נשלף "
        "אוטומטית לפי ההיסטוריה)."
    )
    if not city or "start" not in fields or "end" not in fields:
        send_message(chat_id, usage)
        return

    users = select_rows("users", {"telegram_username": f"eq.{username}"})
    if not users:
        send_message(chat_id, "קודם צריך להגדיר סוג עור: /set_skin_type <1-6>")
        return
    skin_type = users[0]["skin_type"]

    now = datetime.now(timezone.utc)
    target_date = now.date()
    if "date" in fields:
        try:
            day, month = fields["date"].split(".")
            target_date = target_date.replace(month=int(month), day=int(day))
            if target_date > now.date():
                target_date = target_date.replace(year=target_date.year - 1)
        except (ValueError, IndexError):
            send_message(chat_id, "פורמט תאריך לא תקין. השתמשו ב-date=D.M (למשל date=25.8).")
            return

    with httpx.Client() as client:
        geo = geocode_city(client, city)
        if not geo["found"]:
            send_message(chat_id, f'לא הצלחתי לזהות עיר בשם "{city}". בדקו את האיות ונסו שוב.')
            return

        # geocoding זז לפני parsing השעות (בשונה מהקוד הישן) כי צריך את
        # lat/lon כדי לדעת את אזור-הזמן המקומי לפני שאפשר בכלל להמיר
        # start=/end=HH:MM ל-UTC אמיתי.
        utc_offset_seconds = fetch_utc_offset_seconds(client, geo["latitude"], geo["longitude"])

        try:
            start_hh, start_mm = fields["start"].split(":")
            local_start = datetime(
                target_date.year, target_date.month, target_date.day,
                int(start_hh), int(start_mm),
            )
            start_time = (local_start - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            send_message(chat_id, "פורמט שעת התחלה לא תקין. השתמשו ב-start=HH:MM (למשל start=14:00).")
            return

        try:
            end_hh, end_mm = fields["end"].split(":")
            local_end = local_start.replace(hour=int(end_hh), minute=int(end_mm), second=0, microsecond=0)
            if local_end <= local_start:
                local_end += timedelta(days=1)  # session שחצה חצות (בזמן המקומי)
            end_time = (local_end - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            send_message(chat_id, "פורמט שעת סיום לא תקין. השתמשו ב-end=HH:MM (למשל end=16:30).")
            return

        if end_time > now:
            send_message(chat_id, "שעת הסיום לא יכולה להיות בעתיד.")
            return

        spf = None
        if "spf" in fields:
            if not fields["spf"].isdigit():
                send_message(chat_id, "spf חייב להיות מספר, למשל spf=30.")
                return
            spf = int(fields["spf"])

        if "uv" in fields:
            try:
                uv_index = float(fields["uv"])
            except ValueError:
                send_message(chat_id, "uv חייב להיות מספר, למשל uv=6.5.")
                return
        else:
            uv_index = fetch_historical_uv(client, geo["latitude"], geo["longitude"], start_time, end_time)
            if uv_index is None:
                send_message(
                    chat_id,
                    "לא הצלחתי לשלוף UV היסטורי לשעה/תאריך הזה (זמין עד כ-92 יום אחורה, "
                    "ולפעמים פחות). אפשר לנסות עם date= קרוב יותר, או להוסיף uv=<מספר> "
                    "ידנית לפקודה.",
                )
                return

    duration_minutes = (end_time - start_time).total_seconds() / 60
    score = calculate_exposure_score(uv_index, duration_minutes, skin_type, spf)

    insert_row(
        "exposure_log",
        {
            "telegram_username": username,
            "city": geo["name"],
            "country": geo["country"],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "uv_index": uv_index,
            "lat": geo["latitude"],
            "lon": geo["longitude"],
            "spf": spf,
            "exposure_score": score,
        },
    )

    location_label = f"{geo['name']}, {geo['country']}" if geo.get("country") else geo["name"]
    send_message(
        chat_id,
        f"נוסף: session ב{location_label} ({round(duration_minutes)} דקות, UV {uv_index:.1f}). "
        f"מדד חשיפה: {score}%.",
    )
    logger.info("Added manual session for @%s in %s: UV=%s score=%s", username, geo["name"], uv_index, score)


# ---------------------------------------------------------------------
# /my_sessions, /edit_session, /delete_session — ניהול sessions קיימים
# מתוך הבוט (בלי דשבורד/UI נפרד). נועד גם לתקן session שנתקע (כמו
# id=38 ש-uv_index=0 שלו גרם ל-ZeroDivisionError בעבר, ראו התיקון של
# calculate_exposure_score למעלה) בלי לפנות למפתח לתקן ידנית ב-SQL.
# ---------------------------------------------------------------------
def _fmt_dt(iso: str) -> str:
    """מציג timestamp כ-'D.M HH:MM' (UTC) — תואם לפורמט התאריכים בשאר הבוט."""
    dt = datetime.fromisoformat(iso)
    return f"{dt.day}.{dt.month} {dt.strftime('%H:%M')}"


def handle_my_sessions(chat_id: int, username: str) -> None:
    """
    /my_sessions — עד 8 ה-sessions האחרונים של המשתמש, עם ה-id של כל
    אחד כדי לאפשר התייחסות אליו ב-/edit_session/-/delete_session.
    """
    sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "order": "start_time.desc", "limit": "8"},
    )
    if not sessions:
        send_message(chat_id, "עוד אין לך sessions רשומים. שלחו /start_session <עיר> כדי להתחיל.")
        return

    lines = ["ה-sessions האחרונים שלך:"]
    for s in sessions:
        start = _fmt_dt(s["start_time"])
        if s["end_time"]:
            status = f"{start}–{datetime.fromisoformat(s['end_time']).strftime('%H:%M')}"
        else:
            status = f"{start}→פתוח"

        extra = []
        if s["uv_index"] is not None:
            extra.append(f"UV {s['uv_index']:.1f}")
        if s["spf"]:
            extra.append(f"SPF {s['spf']}")
        if s["exposure_score"] is not None:
            extra.append(f"ציון {s['exposure_score']}%")
        extra_str = f" · {' · '.join(extra)}" if extra else ""

        lines.append(f"#{s['id']} · {s['city']} · {status}{extra_str}")

    lines.append("")
    lines.append("למחיקה: /delete_session <מספר>")
    lines.append("לעריכה: /edit_session <מספר> end=now|HH:MM ו/או spf=<מספר>")
    lines.append("להוספת session ישן: /add_session <עיר> start=HH:MM end=HH:MM [spf=..]")
    send_message(chat_id, "\n".join(lines))


def handle_today(chat_id: int, username: str, args: str) -> None:
    """
    /today [date=D.M] — אנליזה יומית: סה"כ מדד חשיפה על כל ה-sessions
    הסגורים שהתחילו ביום המבוקש (UTC, כמו שאר הזמנים באפליקציה; בלי
    date= — היום), + לכל session השוואה "מה היה קורה עם SPF קבוע"
    (DAILY_SUMMARY_REFERENCE_SPF, ראו שם) — כולל sessions שכבר השתמשו
    ב-SPF כלשהו (ההשוואה תמיד מול אותו קבוע, לא רק "בלי הגנה בכלל").
    session פתוח כרגע לא נכלל בסכימה (עדיין אין לו exposure_score מחושב)
    אבל מוזכר בנפרד עם תזכורת ל-/end_session.

    date=D.M הוחלט עם המשתמש ב-2026-09-08 ("הוסף chart_date ככה שאוכל
    לראות את החשיפה לפי תאריך") — אותו פורמט/פרסינג בדיוק כמו
    date= ב-/add_session (כולל גלגול-שנה כש-D.M "עתידי" ביחס להיום),
    לעקביות. גם הגרף (send_daily_exposure_chart) מקבל את אותו target_date.

    באותה בקשה: "שהגרף יציג את המיקום איפה שמד החשיפה היה הגבוה ביותר"
    — מומש ב-_peak_exposure_session (משותף עם render_daily_exposure_chart):
    מוצג גם כאן בטקסט וגם מודגש חזותית על הגרף.

    שולף עד 50 sessions אחרונים ומסנן ליום המבוקש בפייתון (לא ב-query
    עם range filter על start_time) — פשוט יותר לבדיקה, וקצב שימוש-קורס
    שלא מצדיק אופטימיזציה מוקדמת (ראו handle_my_sessions לדפוס דומה).
    """
    args = args.strip()
    target_date = datetime.now(timezone.utc).date()
    if args:
        if not args.startswith("date="):
            send_message(chat_id, "שימוש: /today או /today date=D.M (למשל date=25.8).")
            return
        try:
            day, month = args[len("date="):].split(".")
            candidate = target_date.replace(month=int(month), day=int(day))
            if candidate > target_date:
                candidate = candidate.replace(year=candidate.year - 1)
            target_date = candidate
        except (ValueError, IndexError):
            send_message(chat_id, "פורמט תאריך לא תקין. השתמשו ב-date=D.M (למשל date=25.8).")
            return

    users = select_rows("users", {"telegram_username": f"eq.{username}"})
    skin_type = users[0]["skin_type"] if users else None

    sessions = select_rows(
        "exposure_log",
        {"telegram_username": f"eq.{username}", "order": "start_time.desc", "limit": "50"},
    )
    todays_sessions = _sessions_on_date(sessions, target_date)

    closed = [s for s in todays_sessions if s["end_time"] and s["exposure_score"] is not None]
    open_sessions = [s for s in todays_sessions if not s["end_time"]]

    if not closed and not open_sessions:
        if target_date == datetime.now(timezone.utc).date():
            send_message(chat_id, "עוד אין לך sessions היום. שלחו /start_session <עיר> כדי להתחיל.")
        else:
            send_message(chat_id, f"אין לך sessions בתאריך {target_date.strftime('%d.%m.%Y')}.")
        return

    lines = [f"📊 סיכום ליום {target_date.strftime('%d.%m.%Y')}:"]

    if closed:
        summaries = [_daily_session_summary(s, skin_type, DAILY_SUMMARY_REFERENCE_SPF) for s in closed]
        total_actual = sum(sm["actual_score"] for sm in summaries)
        total_hypothetical = sum(sm["hypothetical_score"] for sm in summaries)
        lines.append(f'{len(closed)} sessions · סה"כ מדד חשיפה: {total_actual}%')
        lines.append(f"עם SPF {DAILY_SUMMARY_REFERENCE_SPF} קבוע לאורך כל היום: כ-{total_hypothetical}% במקום זאת")

        peak = _peak_exposure_session(closed)
        if peak is not None:
            lines.append(f"מדד החשיפה הגבוה ביותר: {peak['city']} ({peak['exposure_score']}%)")

        lines.append("")
        for s, sm in zip(closed, summaries):
            spf_label = f"SPF {sm['spf']}" if sm["spf"] else "בלי קרם הגנה"
            lines.append(
                f"#{sm['id']} · {sm['city']} · UV {s['uv_index']:.1f} · {spf_label} · "
                f"ציון {sm['actual_score']}% (עם SPF {DAILY_SUMMARY_REFERENCE_SPF}: {sm['hypothetical_score']}%)"
            )
        lines.append("")

    if open_sessions:
        cities = ", ".join(s["city"] for s in open_sessions)
        lines.append(f"יש לך גם session פתוח כרגע ב-{cities} — הוא יתווסף לסיכום אחרי /end_session.")

    send_message(chat_id, "\n".join(lines).strip())
    send_daily_exposure_chart(chat_id, todays_sessions, target_date)


def handle_delete_session(chat_id: int, username: str, args: str) -> None:
    """/delete_session <id> — מוחק session, רק אם הוא שייך למשתמש שביקש."""
    session_id = args.strip()
    if not session_id.isdigit():
        send_message(chat_id, "שימוש: /delete_session <מספר> (ראו /my_sessions למספרים).")
        return

    rows = select_rows("exposure_log", {"id": f"eq.{session_id}"})
    if not rows or rows[0]["telegram_username"] != username:
        # אותה הודעה גם אם ה-id שייך למישהו אחר וגם אם הוא לא קיים —
        # לא חושפים למשתמש אם id מסוים "תפוס" ע"י מישהו אחר.
        send_message(chat_id, "לא נמצא session כזה. שלחו /my_sessions לרשימה מעודכנת.")
        return

    session = rows[0]
    delete_rows("exposure_log", {"id": f"eq.{session_id}"})
    send_message(chat_id, f"נמחק: session #{session_id} ב{session['city']} ({_fmt_dt(session['start_time'])}).")
    logger.info("Deleted session id=%s for @%s", session_id, username)


def handle_edit_session(chat_id: int, username: str, args: str) -> None:
    """
    /edit_session <id> [end=now|HH:MM] [spf=<מספר>] — עריכת session קיים
    (סוגר session תקוע, מתקן SPF ששכחו לציין וכו'). לפחות אחד מ-end/spf
    חייב להינתן. end=HH:MM מתפרש כזמן *מקומי* של ה-session (לפי lat/lon
    שלו, אם שמור — fetch_utc_offset_seconds; תוקן ב-2026-09-08 יחד עם
    התיקון הזהה ב-/add_session, ראו שם לרציונל המלא) על אותו יום קלנדרי
    מקומי כמו start_time — אם השעה "לפני" שעת ההתחלה המקומית, מניחים
    חציית חצות ומזיזים ליום הבא. session ישן בלי lat/lon שמור (מלפני
    ה-migration) נופל בחזרה ל-UTC "כמו שהוא", ההתנהגות הישנה. מדד
    החשיפה מחושב מחדש בכל עריכה שיש אחריה end_time (חדש או קיים) —
    אחרת נשאר None, בדיוק כמו session פתוח רגיל (יחושב סופית ב-/end_session).
    """
    parts = args.strip().split()
    if not parts or not parts[0].isdigit():
        send_message(
            chat_id,
            "שימוש: /edit_session <מספר> end=now|HH:MM ו/או spf=<מספר>\n"
            "לדוגמה: /edit_session 38 end=now spf=30\n"
            "(ראו /my_sessions למספרים).",
        )
        return

    session_id = parts[0]
    fields = {}
    for token in parts[1:]:
        key, sep, value = token.partition("=")
        if sep and key in ("end", "spf"):
            fields[key] = value

    if not fields:
        send_message(chat_id, "צריך לציין לפחות end=... או spf=... לעריכה.")
        return

    rows = select_rows("exposure_log", {"id": f"eq.{session_id}"})
    if not rows or rows[0]["telegram_username"] != username:
        send_message(chat_id, "לא נמצא session כזה. שלחו /my_sessions לרשימה מעודכנת.")
        return
    session = rows[0]

    patch = {}

    if "spf" in fields:
        if not fields["spf"].isdigit():
            send_message(chat_id, "spf חייב להיות מספר, למשל spf=30.")
            return
        patch["spf"] = int(fields["spf"])

    if "end" in fields:
        start_time = datetime.fromisoformat(session["start_time"])
        if fields["end"].lower() == "now":
            end_time = datetime.now(timezone.utc)
        else:
            lat, lon = session.get("lat"), session.get("lon")
            utc_offset_seconds = 0
            if lat is not None and lon is not None:
                with httpx.Client() as client:
                    utc_offset_seconds = fetch_utc_offset_seconds(client, lat, lon)
            try:
                hh, mm = fields["end"].split(":")
                local_start = (start_time + timedelta(seconds=utc_offset_seconds)).replace(tzinfo=None)
                local_end = local_start.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                if local_end <= local_start:
                    local_end += timedelta(days=1)  # session שחצה חצות (בזמן המקומי)
                end_time = (local_end - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                send_message(chat_id, "פורמט שעה לא תקין. השתמשו ב-end=now או end=HH:MM (למשל end=22:30).")
                return
        patch["end_time"] = end_time.isoformat()

    effective_end = patch.get("end_time", session["end_time"])
    effective_spf = patch.get("spf", session["spf"])
    if effective_end:
        end_dt = datetime.fromisoformat(effective_end)
        start_dt = datetime.fromisoformat(session["start_time"])
        duration_minutes = (end_dt - start_dt).total_seconds() / 60
        if duration_minutes < 0:
            send_message(chat_id, "שעת הסיום לא יכולה להיות לפני שעת ההתחלה.")
            return
        users = select_rows("users", {"telegram_username": f"eq.{username}"})
        skin_type = users[0]["skin_type"] if users else 3
        patch["exposure_score"] = calculate_exposure_score(
            session["uv_index"], duration_minutes, skin_type, effective_spf
        )

    update_rows("exposure_log", {"id": f"eq.{session_id}"}, patch)
    send_message(chat_id, f"עודכן: session #{session_id} ב{session['city']}.")
    logger.info("Edited session id=%s for @%s: %s", session_id, username, patch)


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------
COMMAND_HANDLERS = {
    "/dashboard": lambda chat_id, username, args: handle_dashboard(chat_id, username),
    "/set_skin_type": handle_set_skin_type,
    "/start_session": handle_start_session,
    "/end_session": handle_end_session,
    "/offline_session": handle_offline_session,
    "/my_sessions": lambda chat_id, username, args: handle_my_sessions(chat_id, username),
    "/today": handle_today,
    "/delete_session": handle_delete_session,
    "/edit_session": handle_edit_session,
    "/add_session": handle_add_session,
    "/diagnose_skin": handle_diagnose_skin,
}


def handle_update(update: dict) -> None:
    """
    מטפל בעדכון בודד מ-getUpdates: אחת מ-4 הפקודות, תמונה בודדת (הצעת
    סוג עור), או שום דבר (מתעלמים משאר סוגי ההודעות — לא זרימת שיחה
    מלאה עדיין).
    """
    message = update.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username")

    text = (message.get("text") or "").strip()
    photo_sizes = message.get("photo")  # רשימת PhotoSize מהקטנה לגדולה, או None
    location = message.get("location")  # {"latitude": ..., "longitude": ...} או None

    if not photo_sizes and not location and not text.startswith("/"):
        return

    if not username:
        send_message(chat_id, "צריך שיהיה לך username מוגדר בהגדרות טלגרם כדי להשתמש בפקודות האלה.")
        return

    # רענון הזדמנותי של chat_id על כל הודעה — לא insert (PATCH בלבד),
    # אז אם עוד אין שורת users למשתמש הזה (לא קבע סוג עור מעולם) זה
    # פשוט לא פוגע בכלום. מכסה משתמשים שקבעו סוג עור *לפני* שהיה
    # chat_id בכלל. ראו docs/2026-08-26-multi-user-broadcast-design.md.
    try:
        update_rows("users", {"telegram_username": f"eq.{username}"}, {"chat_id": chat_id})
    except SupabaseError as e:
        logger.warning("Failed to refresh chat_id for @%s: %s", username, e)

    if photo_sizes:
        largest_photo = photo_sizes[-1]
        # /diagnose_skin "תופס" את התמונה הבאה (בתוך חלון הזמן), אחרת
        # ברירת המחדל הקיימת נשארת — הצעת סוג עור. ראו ההערה מעל
        # _pending_diagnose_skin להסבר המלא על הבחירה הזו.
        expires_at = _pending_diagnose_skin.pop(username, None)
        if expires_at is not None and datetime.now(timezone.utc) < expires_at:
            handle_skin_damage_photo(chat_id, username, largest_photo["file_id"])
        else:
            handle_skin_type_photo(chat_id, username, largest_photo["file_id"])
        return

    if location:
        handle_start_session_location(chat_id, username, location["latitude"], location["longitude"])
        return

    command, _, args = text.partition(" ")
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        return  # פקודה לא מוכרת — מתעלמים

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
            # קריאת ה-getUpdates עצמה עטופה עכשיו ב-try/except (בעבר לא
            # הייתה עטופה — 409 Conflict אמיתי מטלגרם, למשל משני מאזינים
            # על אותו טוקן, הפיל את כל התהליך עם unhandled exception).
            # כשל חד-פעמי (409, timeout, 5xx רגעי) נרשם ללוג ומנסים שוב
            # אחרי המתנה קצרה, במקום להפיל את הבוט כולו.
            try:
                params = {"timeout": 30}
                if offset is not None:
                    params["offset"] = offset
                response = client.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35.0)
                response.raise_for_status()
                updates = response.json().get("result", [])
            except Exception:
                logger.exception("getUpdates failed — retrying in 5s")
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception:
                    logger.exception("Failed to handle update: %s", update)


if __name__ == "__main__":
    poll_forever()
