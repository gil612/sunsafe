# Supabase UV Reading Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every successful UV reading and every Telegram alert actually sent to Supabase, as the first slice of the project's planned data model.

**Architecture:** A new thin `supabase_client.py` module (same style as the existing `telegram_client.py`: sync `httpx` calls, no SDK, config from `.env`) provides one `insert_row(table, row)` function. A new MCP tool `log_uv_reading` in `mcp_weather_server.py` is called by the Agent itself right after `get_current_uv` succeeds. A new `log_alert_sent` function in `send_uv_report.py` is called from external code right after each real Telegram send succeeds (this one stays external for now — see spec — because the actual send is still external code today).

**Tech Stack:** Python 3.12, `httpx` (already a dependency), Supabase PostgREST REST API, no new pip packages.

**Spec:** `docs/superpowers/specs/2026-08-24-supabase-uv-logging-design.md`

## Global Constraints

- Talk to Supabase via direct `httpx` calls to its PostgREST REST API — no `supabase-py` SDK.
- Use the Supabase **secret / service_role** key (`SUPABASE_SERVICE_ROLE_KEY` in `.env`), never the publishable/anon key — writes must bypass RLS as a trusted server-side caller.
- A Supabase write failure must never break the UV report or Telegram send — always catch and degrade to a logged warning, never raise past the calling code.
- `uv_readings` is written by the Agent itself via the `log_uv_reading` MCP tool (not by external code) — this matches the direction already set for `send_telegram_report` in `CLAUDE.md` TODO #1.
- `alerts_sent` is written by external code in `send_uv_report.py`, right after a real Telegram send succeeds — `uv_reading_id` is always `NULL` for now (the id returned by `log_uv_reading` lives inside the Agent Loop and isn't surfaced back to `send_uv_report.py` yet).
- Keep `query_city` (raw user input) and `resolved_city` (what `geocode_city` returned) as separate columns.
- No automated test suite exists in this repo (no `pytest`, no `tests/` dir) — verification steps in this plan use the project's existing convention of manual script runs (`python -c "..."`, `python file.py "<arg>"`), matching how `telegram_client.py` and `mcp_weather_server.py` are already verified.

---

### Task 1: Create the Supabase tables

**Files:**
- Create: `supabase_schema.sql`

**Interfaces:**
- Produces: two tables, `uv_readings` and `alerts_sent`, that Task 2 onward will insert into via PostgREST.

- [ ] **Step 1: Write the schema file**

Create `supabase_schema.sql` at the repo root:

```sql
-- SunSafe — Supabase schema (first slice: uv_readings + alerts_sent)
-- Run this once in the Supabase project's SQL editor.
-- See docs/superpowers/specs/2026-08-24-supabase-uv-logging-design.md for rationale.

create table if not exists uv_readings (
    id             bigint generated always as identity primary key,
    created_at     timestamptz not null default now(),
    query_city     text not null,
    resolved_city  text not null,
    country        text,
    lat            double precision not null,
    lon            double precision not null,
    uv_index       double precision not null,
    temperature_2m double precision,
    cloud_cover    integer
);

create table if not exists alerts_sent (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),
    uv_reading_id bigint references uv_readings(id),
    chat_id       text not null,
    message_text  text not null,
    parse_mode    text,
    status        text not null
);
```

- [ ] **Step 2: Run it against the real Supabase project**

Open the Supabase project's dashboard → SQL Editor → paste the contents of `supabase_schema.sql` → Run.

- [ ] **Step 3: Verify the tables exist**

In the same SQL editor, run:

```sql
select table_name from information_schema.tables
where table_schema = 'public' and table_name in ('uv_readings', 'alerts_sent');
```

Expected: both `uv_readings` and `alerts_sent` are listed.

- [ ] **Step 4: Commit**

```bash
git add supabase_schema.sql
git commit -m "chore: add Supabase schema for uv_readings and alerts_sent"
```

---

### Task 2: Build the Supabase client module

**Files:**
- Create: `supabase_client.py`

**Interfaces:**
- Consumes: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` from `.env` (already added).
- Produces: `insert_row(table: str, row: dict, config: SupabaseConfig | None = None) -> dict` — returns the inserted row (including its generated `id`) on success; raises `SupabaseError` on any failure. `SupabaseConfig.from_env() -> SupabaseConfig` and `SupabaseError` (exception class) are also exported, for Tasks 3 and 4 to import.

- [ ] **Step 1: Write `supabase_client.py`**

```python
"""
SunSafe — Supabase Client
--------------------------
A thin wrapper around Supabase's PostgREST REST API for inserting rows,
built directly on httpx (no supabase-py SDK) — same approach as
telegram_client.py and mcp_weather_server.py's calls to Open-Meteo.

Install:
    pip install httpx python-dotenv

Run as a standalone check:
    python supabase_client.py
"""

import os
import logging
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sunsafe.supabase")
logging.basicConfig(level=logging.INFO)


class SupabaseError(Exception):
    """Raised when a call to the Supabase REST API fails."""


@dataclass
class SupabaseConfig:
    url: str
    service_role_key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL ו/או SUPABASE_SERVICE_ROLE_KEY לא מוגדרים. הוסיפו אותם ל-.env."
            )
        return cls(url=url, service_role_key=key)


