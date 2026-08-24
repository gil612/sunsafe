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
    python send_uv_report.py                 # עיר ברירת המחדל
    python send_uv_report.py "אילת"           # עיר לפי בחירה
"""

import logging
import sys

from telegram_client import TelegramClient, TelegramError, escape_markdown_v2
from mcp_agent_loop import run as run_agent_via_mcp

logger = logging.getLogger("sunsafe.integration")
logging.basicConfig(level=logging.INFO)

DEFAULT_CITY = "תל אביב"


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

    זו משימה מכוונת סביב geocode_city (ולידציה + קואורדינטות מדויקות
    מ-Open-Meteo Geocoding), ורק אח"כ get_current_uv. בכוונה *לא*
    סומכים על ניחוש lat/lon מתוך "הידע" של המודל — ראינו בבדיקות שגם
    קלט עם שגיאת הקלדה (למשל "חיםה") מניב תשובה בביטחון מלא, בלי שום
    אינדיקציה שהעיר בעצם לא זוהתה בוודאות מול מקור אמיתי.

    Open-Meteo Geocoding עצמו לא סובלני לשגיאות הקלדה (אפילו קידומת
    מדויקת כמו "חי" לא מחזירה תוצאה) — לכן תיקון שגיאות הקלדה מופקד
    בפרומפט בידי המודל עצמו (שם עיר מתוקן), אבל הקואורדינטות הסופיות
    *תמיד* חייבות להגיע מקריאה נוספת ומאומתת ל-geocode_city עם השם
    המתוקן, ולא להיות מנוחשות ישירות. רק אם גם הניסיון המתוקן נכשל —
    מוחזרת הודעת שגיאה ברורה במקום ניחוש.

    הפורמט המבוקש לתשובה (הצלחה/כשלון) נאכף כאן בפרומפט, כדי לשמור
    על עקביות ההודעה שנשלחת בפועל לטלגרם.
    """
    return (
        f'המשתמש ביקש את מדד ה-UV הנוכחי עבור העיר: "{city}".\n\n'
        "בצע בדיוק לפי הצעדים הבאים:\n"
        f'1. קרא לכלי geocode_city עם city_name="{city}" כדי לאתר קואורדינטות '
        "מדויקות. אסור לנחש lat/lon בעצמך — תמיד להתבסס על תוצאת הכלי.\n"
        "2. אם found=False: ייתכן שיש שגיאת הקלדה בשם העיר. אם אתה מזהה "
        "בביטחון סביר שם עיר אמיתי ומוכר שדומה לקלט (למשל הבדל של אות "
        "אחת או שתיים), קרא שוב לכלי geocode_city עם city_name מתוקן — "
        "ניסיון תיקון אחד בלבד. אסור בשום מקרה להשתמש בקואורדינטות "
        "מהידע הכללי שלך; חובה שכל קואורדינטה תגיע מתוצאת הכלי עצמו.\n"
        "3. אם גם הניסיון המתוקן מחזיר found=False (או שלא ניתן לזהות "
        "תיקון סביר): החזר תשובה אחת בלבד, בעברית, בדיוק במבנה הזה "
        "(ללא תוספות):\n"
        f'   "לא הצלחתי לזהות עיר בשם \\"{city}\\". בדקו את האיות ונסו שוב."\n\n'
        "4. אם found=True (בניסיון הראשון או המתוקן): קרא לכלי get_current_uv "
        "עם ה-latitude/longitude "
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


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CITY

    answer = run_uv_report_for_city(city)

    print(f"ההודעה נשלחה לטלגרם עבור {city} (דרך MCP):")
    print(answer)