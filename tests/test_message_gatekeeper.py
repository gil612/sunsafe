"""
בדיקה ידנית (לא pytest) ל-message_gatekeeper + לחיווט שלו בתוך
handle_update ב-bot_commands.py.

מכסה שני דברים בנפרד:
1. classify_message() עצמה — עם Gemini מדומה (לא נוגעים ברשת אמיתית).
   בכוונה *לא* בודקים "/start_session" וכדומה כאן: הפונקציה הזו לא
   אמורה לראות פקודות מוכרות בכלל בזרימה האמיתית (ראו handle_update) —
   רק טקסט שכבר נכשל בהתאמה מדויקת. גרסה קודמת של הקובץ הזה בדקה פקודות
   ישירות מול classify_message ועקפה בכך בדיוק את הבאג שהיה בקוד עצמו
   (קיצור-דרך פנימי ש"/" = VALID תמיד) — לא רוצים לחזור על זה.
2. handle_update() — מוודאים שפקודה מוכרת בדיוק מדלגת על הקריאה ל-LLM
   לגמרי (classify_message לא נקרא), ושטקסט שמסווג כ-NOISE נעצר לפני
   handler/DB, בעוד VALID ממשיך הלאה.
"""
import os
os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

# tests/ נמצא רמה אחת מתחת לשורש הריפו — מוסיפים את שורש הריפו ל-sys.path
# כדי ש-import bot_commands (ומודולים אחיים אחרים) ימשיך לעבוד גם כשמריצים
# מ-tests/ ולא משורש הריפו.
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import bot_commands as bc
from message_gatekeeper import classify_message

