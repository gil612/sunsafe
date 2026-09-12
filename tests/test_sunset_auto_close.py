"""
בדיקה ידנית (לא pytest) לתכונת הסגירה האוטומטית של sessions פתוחים
אחרי שקיעה (fetch_sunset_utc, _maybe_auto_close_session,
_auto_close_expired_sessions_once, auto_close_expired_sessions_forever
ב-bot_commands.py).

מדמים select_rows/handle_end_session/fetch_sunset_utc — לא נוגעים
ברשת/DB אמיתיים. תרחישי המפתח:
1. session שהשקיעה שלו כבר עברה -> handle_end_session נקרא עם
   chat_id/username הנכונים.
2. session שהשקיעה שלו עוד לא הגיעה -> לא נוגעים בו.
3. session בלי lat/lon שמור (שורה ישנה) -> מדלגים, לא קורסים.
4. fetch_sunset_utc נכשל (מחזיר None) -> מדלגים בסבב הזה, לא סוגרים בטעות.
5. session שנסגר בדיוק באמצע (race עם /end_session ידני של המשתמש) ->
   הבדיקה החוזרת (still_open) מונעת קריאה מיותרת ל-handle_end_session.
6. משתמש בלי chat_id שמור -> מדלגים (best-effort), לא קורסים.
7. _auto_close_expired_sessions_once עם רשימת sessions ריקה/כשל בשליפה
   -> לא קורס.
"""
import os
os.environ.setdefault("BOT_TOKEN", "TEST_TOKEN")

# tests/ נמצא רמה אחת מתחת לשורש הריפו — מוסיפים את שורש הריפו ל-sys.path
# כדי ש-import bot_commands (ומודולים אחיים אחרים) ימשיך לעבוד גם כשמריצים
# מ-tests/ ולא משורש הריפו.
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bot_commands as bc

FAILURES = []


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        FAILURES.append(name)


NOW = datetime(2026, 9, 12, 17, 0, tzinfo=timezone.utc)  # 20:00 שעון ישראל (UTC+3)

BASE_SESSION = {
    "id": 501,
    "telegram_username": "gil612",
    "city": "תל אביב",
    "lat": 32.08,
    "lon": 34.78,
    "start_time": "2026-09-12T10:00:00+00:00",
    "end_time": None,
}


def make_session(**overrides):
    s = dict(BASE_SESSION)
    s.update(overrides)
    return s


# ---------------------------------------------------------------------
# 1) שקיעה כבר עברה -> handle_end_session נקרא
# ---------------------------------------------------------------------
session = make_session()
sunset_already_passed = NOW - timedelta(hours=1)  # 16:00 UTC, לפני NOW=17:00 UTC

with patch.object(bc, "fetch_sunset_utc", return_value=sunset_already_passed) as mock_sunset, \
     patch.object(bc, "select_rows") as mock_select, \
     patch.object(bc, "handle_end_session") as mock_end:
    # קריאה ראשונה (still_open check) -> עדיין פתוח; קריאה שנייה (users) -> chat_id
    mock_select.side_effect = [
        [{"id": 501, "end_time": None}],          # still_open
        [{"chat_id": 999, "telegram_username": "gil612"}],  # users
    ]
    bc._maybe_auto_close_session(object(), session, NOW)
    check(
        "sunset already passed -> handle_end_session called with correct args",
        mock_end.call_args == ((999, "gil612", ""),),
        f"-> {mock_end.call_args}",
    )

# ---------------------------------------------------------------------
# 2) שקיעה עוד לא הגיעה -> לא נוגעים
# ---------------------------------------------------------------------
sunset_in_future = NOW + timedelta(hours=1)
with patch.object(bc, "fetch_sunset_utc", return_value=sunset_in_future), \
     patch.object(bc, "select_rows") as mock_select, \
     patch.object(bc, "handle_end_session") as mock_end:
    bc._maybe_auto_close_session(object(), session, NOW)
    check("sunset not yet reached -> handle_end_session NOT called", not mock_end.called)
    check("sunset not yet reached -> no DB lookups at all (short-circuits early)", not mock_select.called)

# ---------------------------------------------------------------------
# 3) session בלי lat/lon -> מדלגים בלי לקרוס
# ---------------------------------------------------------------------
session_no_geo = make_session(lat=None, lon=None)
with patch.object(bc, "fetch_sunset_utc") as mock_sunset, \
     patch.object(bc, "handle_end_session") as mock_end:
    bc._maybe_auto_close_session(object(), session_no_geo, NOW)
    check("no lat/lon -> fetch_sunset_utc not even called", not mock_sunset.called)
    check("no lat/lon -> handle_end_session not called", not mock_end.called)