def insert_row(table: str, row: dict, config: "SupabaseConfig | None" = None) -> dict:
    """
    Insert a single row into a Supabase table via PostgREST and return the
    inserted row (including its generated id).
    Raises SupabaseError on any failure — callers that must not let a
    logging failure break the main flow should catch this explicitly.
    """
    config = config or SupabaseConfig.from_env()
    url = f"{config.url}/rest/v1/{table}"
    headers = {
        "apikey": config.service_role_key,
        "Authorization": f"Bearer {config.service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        response = httpx.post(url, headers=headers, json=row, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Supabase insert into %s failed (%s): %s", table, e.response.status_code, e.response.text)
        raise SupabaseError(f"הוספת שורה ל-{table} נכשלה: {e.response.text}") from e
    except httpx.RequestError as e:
        logger.error("Supabase request to %s failed: %s", table, e)
        raise SupabaseError(f"בקשת רשת ל-Supabase נכשלה: {e}") from e

    inserted = response.json()
    return inserted[0] if isinstance(inserted, list) else inserted


if __name__ == "__main__":
    # בדיקה עצמאית מהירה — מריצים "python supabase_client.py"
    result = insert_row(
        "uv_readings",
        {
            "query_city": "test",
            "resolved_city": "Test City",
            "country": "Testland",
            "lat": 0.0,
            "lon": 0.0,
            "uv_index": 1.0,
            "temperature_2m": 20.0,
            "cloud_cover": 0,
        },
    )
    print(f"נכתבה שורה בהצלחה, id={result['id']}")
```

- [ ] **Step 2: Run the standalone check**

Run: `python supabase_client.py`
Expected: prints `נכתבה שורה בהצלחה, id=<some integer>` with no traceback.

- [ ] **Step 3: Verify the row landed in Supabase**

In the Supabase SQL editor:

```sql
select * from uv_readings where query_city = 'test';
```

Expected: one row, with `resolved_city = 'Test City'`. Delete it afterwards so it doesn't pollute real data:

```sql
delete from uv_readings where query_city = 'test';
```

- [ ] **Step 4: Commit**

```bash
git add supabase_client.py
git commit -m "feat: add Supabase REST client for inserting rows"
```

---

### Task 3: Add the `log_uv_reading` MCP tool and wire it into the Agent's prompt

**Files:**
- Modify: `mcp_weather_server.py:1-27` (imports and top-level constants — add the `supabase_client` import)
- Modify: `mcp_weather_server.py` (add the new tool, placed after `get_current_uv`, before `get_uv_forecast`)
- Modify: `send_uv_report.py:68-102` (`build_uv_task` — add step 5 to the prompt)

**Interfaces:**
- Consumes: `insert_row`, `SupabaseError` from `supabase_client` (Task 2).
- Produces: a new MCP tool `log_uv_reading(query_city, resolved_city, country, lat, lon, uv_index, temperature_2m, cloud_cover) -> dict`, returning `{"logged": True, "id": <int>}` or `{"logged": False, "error": <str>}`. Discovered automatically by `mcp_agent_loop.py` (tools are listed dynamically — no change needed there).

- [ ] **Step 1: Add the import and tool to `mcp_weather_server.py`**

Add this import alongside the existing ones (after `from mcp.server.fastmcp import FastMCP`):

```python
from supabase_client import insert_row, SupabaseError
```

Add this tool right after `get_current_uv` (before `get_uv_forecast`):

```python
@mcp.tool()
async def log_uv_reading(
    query_city: str,
    resolved_city: str,
    country: str | None,
    lat: float,
    lon: float,
    uv_index: float,
    temperature_2m: float | None = None,
    cloud_cover: int | None = None,
) -> dict:
    """
    שומר קריאת UV שבוצעה בפועל בטבלת uv_readings ב-Supabase, לצורך
    היסטוריה עתידית (Dashboard). יש לקרוא לכלי הזה תמיד אחרי
    get_current_uv כאשר יש תוצאה תקפה לדווח עליה.

    כשלון בשמירה (בעיית רשת/הרשאות מול Supabase) לא אמור לעצור את
    התשובה למשתמש — הכלי מחזיר {"logged": False, "error": ...}
    במקום לזרוק חריגה.
    """
    logger.info(
        "log_uv_reading(query_city=%s, resolved_city=%s, uv_index=%s)",
        query_city, resolved_city, uv_index,
    )
    row = {
        "query_city": query_city,
        "resolved_city": resolved_city,
        "country": country,
        "lat": lat,
        "lon": lon,
        "uv_index": uv_index,
        "temperature_2m": temperature_2m,
        "cloud_cover": cloud_cover,
    }
    try:
        inserted = insert_row("uv_readings", row)
        logger.info("log_uv_reading -> logged id=%s", inserted.get("id"))
        return {"logged": True, "id": inserted.get("id")}
    except (SupabaseError, RuntimeError) as e:
        logger.warning("log_uv_reading failed: %s", e)
        return {"logged": False, "error": str(e)}
