"""
SunSafe — חיבור Agent Loop ל-Telegram
---------------------------------------
לוקח את התשובה החופשית מה-Agent Loop (agent_loop.py) ושולח אותה בפועל
כהודעת Telegram, עם המרה בטוחה של פורמט ה-Markdown שג'מיני מייצר
(**bold**) לפורמט MarkdownV2 שטלגרם דורש — כולל Fallback לטקסט רגיל
אם ההמרה נכשלת, כדי שההדגמה בכיתה לא תיפול על שגיאת פרסינג.

הרצה:
    python send_uv_report.py
"""

import logging
import re

from telegram_client import TelegramClient, TelegramError, escape_markdown_v2
from agent_loop import agent_loop

logger = logging.getLogger("sunsafe.integration")
logging.basicConfig(level=logging.INFO)


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


def send_agent_answer_to_telegram(
    task: str,
    client: TelegramClient | None = None,
    chat_id: str | int | None = None,
) -> str:
    """
    מריץ את ה-Agent Loop על task נתון, ושולח את התשובה לטלגרם.
    מנסה קודם MarkdownV2 מפורמט; אם טלגרם מחזיר שגיאת פרסינג —
    שולח שוב כטקסט רגיל, כדי שההדגמה תמיד תצליח.
    מחזיר את הטקסט הגולמי שהוחזר מה-Agent (שימושי גם ללוגים/בדיקות).
    """
    client = client or TelegramClient()

    logger.info("Running agent loop for task: %s", task)
    answer = agent_loop(task)
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


if __name__ == "__main__":
    send_agent_answer_to_telegram(
        "מה ה-UV Index הנוכחי בתל אביב? (קואורדינטות: lat=32.08, lon=34.78). "
        "תן תשובה קצרה בעברית, כולל אם צריך הגנה מהשמש עכשיו."
    )
    print("ההודעה נשלחה לטלגרם.")
