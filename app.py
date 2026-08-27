"""
SunSafe — Hugging Face Space entry point
------------------------------------------
עוטף את bot_commands.py (ה-listener האמיתי, polling מול Telegram) בתוך
אפליקציית Gradio מינימלית, כדי שהקוד ירוץ 24/7 על Hugging Face Spaces
(CPU Basic, חינמי) במקום להיות תלוי שהמחשב של המשתמש דלוק. ראו
docs/2026-08-27-hf-spaces-hosting-design.md לרציונל המלא.

ה-Gradio UI עצמו לא עושה שום דבר פונקציונלי — הוא קיים רק כי Spaces
חינמיים "נרדמים" בלי תעבורת HTTP נכנסת, אז צריך *משהו* שאפשר לפנג' אליו
(ראו .github/workflows/keep-alive.yml) כדי לשמור על ה-Space ער. ה-
listener האמיתי של הבוט רץ ב-thread נפרד ברקע, בלתי תלוי לגמרי ב-Gradio
— גם אם אף אחד לעולם לא פותח את העמוד הזה בדפדפן.

חשוב: כל הסודות (BOT_TOKEN, GEMINI_API_KEY, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY) חייבים להיות מוגדרים כ-Space secrets
(Settings → Variables and secrets) *לפני* ההפעלה הראשונה — bot_commands
קורא os.environ["BOT_TOKEN"] (לא .get) ברמת המודול, אז חוסר סוד יגרום
לקריסה מיידית (KeyError) כבר בעליית האפליקציה.
"""

import logging
import threading

import gradio as gr

from bot_commands import poll_forever

logger = logging.getLogger("sunsafe.hf_space")

_bot_thread_started = False
_start_lock = threading.Lock()


def _start_bot_once() -> None:
    """
    מפעיל את poll_forever() ב-thread נפרד, פעם אחת בלבד לכל תהליך.
    ה-lock+flag מונעים שני listeners מקבילים בטעות (אותה בעיית 409
    Conflict מ-Telegram שכבר נתקלנו בה כששני תהליכים רצו על אותו
    BOT_TOKEN בו-זמנית — ראו docs/2026-08-27-hf-spaces-hosting-design.md).
    """
    global _bot_thread_started
    with _start_lock:
        if _bot_thread_started:
            return
        _bot_thread_started = True
        thread = threading.Thread(target=poll_forever, name="sunsafe-bot-poll", daemon=True)
        thread.start()
        logger.info("SunSafe bot polling thread started")


_start_bot_once()

with gr.Blocks(title="SunSafe Bot Status") as demo:
    gr.Markdown(
        "## ☀️ SunSafe — הבוט פעיל\n\n"
        "זהו עמוד סטטוס בלבד. הבוט האמיתי (`bot_commands.py`) רץ ברקע "
        "כ-thread נפרד ומאזין ל-Telegram — הוא לא תלוי בעמוד הזה בשום "
        "צורה. העמוד קיים רק כדי לתת ל-Space כתובת URL שאפשר \"לפנג'\" "
        "(keep-alive, ראו .github/workflows/keep-alive.yml) כדי שהוא "
        "לא יירדם על החומרה החינמית."
    )

if __name__ == "__main__":
    demo.launch()