```

- [ ] **Step 2: Verify the tool works standalone**

Run:

```bash
python -c "
import asyncio
from mcp_weather_server import log_uv_reading

async def main():
    print(await log_uv_reading('בדיקה', 'Test City', 'Testland', 0.0, 0.0, 1.0, 20.0, 0))

asyncio.run(main())
"
```

Expected: prints `{'logged': True, 'id': <int>}`. Then delete the test row via the Supabase SQL editor: `delete from uv_readings where query_city = 'בדיקה';`

- [ ] **Step 3: Add step 5 to the `build_uv_task` prompt in `send_uv_report.py`**

In `send_uv_report.py`, inside `build_uv_task`, find this exact existing block (the last two lines of the returned string):

```python
        "   אם לא נדרשת הגנה מיוחדת (רמה 1–2 בסולם):\n"
        '   "מדד ה-UV הנוכחי ב<שם העיר> הוא כ-**<המספר, עיגול לספרה '
        "עשרונית אחת>**. זהו מדד **<התיאור בעברית> (<הסיווג באנגלית>)**, "
        'ולכן אין צורך מיוחד בהגנה מהשמש כרגע."'
    )
```

Replace it with:

```python
        "   אם לא נדרשת הגנה מיוחדת (רמה 1–2 בסולם):\n"
        '   "מדד ה-UV הנוכחי ב<שם העיר> הוא כ-**<המספר, עיגול לספרה '
        "עשרונית אחת>**. זהו מדד **<התיאור בעברית> (<הסיווג באנגלית>)**, "
        'ולכן אין צורך מיוחד בהגנה מהשמש כרגע."\n\n'
        "5. אחרי שקיבלת תוצאה תקפה מ-get_current_uv (כלומר found=True "
        "בשלב הקודם): קרא גם לכלי log_uv_reading עם query_city, "
        "resolved_city, country, lat, lon, uv_index, temperature_2m, "
        "cloud_cover בהתאם לתוצאות שקיבלת מ-geocode_city ו-get_current_uv. "
        "זו פעולת רקע — אין להזכיר אותה או את תוצאתה בתשובה הסופית "
        "למשתמש, ואין לחכות איתה לאף החלטה נוספת."
    )
