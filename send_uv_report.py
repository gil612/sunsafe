"""
SunSafe — חיבור Agent Loop (דרך MCP) ל-Telegram
--------------------------------------------------
לוקח את התשובה החופשית מה-Agent Loop — דרך mcp_agent_loop.py (המתחבר
ל-mcp_weather_server.py כ-MCP Client) — ושולח אותה בפועל כהודעת Telegram,
עם המרה בטוחה של פורמט ה-Markdown שג'מיני מייצר (**bold**) לפורמט
MarkdownV2 שטלגרם דורש, כולל Fallback לטקסט רגיל אם ההמרה נכשלת.

העיר ניתנת כארגומנט שורת-פקודה (argv); אם לא סופקה, נשתמש בברירת
המחדל (DEFAULT_CITY). לא מספקים קואורדינטות ידנית — הסוכן עצמו אחראי
להסיק lat/lon מתאימים לעיר לפי הידע שלו, ולקרוא לכלי get_current_uv.

הרצה:
    python send_uv_report.py                 # עיר ברירת המחדל, ל-CHAT_ID מ-.env
    python send_uv_report.py "אילת"           # עיר לפי בחירה, ל-CHAT_ID מ-.env
    python send_uv_report.py --broadcast      # דוח יזום לכל המשתמשים הרשומים
                                               # (chat_id+עיר לכל אחד בנפרד —
                                               # ראו docs/2026-08-26-multi-user-
                                               # broadcast-design.md)
"""

import logging
import sys
import time

from supabase_client import SupabaseError, select_rows
from telegram_client import TelegramClient, TelegramError, escape_markdown_v2
from mcp_agent_loop import run as run_agent_via_mcp

logger = logging.getLogger("sunsafe.integration")
logging.basicConfig(level=logging.INFO)

DEFAULT_CITY = "תל אביב"

# השהיה קטנה בין הודעה להודעה בשידור ל-broadcast — לא בעיית rate-limit
# אמיתית בהיקף הנוכחי (כמות משתמשי-קורס קטנה), אבל נוהג טוב שלא
# להצמיד N בקשות ל-Telegram API בבת אחת.
BROADCAST_DELAY_SECONDS = 0.5


def convert_gemini_markdown_to_telegram_v2(text: str) -> str:
    """
    ג'מיני מחזיר טקסט עם **bold** (Markdown רגיל).
    טלגרם ב-MarkdownV2 דורש *bold* (כוכבית בודדת) וגם escape לכל
    שאר התווים המיוחדים. הפונקציה מפצלת לפי **, הופכת כל קטע זוגי
    ל-bold, ובורחת מכל השאר.
    """
    segments = text.split("**")
    converted = []
    for i, segment in enumerate(segments):
        escaped = escape_markdown_v2(segment)
        if i % 2 == 1:  # קטע זוגי = היה בין שני **
            converted.append(f"*{escaped}*")
        else:
            converted.append(escaped)
    return "".join(converted)


def build_uv_task(city: str) -> str:
    """
    בונה את המשימה (task) שנשלחת ל-Agent Loop עבור עיר נתונה.

    זו משימה דו-שלבית מכוונת: קודם geocode_city (ולידציה + קואורדינטות
    מדויקות מ-Open-Meteo Geocoding), ורק אח"כ get_current_uv. בכוונה
    *לא* סומכים יותר על ניחוש lat/lon מתוך "הידע" של המודל — ראינו
    בבדיקות שגם קלט עם שגיאת הקלדה (למשל "חיםה") מניב תשובה בביטחון
    מלא, בלי שום אינדיקציה שהעיר בעצם לא זוהתה בוודאות. עכשיו יש כלי
    ייעודי שמחזיר found=False באופן מפורש כשלא נמצאה התאמה, וה-Agent
    מונחה להחזיר הודעת שגיאה ברורה במקום לנחש.

    הפורמט המבוקש לתשובה (הצלחה/כשלון) נאכף כאן בפרומפט, כדי לשמור
    על עקביות ההודעה שנשלחת בפועל לטלגרם.
    """
    return (
        f'המשתמש ביקש את מדד ה-UV הנוכחי עבור העיר: "{city}".\n\n'
        "בצע בדיוק לפי הצעדים הבאים:\n"
        f'1. קרא לכלי geocode_city עם city_name="{city}" כדי לאתר קואורדינטות '
        "מדויקות. אסור לנחש lat/lon בעצמך — תמיד להתבסס על תוצאת הכלי.\n"
        "2. אם found=False (לא נמצאה עיר תואמת): החזר תשובה אחת בלבד, "
        "בעברית, בדיוק במבנה הזה (ללא תוספות):\n"
        f'   "לא הצלחתי לזהות עיר בשם \\"{city}\\". בדקו את האיות ונסו שוב."\n\n'
        "3. אם found=True: קרא לכלי get_current_uv עם ה-latitude/longitude "
        "שהתקבלו מ-geocode_city (לא ערכים אחרים). סווג את התוצאה לפי "
        "הסולם הבינלאומי הרגיל של UV Index: 0–2 נמוך (Low), 3–5 בינוני "
        "(Moderate), 6–7 גבוה (High), 8–10 גבוה מאוד (Very High), "
        "11+ קיצוני (Extreme). החזר תשובה אחת קצרה בעברית בלבד, בדיוק "
        "במבנה הבא (ללא כותרות או תוספות אחרות), תוך שימוש בשם העיר "
        "הרשמי/המתוקן שהתקבל מ-geocode_city (השדה name). הדגש עם "
        "**bold** (כוכביות כפולות) בדיוק שני חלקים: את המספר של מדד "
        "ה-UV, ואת מצב/סיווג ה-UV (התיאור בעברית + הסיווג באנגלית "
        "בסוגריים יחד) — אל תוסיף הדגשות או פורמט נוסף בשום מקום אחר:\n\n"
        "   אם נדרשת הגנה מהשמש (רמה 3 ומעלה בסולם):\n"
        '   "מדד ה-UV הנוכחי ב<שם העיר> הוא כ-**<המספר, עיגול לספרה '
        "עשרונית אחת>**. זהו מדד **<התיאור בעברית> (<הסיווג באנגלית>)**, "
        'ולכן כן צריך הגנה מהשמש (כובע, משקפי שמש, קרם הגנה והימנעות '
        'מחשיפה ישירה בשעות השיא)."\n\n'
        "   אם לא נדרשת הגנה מיוחדת (רמה 1–2 בסולם):\n"
        '   "מדד ה-UV הנוכחי ב<שם העיר> הוא כ-**<המספר, עיגול לספרה '
        "עשרונית אחת>**. זהו מדד **<התיאור בעברית> (<הסיווג באנגלית>)**, "
        'ולכן אין צורך מיוחד בהגנה מהשמש כרגע."'
    )


