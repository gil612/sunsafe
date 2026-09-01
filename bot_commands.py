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
from supabase_client import SupabaseError, delete_rows, insert_row, select_rows, update_rows, upsert_row

load_dotenv()

logger = logging.getLogger("sunsafe.bot_commands")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
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


def render_uv_forecast_chart(hourly_times: list[str], hourly_uv: list[float], city_name: str) -> bytes:
    """
    מרנדר תרשים עמודות PNG (matplotlib, in-memory — io.BytesIO, בלי כתיבה
    לדיסק) של תחזית UV להמשך היום. צבע כל עמודה לפי דרגת UV Index (תקן
    WHO: Low/Moderate/High/Very High) וממופה לפלטת הסטטוס הקיימת של
    SunSafe (--status-good/warning/serious/critical, ראו deploy_staging
    /index.html) — כדי שהצבעים יהיו עקביים עם שאר האפליקציה.
    import מקומי (לא בראש הקובץ) בכוונה: אם matplotlib חסר בסביבת
    ה-deploy, רק הפיצ'ר הזה נכשל (ונתפס ב-send_uv_forecast_chart) —
    שאר הבוט (כולל /start_session עצמו) ממשיך לעבוד כרגיל.
    """
    import matplotlib
    matplotlib.use("Agg")  # רינדור ל-buffer בלבד, בלי חלון/תצוגה — נדרש בסביבת שרת
    import matplotlib.pyplot as plt

    STATUS_COLORS = {
        "good": "#0ca30c",
        "warning": "#fab219",
        "serious": "#ec835a",
        "critical": "#d03b3b",
    }

    def status_for_uv(uv: float) -> str:
        # דרגות UV Index רשמיות של WHO (Low 0-2 / Moderate 3-5 / High 6-7 /
        # Very High+Extreme 8+), מכווצות לארבע רמות הסטטוס הקיימות באפליקציה.
        if uv < 3:
            return "good"
        if uv < 6:
            return "warning"
        if uv < 8:
            return "serious"
        return "critical"

    hours = [datetime.fromisoformat(t).strftime("%H:%M") for t in hourly_times]
    colors = [STATUS_COLORS[status_for_uv(uv)] for uv in hourly_uv]

    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=150)
    bars = ax.bar(hours, hourly_uv, color=colors, width=0.65, zorder=3)

    ax.set_title("UV Forecast — Next 24 Hours", fontsize=13, pad=12)
    ax.set_ylabel("UV Index")
    ax.set_ylim(bottom=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    plt.xticks(rotation=60, ha="right", fontsize=7)

    # תווית ישירה רק על שיא ה-UV (הערך הכי שימושי, לא על כל 24 העמודות —
    # זה היה עמוס מדי לקריאה). ראו dataviz skill: "selective direct labels".
    if hourly_uv:
        peak_idx = max(range(len(hourly_uv)), key=lambda i: hourly_uv[i])
        peak_bar = bars[peak_idx]
        ax.annotate(
            f"peak {hourly_uv[peak_idx]:.1f}",
            (peak_bar.get_x() + peak_bar.get_width() / 2, peak_bar.get_height()),
            textcoords="offset points", xytext=(0, 4),
            ha="center", fontsize=8, color="#333333", fontweight="bold",
        )

    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, color=color, label=label)
        for label, color in [
            ("Low", STATUS_COLORS["good"]),
            ("Moderate", STATUS_COLORS["warning"]),
            ("High", STATUS_COLORS["serious"]),
            ("Very High", STATUS_COLORS["critical"]),
        ]
    ]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=7, frameon=False)

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
            "spf": None,
            "exposure_score": None,
        },
    )
    send_message(
        chat_id,
        f"התחלת session ב{city_name} (UV נוכחי: {uv_index:.1f}). "
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
    send_message(chat_id, "\n".join(lines))


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
    חייב להינתן. HH:MM מתפרש כאותו יום קלנדרי כמו start_time — אם השעה
    "לפני" שעת ההתחלה, מניחים חציית חצות ומזיזים ליום הבא. מדד החשיפה
    מחושב מחדש בכל עריכה שיש אחריה end_time (חדש או קיים) — אחרת נשאר
    None, בדיוק כמו session פתוח רגיל (יחושב סופית ב-/end_session).
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
            try:
                hh, mm = fields["end"].split(":")
                end_time = start_time.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                if end_time <= start_time:
                    end_time += timedelta(days=1)  # session שחצה חצות
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
    "/delete_session": handle_delete_session,
    "/edit_session": handle_edit_session,
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
