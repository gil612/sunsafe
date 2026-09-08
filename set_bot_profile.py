"""
SunSafe — עדכון פרופיל הבוט בטלגרם (Name / About / Description / Commands)
--------------------------------------------------------------------------
כרגע הבוט מוצג עם Name="Gil_token" (שם dev פנימי, לא ידידותי למשתמש)
ובלי Commands רשומים בכלל ("no commands yet" ב-/mybots) — כלומר מי
שמקליד "/" בצ'אט לא מקבל תפריט הצעות פקודות. הסקריפט הזה מתקן את שני
אלה + מוסיף About/Description קריאים, דרך ה-Bot API (לא צריך BotFather
ידני בכלל לחלקים האלה):
    setMyName, setMyShortDescription, setMyDescription, setMyCommands

**מה שהסקריפט הזה לא יכול לעשות** (אין להם endpoint ב-Bot API בכלל,
רק דרך BotFather ידנית): תמונת פרופיל (Botpic), תמונת Description,
Privacy Policy. ראו את ההודעה שהסקריפט מדפיס בסוף להוראות ידניות.

הרצה: python set_bot_profile.py   (חד-פעמי; אפשר להריץ שוב בבטחה בכל
עדכון עתידי לתוכן/לרשימת הפקודות — כל הקריאות הן "set", לא "add").
"""

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sunsafe.set_bot_profile")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# httpx רושם ללוג את ה-URL המלא של כל בקשה ברמת INFO כברירת מחדל, וה-URL
# למטה כולל את ה-BOT_TOKEN עצמו — בדיוק מה שקרה בהרצה הקודמת (הטוקן
# המלא הופיע במסוף, בטקסט גלוי). בלי השורה הזו זה יקרה בכל הרצה.
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- תוכן, בעברית (כמו שאר הבוט) -------------------------------------
NAME = "SunSafe – עוזר הגנה מהשמש ☀️"

SHORT_DESCRIPTION = (
    "🌞 עוקב אחרי חשיפה שלכם ל-UV לאורך היום ומזכיר מתי להתגונן. "
    "שלחו /start_session כדי להתחיל."
)

DESCRIPTION = (
    "☀️ SunSafe עוזר לכם לעקוב אחרי חשיפה יומית לקרינת UV ולהימנע "
    "משיזוף-יתר וכוויות שמש.\n\n"
    "מה הבוט עושה:\n"
    "• /start_session <עיר> — מתחיל מעקב, כולל תחזית UV ל-24 השעות הקרובות\n"
    "• /end_session — מסיים ומחשב מדד חשיפה אישי (לפי סוג העור וקרם הגנה)\n"
    "• /my_sessions — היסטוריית ה-sessions שלכם\n"
    "• /dashboard — אזור אישי עם גרפים\n\n"
    "לפני הכל: /set_skin_type <1-6> כדי שהחישוב יתאים לכם אישית."
)

# סדר = סדר ההופעה בתפריט "/" בטלגרם. שם פקודה חייב: אותיות קטנות/
# ספרות/קו תחתון בלבד, 1-32 תווים (מגבלת Bot API) — תואם ל-COMMAND_HANDLERS
# הקיים ב-bot_commands.py.
COMMANDS = [
    ("start_session", "התחלת session חדש למעקב חשיפה לשמש"),
    ("end_session", "סיום ה-session הפתוח וחישוב מדד חשיפה"),
    ("add_session", "הוספת session ישן שכבר הסתיים"),
    ("my_sessions", "רשימת ה-sessions האחרונים שלכם"),
    ("edit_session", "עריכת session קיים (שעת סיום / SPF)"),
    ("delete_session", "מחיקת session"),
    ("offline_session", "תיעוד session בלי אינטרנט"),
    ("set_skin_type", "הגדרת סוג עור (1-6, סולם Fitzpatrick)"),
    ("diagnose_skin", "הערכת נזק-שמש מתמונה (לא ייעוץ רפואי)"),
    ("dashboard", "קישור לאזור האישי שלכם"),
]


def _assert_within_limits() -> None:
    """בדיקת מגבלות Bot API לפני שליחה — עדיף כשל ברור פה מ-400 סתום מטלגרם."""
    assert len(NAME) <= 64, f"NAME too long: {len(NAME)}/64"
    assert len(SHORT_DESCRIPTION) <= 120, f"SHORT_DESCRIPTION too long: {len(SHORT_DESCRIPTION)}/120"
    assert len(DESCRIPTION) <= 512, f"DESCRIPTION too long: {len(DESCRIPTION)}/512"
    assert len(COMMANDS) <= 100, f"too many commands: {len(COMMANDS)}/100"
    for cmd, desc in COMMANDS:
        assert 1 <= len(cmd) <= 32, f"command name length invalid: {cmd!r}"
        assert cmd.replace("_", "").isalnum() and cmd == cmd.lower(), f"invalid command name: {cmd!r}"
        assert 1 <= len(desc) <= 256, f"command description length invalid for {cmd!r}: {len(desc)}"


def _call(client: httpx.Client, method: str, payload: dict) -> None:
    response = client.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=10.0)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"{method} failed: {result}")
    logger.info("%s -> ok", method)


def main() -> None:
    _assert_within_limits()
    with httpx.Client() as client:
        _call(client, "setMyName", {"name": NAME})
        _call(client, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION})
        _call(client, "setMyDescription", {"description": DESCRIPTION})
        _call(client, "setMyCommands", {
            "commands": [{"command": cmd, "description": desc} for cmd, desc in COMMANDS]
        })

    print(
        "\nעודכן: Name / About / Description / Commands.\n"
        "מה שנשאר לעשות ידנית ב-BotFather (/mybots -> Edit @gil612Bot info) —\n"
        "אין להם API בכלל:\n"
        "  • Botpic — Edit Botpic, להעלות תמונת פרופיל לבוט\n"
        "  • Description picture — Edit Description Picture (אופציונלי)\n"
        "  • Privacy Policy — Edit Privacy Policy (קישור למדיניות פרטיות, אם רוצים)\n"
    )


if __name__ == "__main__":
    main()
