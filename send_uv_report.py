"""
SunSafe — wiring the Agent Loop (via MCP) to Telegram
--------------------------------------------------------
Takes the free-form answer from the Agent Loop — now through mcp_agent_loop.py
(which connects to mcp_weather_server.py as an MCP Client, instead of the older
agent_loop.py with its hand-written tool) — and actually sends it as a Telegram
message, safely converting the Markdown that Gemini produces (**bold**) into the
MarkdownV2 format Telegram requires, with a fallback to plain text if the
conversion fails.

Run:
    python send_uv_report.py
"""

import logging

from telegram_client import TelegramClient, TelegramError, escape_markdown_v2
from mcp_agent_loop import run as run_agent_via_mcp

logger = logging.getLogger("sunsafe.integration")
logging.basicConfig(level=logging.INFO)


def convert_gemini_markdown_to_telegram_v2(text: str) -> str:
    """
    Gemini returns text with **bold** (standard Markdown).
    Telegram's MarkdownV2 requires *bold* (a single asterisk) plus escaping of
    every other special character. This function splits on **, turns each
    odd-indexed segment into bold, and escapes everything else.
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
    Run the Agent Loop (via MCP) on a given task and send the answer to Telegram.
    Tries formatted MarkdownV2 first; if Telegram returns a parsing error, resends
    as plain text so the demo always succeeds.
    Returns the raw text returned by the agent (also useful for logs/tests).
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


if __name__ == "__main__":
    send_agent_answer_to_telegram(
        "מה ה-UV Index הנוכחי בתל אביב? (קואורדינטות: lat=32.08, lon=34.78). "
        "תן תשובה קצרה בעברית, כולל אם צריך הגנה מהשמש עכשיו."
    )
    print("ההודעה נשלחה לטלגרם (דרך MCP).")