```

(Only the last line changed — the closing `'ולכן אין...'` line gains a `\n\n` and the new step 5 lines are added before the final `)`.)

- [ ] **Step 4: Verify end-to-end via a real agent run**

Run: `python send_uv_report.py "אילת"`

Expected in the output/logs: the agent calls `geocode_city`, then `get_current_uv`, then `log_uv_reading`, then sends the Telegram message — in that order. Confirm with:

```sql
select * from uv_readings order by created_at desc limit 1;
```

Expected: the most recent row is for אילת with a realistic `uv_index`.

- [ ] **Step 5: Commit**

```bash
git add mcp_weather_server.py send_uv_report.py
git commit -m "feat: add log_uv_reading MCP tool, called by the agent after get_current_uv"
```

---

### Task 4: Log every real Telegram send to `alerts_sent`

**Files:**
- Modify: `send_uv_report.py:18-22` (imports)
- Modify: `send_uv_report.py:104-131` (`send_agent_answer_to_telegram`)

**Interfaces:**
- Consumes: `insert_row`, `SupabaseError` from `supabase_client` (Task 2).
- Produces: `log_alert_sent(chat_id, message_text, parse_mode) -> None` (best-effort, never raises) inside `send_uv_report.py`, used by `send_agent_answer_to_telegram`.

- [ ] **Step 1: Add the import**

In `send_uv_report.py`, add alongside the existing imports:

```python
from supabase_client import insert_row, SupabaseError
```

- [ ] **Step 2: Add `log_alert_sent`**

Add this function right after `convert_gemini_markdown_to_telegram_v2` (before `build_uv_task`):

```python
def log_alert_sent(chat_id: str | int, message_text: str, parse_mode: str | None) -> None:
    """
    רושם ב-Supabase שהודעה נשלחה בפועל לטלגרם. uv_reading_id נשאר NULL
    בהיקף הנוכחי — ראו docs/superpowers/specs/2026-08-24-supabase-uv-logging-design.md.
    כשלון בכתיבה נרשם ללוג בלבד ולא מפיל את הריצה.
    """
    row = {
        "uv_reading_id": None,
        "chat_id": str(chat_id),
        "message_text": message_text,
        "parse_mode": parse_mode,
        "status": "sent",
    }
    try:
        insert_row("alerts_sent", row)
        logger.info("Logged alert to Supabase (chat_id=%s)", chat_id)
    except (SupabaseError, RuntimeError) as e:
        logger.warning("Failed to log alert to Supabase: %s", e)
```

- [ ] **Step 3: Call it after each real send in `send_agent_answer_to_telegram`**

Replace the body of `send_agent_answer_to_telegram` from `client = client or TelegramClient()` onward with:

```python
    client = client or TelegramClient()
    target_chat_id = chat_id or client.config.default_chat_id

    logger.info("Running MCP agent loop for task: %s", task)
    answer = run_agent_via_mcp(task)
    logger.info("Agent answer: %s", answer)

    formatted = convert_gemini_markdown_to_telegram_v2(answer)

    try:
        client.send_text_message(formatted, chat_id=chat_id, parse_mode="MarkdownV2")
        logger.info("Sent to Telegram with MarkdownV2 formatting")
        log_alert_sent(target_chat_id, formatted, "MarkdownV2")
    except TelegramError as e:
        logger.warning("MarkdownV2 send failed (%s) — retrying as plain text", e)
        client.send_text_message(answer, chat_id=chat_id, parse_mode=None)
        logger.info("Sent to Telegram as plain text (fallback)")
        log_alert_sent(target_chat_id, answer, None)

    return answer
```

- [ ] **Step 4: Verify end-to-end**

Run: `python send_uv_report.py "ירושלים"`

Then in the Supabase SQL editor:

```sql
select chat_id, parse_mode, status, uv_reading_id from alerts_sent order by created_at desc limit 1;
```

Expected: one new row, `status = 'sent'`, `parse_mode = 'MarkdownV2'` (unless the fallback path triggered, in which case `NULL`), `uv_reading_id` is `NULL`.

- [ ] **Step 5: Commit**

```bash
git add send_uv_report.py
git commit -m "feat: log every real Telegram send to alerts_sent in Supabase"
```

---

### Task 5: End-to-end verification of both success and not-found paths

**Files:** none (verification only)

**Interfaces:** none — this task only exercises Tasks 1-4 together.

- [ ] **Step 1: Verify the happy path writes both tables**

Run: `python send_uv_report.py "תל אביב"`

Then:

```sql
select id, resolved_city, uv_index from uv_readings order by created_at desc limit 1;
select uv_reading_id, status from alerts_sent order by created_at desc limit 1;
```

Expected: a fresh `uv_readings` row for תל אביב, and a fresh `alerts_sent` row with `status='sent'`.

- [ ] **Step 2: Verify the not-found path writes only `alerts_sent`**

Note the current row count first:

```sql
select count(*) from uv_readings;
```

Run: `python send_uv_report.py "בלאבלה"`

Then:

```sql
select count(*) from uv_readings;  -- must be unchanged from the count above
select message_text, status from alerts_sent order by created_at desc limit 1;
```

Expected: `uv_readings` count is unchanged (no row was written — the agent never reached `get_current_uv`/`log_uv_reading`), and the latest `alerts_sent` row contains the "לא הצלחתי לזהות עיר" message with `status='sent'`.

- [ ] **Step 3: Update `CLAUDE.md`**

In the `## סטטוס נוכחי` section, add a line noting the Supabase logging is live, and in `## TODO`, note that TODO #6 now has its first slice done (`uv_readings` + `alerts_sent`), with `users`/`locations`/`exposure_log` still pending on TODO #2 (exposure score) landing first.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mark first Supabase logging slice as done in CLAUDE.md"
```
