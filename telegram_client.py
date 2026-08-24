"""
SunSafe — Telegram Client
--------------------------
A thin wrapper around the Telegram Bot API for sending text messages and UV
alerts. Used both for local testing and as an importable module inside a
FastAPI server.

Install:
    pip install httpx python-dotenv

Run as a standalone check:
    python telegram_client.py
"""

import os
import re
import logging
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sunsafe.telegram")
logging.basicConfig(level=logging.INFO)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# תווים ש-MarkdownV2 של טלגרם דורש לברוח מהם (Escape)
# https://core.telegram.org/bots/api#markdownv2-style
_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


class TelegramError(Exception):
    """Raised when a call to the Telegram API fails."""


@dataclass
class TelegramConfig:
    bot_token: str = field(repr=False)
    default_chat_id: str | None = None

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "BOT_TOKEN לא מוגדר. הוסיפו אותו לקובץ .env או כמשתנה סביבה."
            )
        return cls(bot_token=token, default_chat_id=os.getenv("CHAT_ID"))


def escape_markdown_v2(text: str) -> str:
    """
    Escape special characters so free-form text (e.g. from an AI) does not
    break Telegram's MarkdownV2 format. Must be applied to any dynamic text
    (for example, a Gemini answer) before sending with parse_mode="MarkdownV2".
    """
    pattern = f"([{re.escape(_MDV2_SPECIAL_CHARS)}])"
    return re.sub(pattern, r"\\\1", text)


class TelegramClient:
    def __init__(self, config: TelegramConfig | None = None, timeout: float = 10.0):
        self.config = config or TelegramConfig.from_env()
        self._base_url = TELEGRAM_API_BASE.format(token=self.config.bot_token)
        self._timeout = timeout

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{self._base_url}/{method}"
        try:
            response = httpx.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error("Telegram API error %s: %s", e.response.status_code, e.response.text)
            raise TelegramError(f"Telegram API החזיר שגיאה: {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error("Telegram request failed: %s", e)
            raise TelegramError(f"בקשת רשת ל-Telegram נכשלה: {e}") from e

        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API החזיר ok=false: {data}")
        return data["result"]

    def send_text_message(
        self,
        text: str,
        chat_id: str | int | None = None,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> dict:
        """Send a plain text message (for example, a UV alert)."""
        target_chat_id = chat_id or self.config.default_chat_id
        if not target_chat_id:
            raise ValueError("לא סופק chat_id ואין CHAT_ID ברירת מחדל ב-.env")

        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        result = self._post("sendMessage", payload)
        logger.info("Message sent to chat_id=%s (message_id=%s)", target_chat_id, result.get("message_id"))
        return result

    def send_reply_button_message(
        self,
        text: str,
        buttons: list[str],
        chat_id: str | int | None = None,
    ) -> dict:
        """
        Send a message with quick-reply buttons (Reply Keyboard).
        Useful, for example, for a skin-type questionnaire: ["Type I", "Type II", ...]
        """
        target_chat_id = chat_id or self.config.default_chat_id
        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "reply_markup": {
                "keyboard": [[{"text": b}] for b in buttons],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        }
        return self._post("sendMessage", payload)

    def get_me(self) -> dict:
        """Verify that the token is valid and return the bot's details."""
        url = f"{self._base_url}/getMe"
        response = httpx.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response.json()["result"]


def send_uv_alert(
    client: TelegramClient,
    chat_id: str | int,
    uv_index: float,
    safe_minutes: float,
    recommendation: str,
    cost_usd: float | None = None,
) -> dict:
    """
    Build and send a formatted UV alert, following the format defined in the SPEC.
    `recommendation` usually comes from Gemini, so it is escaped before being
    embedded in Markdown.
    """
    safe_reco = escape_markdown_v2(recommendation)
    lines = [
        "☀️ *התראת UV*",
        "",
        f"UV Index כרגע: *{uv_index:g}*",
        f"זמן חשיפה בטוח משוער: *~{safe_minutes:.0f} דקות*",
        "",
        f"💡 {safe_reco}",
    ]
    if cost_usd is not None:
        lines.append("")
        lines.append(f"_עלות קריאה: ${cost_usd:.4f}_")

    text = "\n".join(lines)
    return client.send_text_message(text, chat_id=chat_id, parse_mode="MarkdownV2")


if __name__ == "__main__":
    # בדיקה עצמאית מהירה — מריצים "python telegram_client.py"
    client = TelegramClient()

    me = client.get_me()
    print(f"מחובר לבוט: @{me['username']} ({me['first_name']})")

    result = client.send_text_message(
        "✅ *SunSafe מחובר בהצלחה*\n\nה-`telegram_client.py` עובד כמו שצריך.",
        parse_mode="Markdown",
    )
    print(f"הודעה נשלחה בהצלחה, message_id={result['message_id']}")