FAILURES = []


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        FAILURES.append(name)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _gemini_payload(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# ---------------------------------------------------------------------
# 1. classify_message() עצמה, עם Gemini מדומה
# ---------------------------------------------------------------------

with patch.dict(os.environ, {"GEMINI_API_KEY": "TEST_KEY"}):
    with patch("message_gatekeeper.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = FakeResponse(
            200, _gemini_payload("NOISE")
        )
        result = classify_message("hi there")
        check("classify_message: chit-chat -> NOISE (מוקאי)", result == "NOISE", result)

    with patch("message_gatekeeper.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = FakeResponse(
            200, _gemini_payload("VALID")
        )
        result = classify_message("/strt_sesion תל אביב")  # typo של פקודה אמיתית
        check("classify_message: malformed-command typo -> VALID (מוקאי)", result == "VALID", result)

    # קלט קצר מאוד -> fast-path, בלי לקרוא ל-Gemini בכלל
    with patch("message_gatekeeper.httpx.Client") as MockClient:
        result = classify_message("👍")
        check("classify_message: very short text -> NOISE fast-path", result == "NOISE", result)
        check("classify_message: fast-path never calls Gemini", not MockClient.called)

    # קריאה נכשלת -> fail open ל-VALID
    with patch("message_gatekeeper.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = FakeResponse(500, {})
        result = classify_message("some longer ambiguous text here")
        check("classify_message: API error -> fail open to VALID", result == "VALID", result)

# בלי מפתח API בכלל -> fail open ל-VALID, בלי לנסות לקרוא לרשת
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("GEMINI_API_KEY", None)
    result = classify_message("some longer ambiguous text here")
    check("classify_message: no API key -> fail open to VALID", result == "VALID", result)


# ---------------------------------------------------------------------
# 2. החיווט בתוך handle_update: פקודה מוכרת מדלגת על ה-LLM; NOISE נעצר
# ---------------------------------------------------------------------

def _fake_update(text=None, photo=None, location=None, username="gil612"):
    message = {"chat": {"id": 111}, "from": {"username": username}}
    if text is not None:
        message["text"] = text
    if photo is not None:
        message["photo"] = photo
    if location is not None:
        message["location"] = location
    return {"message": message}


with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message") as mock_send, \
     patch("bot_commands.classify_message") as mock_classify, \
     patch.object(bc, "handle_dashboard") as mock_dashboard:
    bc.handle_update(_fake_update(text="/dashboard"))
    check(
        "handle_update: exact known command skips classify_message entirely",
        not mock_classify.called,
    )
    check("handle_update: known command still dispatches to its handler", mock_dashboard.called)

with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message") as mock_send, \
     patch("bot_commands.classify_message", return_value="NOISE") as mock_classify:
    bc.handle_update(_fake_update(text="hey what's up"))
    check("handle_update: non-command text is classified (not auto-skipped)", mock_classify.called)
    check("handle_update: NOISE classification -> no message sent, nothing dispatched", not mock_send.called)

with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message") as mock_send, \
     patch("bot_commands.classify_message", return_value="NOISE") as mock_classify:
    # פקודה לא מוכרת (מתחילה ב-"/" אבל לא קיימת) -> חייבת לעבור דרך
    # ה-LLM ולא לקבל מעבר חינם רק כי יש "/" בהתחלה (זה היה הבאג).
    bc.handle_update(_fake_update(text="/totally_unknown_command"))
    check("handle_update: unknown '/'-prefixed text is still classified, not auto-VALID", mock_classify.called)

with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message") as mock_send, \
     patch("bot_commands.classify_message", return_value="VALID") as mock_classify, \
     patch("bot_commands.run_agent_via_mcp", return_value="ה-UV בתל אביב עכשיו הוא 6.") as mock_agent, \
     patch.object(bc, "COMMAND_HANDLERS", bc.COMMAND_HANDLERS):
    # טקסט לא-פקודה שסווג VALID עדיין לא "מזייף" פקודה אמיתית -- אבל
    # עכשיו (בשונה מגרסה קודמת של הבדיקה הזו) גם לא נעלם בשקט: מנותב
    # ל-Agent Loop דרך MCP (_handle_freeform_question), והתשובה שלו
    # נשלחת למשתמש כמו שהיא.
    bc.handle_update(_fake_update(text="תל אביב אולי?"))
    check("handle_update: VALID non-command routed to the Agent Loop", mock_agent.called)
    check(
        "handle_update: VALID non-command -> Agent Loop answer sent as-is",
        mock_send.call_args is not None and mock_send.call_args[0][1] == "ה-UV בתל אביב עכשיו הוא 6.",
    )

with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message") as mock_send, \
     patch("bot_commands.classify_message", return_value="VALID"), \
     patch("bot_commands.run_agent_via_mcp", side_effect=RuntimeError("MCP subprocess died")), \
     patch.object(bc, "COMMAND_HANDLERS", bc.COMMAND_HANDLERS):
    # אם ה-Agent Loop עצמו נכשל (רשת/subprocess/וכו') -- לא קריסה, רק
    # הודעת שגיאה עדינה למשתמש (fail open, לא fail crash).
    bc.handle_update(_fake_update(text="תל אביב אולי?"))
    check("handle_update: Agent Loop failure -> no crash, graceful message sent", mock_send.called)



# ---------------------------------------------------------------------
# 3. בחירת סוג-עור "אינטראקטיבית": ספרה בודדת עם pending flag מנותבת
#    ישירות ל-handle_set_skin_type, *עוקפת* את הגייטקיפר לגמרי (לא רק
#    מסווגת VALID) -- ובלי flag, מתנהגת בדיוק כמו כל טקסט קצר אחר.
# ---------------------------------------------------------------------
bc._pending_skin_type_pick.clear()

with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message"), \
     patch("bot_commands.send_photo"), \
     patch("bot_commands.upsert_row") as mock_upsert, \
     patch("bot_commands.classify_message") as mock_classify:
    bc.handle_start(111, "gil612", "")
    check("handle_start: sets pending skin-type flag", "gil612" in bc._pending_skin_type_pick)

    bc.handle_update(_fake_update(text="3"))
    check("pending digit: bypasses classify_message entirely", not mock_classify.called)
    check("pending digit: actually sets skin type via handle_set_skin_type", mock_upsert.called)
    check(
        "pending digit: consumes the flag (one-shot, not reusable)",
        "gil612" not in bc._pending_skin_type_pick,
    )

bc._pending_skin_type_pick.clear()
with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message"), \
     patch("bot_commands.upsert_row") as mock_upsert, \
     patch("bot_commands.classify_message", return_value="NOISE") as mock_classify:
    # אותה ספרה בדיוק, אבל בלי pending flag פעיל -- לא אמורה "להיתפס",
    # חוזרת להתנהגות הרגילה (עוברת דרך הגייטקיפר כמו כל טקסט קצר).
    bc.handle_update(_fake_update(text="3"))
    check("digit without pending flag: still goes through the gatekeeper", mock_classify.called)
    check("digit without pending flag: skin type NOT silently set", not mock_upsert.called)

bc._pending_skin_type_pick.clear()
with patch("bot_commands.update_rows"), \
     patch("bot_commands.send_message"), \
     patch("bot_commands.upsert_row") as mock_upsert:
    # ה-usage error של /set_skin_type עצמו גם מסמן pending -- לא רק /start.
    bc.handle_set_skin_type(111, "gil612", "")
    check(
        "handle_set_skin_type usage-error also sets pending flag",
        "gil612" in bc._pending_skin_type_pick,
    )
    bc.handle_update(_fake_update(text="5"))
    check("digit after usage-error is picked up too", mock_upsert.called)


print(f"\n{len(FAILURES)} failing check(s)." if FAILURES else "\nAll checks passed.")
if FAILURES:
    raise SystemExit(1)
