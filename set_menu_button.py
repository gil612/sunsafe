"""
SunSafe — הגדרת Menu Button קבוע לבוט (חד-פעמי)
--------------------------------------------------
קורא ל-setChatMenuButton של Telegram Bot API כדי שכפתור פתיחת ה-Mini App
האופליין (docs/session/index.html) יופיע תמיד בפינת הצ'אט של הבוט —
ליד תיבת ההקלדה — גם בלי לגלול היסטוריה ובלי לזכור לשלוח /offline_session
מראש. זו הגדרה ברמת הבוט (לא per-message), אז מריצים את זה פעם אחת
ולא כחלק מ-bot_commands.py הרץ-תמיד.

ראה docs/2026-08-29-offline-session-miniapp-design.md סעיף 10.

הרצה (חד-פעמי, אחרי שה-Mini App כבר פרוס ב-GitHub Pages):
    python set_menu_button.py
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_MINIAPP_URL = os.environ.get("SESSION_MINIAPP_URL", "http://localhost:8080/session")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def main() -> None:
    if SESSION_MINIAPP_URL.startswith("http://localhost"):
        print(
            "אזהרה: SESSION_MINIAPP_URL עדיין מצביע ל-localhost. הגדירו אותו "
            "לכתובת ה-GitHub Pages האמיתית (משתנה סביבה) לפני שמריצים את זה, "
            "אחרת הכפתור בטלגרם יפתח קישור שלא עובד למשתמשים אחרים."
        )
        return

    response = httpx.post(
        f"{TELEGRAM_API}/setChatMenuButton",
        json={
            "menu_button": {
                "type": "web_app",
                "text": "SunSafe אופליין",
                "web_app": {"url": SESSION_MINIAPP_URL},
            }
        },
        timeout=10.0,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("ok"):
        print(f"הוגדר בהצלחה. Menu Button יפתח: {SESSION_MINIAPP_URL}")
    else:
        print(f"נכשל: {result}")


if __name__ == "__main__":
    main()
