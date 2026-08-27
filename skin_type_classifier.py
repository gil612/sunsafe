"""
SunSafe — הערכת סוג עור (Fitzpatrick) מתמונה
----------------------------------------------
קריאה חד-פעמית ל-Gemini (multimodal) שמעריכה סוג עור לפי תמונה. **הצעה
בלבד** — לא כותבת שום דבר ל-DB, ולא שומרת את התמונה בשום מקום. הזרימה
המלאה (הורדת התמונה מטלגרם, קריאה לפונקציה הזו, ושליחת ההודעה חזרה
למשתמש) נמצאת ב-bot_commands.py; המשתמש עדיין חייב לאשר/לתקן דרך
/set_skin_type הקיים כדי שהערך בפועל יישמר.

ראה docs/2026-08-26-skin-type-photo-design.md לרציונל המלא — בפרט,
למה זו הערכה משוערת בלבד ולא קביעה אוטומטית.

לא עובר דרך ה-Agent Loop / MCP (mcp_agent_loop.py) בכוונה: זו קריאה
חד-פעמית בלי Tool Use בכלל (המודל לא צריך לקרוא לשום כלי כדי לסווג
תמונה) — agent_loop_mcp מיועד לזרימות עם קריאות-כלים איטרטיביות, מה
שרק מוסיף מורכבות מיותרת כאן. במקום זה, יש כאן עותק מקומי קטן של
make_client (כמו ש-bot_commands.py כבר עושה ל-geocode_city/get_current_uv
מ-mcp_weather_server.py — אותה סיבה: הימנעות מייבוא כבד של תשתית MCP
בשביל פונקציה אחת).
"""

import json
import logging
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logger = logging.getLogger("sunsafe.skin_type_classifier")

# אותו מודל בו כבר משתמש mcp_agent_loop.py — עקביות.
CLASSIFIER_MODEL = "gemini-3.5-flash-lite"

SKIN_TYPE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "detected": {
            "type": "BOOLEAN",
            "description": "true אם ניתן לזהות עור אנושי בבירור בתמונה.",
        },
        "skin_type": {
            "type": "INTEGER",
            "description": "1-6 בסולם Fitzpatrick, רלוונטי רק אם detected=true.",
        },
        "confidence": {
            "type": "STRING",
            "enum": ["low", "medium", "high"],
        },
        "reasoning": {
            "type": "STRING",
            "description": "הסבר קצר בעברית (משפט אחד).",
        },
    },
    "required": ["detected", "confidence", "reasoning"],
}

CLASSIFICATION_PROMPT = (
    "התמונה המצורפת היא תצלום של עור אדם (לרוב זרוע או יד), לצורך הערכה "
    "גסה של סוג עור בסולם Fitzpatrick (I-VI), כחלק מאפליקציית מעקב חשיפה "
    "לשמש בשם SunSafe.\n\n"
    "חשוב מאוד: זו הערכה חזותית משוערת בלבד, מבוססת אך ורק על גוון העור "
    "הנראה בתמונה (תלוי בתאורה ואיזון צבעים של המצלמה) — לא שאלון רשמי "
    "המבוסס על היסטוריית שרפות-שמש/שיזוף בפועל, ולכן פחות מדויקת. התשובה "
    "שלך תוצג למשתמש כהצעה בלבד לאישור/תיקון ידני — לא כקביעה סופית.\n\n"
    "אם ניתן לזהות עור אנושי בבירור בתמונה: קבע detected=true, skin_type "
    "כמספר שלם 1-6 (1=הכי בהיר, נוטה להישרף כמעט תמיד ולא להשתזף; "
    "6=הכי כהה, כמעט אף פעם לא נשרף), confidence (low/medium/high לפי "
    "כמה ברור לך העור בתמונה), ו-reasoning קצר בעברית שמסביר את ההערכה.\n\n"
    "אם התמונה לא מראה עור אנושי בבירור (לא רלוונטי, מטושטש, זווית/תאורה "
    "גרועה מדי כדי להעריך): קבע detected=false, השאר skin_type ריק, "
    "ותן reasoning שמסביר למה — למשל שהמשתמש צריך לשלוח תמונה ברורה יותר."
)


def make_client() -> genai.Client:
    """Gemini Developer API — זהה ל-make_client ב-mcp_agent_loop.py."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY לא מוגדר. הוסיפו אותו ל-.env. "
            "מקבלים מפתח חינמי דרך https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


def classify_skin_type_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    שולח תמונה בודדת ל-Gemini ומחזיר את ה-JSON הגולמי (dict) לפי
    SKIN_TYPE_RESPONSE_SCHEMA. אין כאן שום כתיבה ל-DB ואין שמירה של
    התמונה — הבייטים משמשים רק לקריאה הזו ונזרקים מיד אחריה.

    מחזיר את הפלט הגולמי מהמודל בלבד — **חובה** להעביר דרך
    validate_classification לפני שימוש בפועל (לא סומכים עיוור על
    Structured Output, גם עם schema אכוף).
    """
    client = make_client()
    response = client.models.generate_content(
        model=CLASSIFIER_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            CLASSIFICATION_PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SKIN_TYPE_RESPONSE_SCHEMA,
        ),
    )
    raw = json.loads(response.text)
    logger.info("classify_skin_type_from_image -> %s", raw)
    return raw


def validate_classification(raw: dict) -> dict:
    """
    פונקציה טהורה (בלי רשת/DB) שמאמתת ומנקה את הפלט הגולמי של המודל.
    אותו עיקרון כמו isTokenExpired/buildDashboardBody ב-Edge Function
    של ה-Magic Link: לוגיקה טהורה נפרדת מה-I/O, קלה לבדיקה בלי תלות
    ברשת. לא סומכים על התוצאה של Gemini גם כשיש response_schema —
    המודל עדיין יכול להחזיר detected=true בלי skin_type, מספר מחוץ
    לטווח 1-6, וכו'.

    מחזיר תמיד dict עם מפתח "ok":
    - ok=False, "reason": str — לא ניתן לזהות/הפלט לא תקין/חסר.
    - ok=True, "skin_type": int (1-6), "confidence": str, "reasoning": str.
    """
    if not isinstance(raw, dict) or not raw.get("detected"):
        reason = (raw or {}).get("reasoning") or "לא זוהה עור אנושי בבירור בתמונה."
        return {"ok": False, "reason": reason}

    try:
        skin_type = int(raw.get("skin_type"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "המודל לא החזיר סוג עור תקין."}

    if not (1 <= skin_type <= 6):
        return {"ok": False, "reason": f"המודל החזיר סוג עור מחוץ לטווח התקין ({skin_type})."}

    return {
        "ok": True,
        "skin_type": skin_type,
        "confidence": raw.get("confidence", "low"),
        "reasoning": raw.get("reasoning", ""),
    }