def send_agent_answer_to_telegram(
    task: str,
    client: TelegramClient | None = None,
    chat_id: str | int | None = None,
) -> str:
    """
    מריץ את ה-Agent Loop (דרך MCP) על task נתון, ושולח את התשובה לטלגרם.
    מנסה קודם MarkdownV2 מפורמט; אם טלגרם מחזיר שגיאת פרסינג —
    שולח שוב כטקסט רגיל, כדי שההדגמה תמיד תצליח.
    מחזיר את הטקסט הגולמי שהוחזר מה-Agent (שימושי גם ללוגים/בדיקות).
    """
    client = client or TelegramClient()

    logger.info("Running MCP agent loop for task: %s", task)
    answer = run_agent_via_mcp(task)
    logger.info("Agent answer: %s", answer)

    formatted = convert_gemini_markdown_to_telegram_v2(answer)

    try:
        client.send_text_message(formatted, chat_id=chat_id, parse_mode="MarkdownV2")
        logger.info("Sent to Telegram with MarkdownV2 formatting")
    except TelegramError as e:
        logger.warning("MarkdownV2 send failed (%s) — retrying as plain text", e)
        client.send_text_message(answer, chat_id=chat_id, parse_mode=None)
        logger.info("Sent to Telegram as plain text (fallback)")

    return answer


def run_uv_report_for_city(city: str) -> str:
    """
    פונקציית הפעולה הראשית: מקבלת שם עיר, בונה את המשימה המתאימה,
    מריצה אותה דרך ה-Agent Loop, ושולחת את התוצאה לטלגרם.

    זו נקודת הכניסה שנקראת גם מ-__main__ (לפי argv) וגם ניתנת
    לשימוש חוזר מקוד אחר (בדיקות, endpoint עתידי וכו').
    """
    task = build_uv_task(city)
    return send_agent_answer_to_telegram(task)


def _latest_city_for_user(telegram_username: str) -> str:
    """
    עיר לדוח היזום של משתמש נתון: העיר מתוך ה-exposure_log *האחרון*
    שלו (מ-/start_session), או DEFAULT_CITY אם עדיין אין לו שום
    היסטוריה. בלי שדה "עיר מועדפת" חדש ב-DB — ראו
    docs/2026-08-26-multi-user-broadcast-design.md, סעיף 3.
    """
    sessions = select_rows(
        "exposure_log",
        {
            "telegram_username": f"eq.{telegram_username}",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if sessions:
        return sessions[0]["city"]
    return DEFAULT_CITY


def run_uv_report_for_all_users() -> list[dict]:
    """
    דוח יזום לכל המשתמשים הרשומים עם chat_id שמור (ראו
    docs/2026-08-26-multi-user-broadcast-design.md) — במקום CHAT_ID
    אחד קבוע מ-.env. כשל אצל משתמש בודד לא עוצר את השאר — כל איטרציה
    עטופה בנפרד ונרשמת ללוג. מחזיר רשימת תוצאות (למשתמש: status +
    עיר, או status="error"+סיבה) — שימושי לבדיקות/סיכום הרצה.
    """
    users = select_rows("users", {"chat_id": "not.is.null"})
    logger.info("Broadcasting UV report to %d user(s) with a saved chat_id", len(users))

    client = TelegramClient()
    results = []
    for user in users:
        username = user["telegram_username"]
        chat_id = user["chat_id"]
        try:
            city = _latest_city_for_user(username)
            task = build_uv_task(city)
            send_agent_answer_to_telegram(task, client=client, chat_id=chat_id)
            logger.info("Broadcast sent to @%s (chat_id=%s, city=%s)", username, chat_id, city)
            results.append({"username": username, "status": "sent", "city": city})
        except (SupabaseError, TelegramError) as e:
            logger.error("Broadcast failed for @%s (chat_id=%s): %s", username, chat_id, e)
            results.append({"username": username, "status": "error", "reason": str(e)})
        except Exception as e:  # שגיאה לא-צפויה (Agent Loop/MCP וכו') — לא עוצרת את שאר המשתמשים
            logger.exception("Unexpected broadcast failure for @%s (chat_id=%s)", username, chat_id)
            results.append({"username": username, "status": "error", "reason": str(e)})
        time.sleep(BROADCAST_DELAY_SECONDS)

    sent = sum(1 for r in results if r["status"] == "sent")
    logger.info("Broadcast done: %d/%d sent successfully", sent, len(results))
    return results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--broadcast":
        run_uv_report_for_all_users()
    else:
        city = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CITY
        answer = run_uv_report_for_city(city)
        print(f"ההודעה נשלחה לטלגרם עבור {city} (דרך MCP):")
        print(answer)