# ---------------------------------------------------------------------
# 4) fetch_sunset_utc נכשל (None) -> מדלגים, לא סוגרים בטעות
# ---------------------------------------------------------------------
with patch.object(bc, "fetch_sunset_utc", return_value=None), \
     patch.object(bc, "handle_end_session") as mock_end:
    bc._maybe_auto_close_session(object(), session, NOW)
    check("fetch_sunset_utc returned None -> handle_end_session NOT called (best-effort)", not mock_end.called)

# ---------------------------------------------------------------------
# 5) race עם /end_session ידני -> still_open check מונע קריאה שגויה
# ---------------------------------------------------------------------
with patch.object(bc, "fetch_sunset_utc", return_value=sunset_already_passed), \
     patch.object(bc, "select_rows", return_value=[]) as mock_select, \
     patch.object(bc, "handle_end_session") as mock_end:
    bc._maybe_auto_close_session(object(), session, NOW)
    check(
        "session already closed by user (still_open empty) -> handle_end_session NOT called",
        not mock_end.called,
    )
    check("still_open check queried exposure_log by id", mock_select.call_args[0][0] == "exposure_log")

# ---------------------------------------------------------------------
# 6) משתמש בלי chat_id שמור -> מדלגים
# ---------------------------------------------------------------------
with patch.object(bc, "fetch_sunset_utc", return_value=sunset_already_passed), \
     patch.object(bc, "select_rows") as mock_select, \
     patch.object(bc, "handle_end_session") as mock_end:
    mock_select.side_effect = [
        [{"id": 501, "end_time": None}],  # still_open
        [{"chat_id": None, "telegram_username": "gil612"}],  # users, no chat_id
    ]
    bc._maybe_auto_close_session(object(), session, NOW)
    check("user has no chat_id -> handle_end_session NOT called", not mock_end.called)

# ---------------------------------------------------------------------
# 7) _auto_close_expired_sessions_once: רשימה ריקה / כשל בשליפה -> לא קורס
# ---------------------------------------------------------------------
with patch.object(bc, "select_rows", return_value=[]), \
     patch.object(bc, "_maybe_auto_close_session") as mock_maybe:
    bc._auto_close_expired_sessions_once()
    check("empty open_sessions -> _maybe_auto_close_session never called", not mock_maybe.called)

with patch.object(bc, "select_rows", side_effect=RuntimeError("DB down")), \
     patch.object(bc, "_maybe_auto_close_session") as mock_maybe:
    try:
        bc._auto_close_expired_sessions_once()
        check("select_rows raising -> does not propagate (caught + logged)", True)
    except Exception as e:
        check("select_rows raising -> does not propagate (caught + logged)", False, f"raised {e!r}")

# ---------------------------------------------------------------------
# 8) _auto_close_expired_sessions_once: session בודד שנכשל לא עוצר את השאר
# ---------------------------------------------------------------------
sessions = [make_session(id=1), make_session(id=2), make_session(id=3)]
processed = []


def fake_maybe(client, session, now):
    if session["id"] == 2:
        raise RuntimeError("boom on session 2")
    processed.append(session["id"])


with patch.object(bc, "select_rows", return_value=sessions), \
     patch.object(bc, "_maybe_auto_close_session", side_effect=fake_maybe):
    bc._auto_close_expired_sessions_once()
    check(
        "one session raising doesn't stop processing the others",
        processed == [1, 3],
        f"-> processed={processed}",
    )

# ---------------------------------------------------------------------
# 9) fetch_sunset_utc עצמה: פירוש נכון של daily.sunset + utc_offset_seconds
# ---------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *args, **kwargs):
        return FakeResponse(self._payload)


# תל אביב, UTC+3 (10800 שניות), שקיעה מקומית 18:47 -> UTC 15:47
fake_client = FakeClient({"daily": {"sunset": ["2026-09-12T18:47"]}, "utc_offset_seconds": 10800})
result = bc.fetch_sunset_utc(fake_client, 32.08, 34.78)
check(
    "fetch_sunset_utc: local 18:47 UTC+3 -> UTC 15:47",
    result == datetime(2026, 9, 12, 15, 47, tzinfo=timezone.utc),
    f"-> {result}",
)

fake_client_fail = FakeClient({"daily": {}})  # missing "sunset" key
result_fail = bc.fetch_sunset_utc(fake_client_fail, 32.08, 34.78)
check("fetch_sunset_utc: malformed response -> None (best-effort)", result_fail is None)


print(f"\n{len(FAILURES)} failing check(s)." if FAILURES else "\nAll checks passed.")
if FAILURES:
    raise SystemExit(1)
