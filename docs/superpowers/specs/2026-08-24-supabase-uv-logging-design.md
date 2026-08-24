# Supabase UV Reading Logging — Design

## מטרה

להתחיל לממש את TODO #6 ב-`CLAUDE.md` (מודל נתונים ב-Supabase), בפרוסה
מצומצמת: לוג של קריאות UV שהתבצעו בפועל, כבסיס לדשבורד עתידי (TODO #7)
ולתשתית ל-`exposure_log`/`users` שיתווספו בהמשך, כשיהיה קוד production
שמפיק סוג עור/exposure_score (TODO #2 בקוד `mcp_weather_server.py`).

לא בהיקף הנוכחי: `users`, `locations`, `exposure_log` — אין עדיין
בקוד שום דבר שמייצר את הנתונים האלה (skin type, SPF, exposure score),
ובניית טבלאות ריקות מראש היא ניחוש ספקולטיבי (YAGNI).

## היקף

שתי טבלאות:

1. **`uv_readings`** — כל קריאת UV מוצלחת שבוצעה בפועל (geocode + get_current_uv).
   נכתבת ע"י **כלי MCP חדש שה-Agent עצמו קורא לו** (`log_uv_reading`),
   כי בזמן שה-Agent מחזיק את תוצאת `get_current_uv` יש לו כל מה שצריך
   כדי לתעד — בהתאם לכיוון שכבר מתועד ב-TODO #1 (Agent מבצע פעולות
   מסיימות בעצמו, לא קוד חיצוני אחרי שהלולאה הסתיימה).

2. **`alerts_sent`** — כל הודעה שנשלחה בפועל לטלגרם.
   נכתבת **בקוד חיצוני** (`send_uv_report.py`), **אחרי** הקריאה
   האמיתית ל-Telegram API — כי זו עדיין הנקודה היחידה שבה השליחה
   בפועל קורית (הכלי `send_telegram_report` מ-TODO #1 טרם קיים).
   כשהכלי הזה ייבנה, לוגיקת ה-logging תעבור יחד איתו ל-Agent, באותה
   תזוזה אחת (לא בונים כאן מעבר-ביניים שיזרק).

## סכימה (Postgres / Supabase)

```sql
create table uv_readings (
    id           bigint generated always as identity primary key,
    created_at   timestamptz not null default now(),
    query_city   text not null,      -- מה שהמשתמש הקליד במקור
    resolved_city text not null,     -- מה ש-geocode_city החזיר (name)
    country      text,
    lat          double precision not null,
    lon          double precision not null,
    uv_index     double precision not null,
    temperature_2m double precision,
    cloud_cover  integer
);

create table alerts_sent (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),
    uv_reading_id bigint references uv_readings(id),  -- null אם city לא נמצא
    chat_id       text not null,
    message_text  text not null,
    parse_mode    text,             -- 'MarkdownV2' | null (טקסט רגיל)
    status        text not null     -- 'sent' | 'failed'
);
```

`query_city` מול `resolved_city` נשמרים בנפרד בכוונה — זה מאפשר בעתיד
למדוד כמה פעמים מנגנון תיקון שגיאות ההקלדה (ב-`build_uv_task`) בפועל
נכנס לפעולה.

`uv_reading_id` הוא nullable כי כשהעיר לא נמצאת (`found=False`,
כולל אחרי ניסיון תיקון) אין בכלל קריאת UV לתעד — רק את ניסיון השליחה
של הודעת השגיאה לטלגרם.

## גישה ל-Supabase

קריאות REST ישירות מול Supabase PostgREST דרך `httpx` (לא SDK
`supabase-py`) — עקבי עם איך ש-`mcp_weather_server.py` כבר פונה
ל-Open-Meteo, ובלי להוסיף תלות כבדה לצורך שתי טבלאות.

משתני סביבה חדשים (יתווספו ל-`.env` ול-`.env.example`):

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
```

משתמשים במפתח ה-service role (לא anon) כי הכתיבה מתבצעת משרת
צד-שרת מהימן (ה-MCP Server / `send_uv_report.py`), לא מדפדפן.

## כלי MCP חדש: `log_uv_reading`

מתווסף ל-`mcp_weather_server.py`, לצד שאר הכלים:

```python
@mcp.tool()
async def log_uv_reading(
    query_city: str,
    resolved_city: str,
    country: str | None,
    lat: float,
    lon: float,
    uv_index: float,
    temperature_2m: float | None,
    cloud_cover: int | None,
) -> dict:
    """
    שומר קריאת UV שבוצעה בפועל בטבלת uv_readings ב-Supabase, לצורך
    היסטוריה עתידית (Dashboard). יש לקרוא לכלי הזה תמיד אחרי
    get_current_uv כאשר יש תוצאה תקפה לדווח עליה.

    כשלון בשמירה (בעיית רשת/הרשאות מול Supabase) לא אמור לעצור את
    התשובה למשתמש — הכלי מחזיר {"logged": False, "error": ...}
    במקום לזרוק חריגה.
    """
```

מחזיר `{"logged": True, "id": <int>}` בהצלחה, או `{"logged": False,
"error": "<תיאור קצר>"}` בכשלון. ה-Agent לא נדרש להזכיר את תוצאת
הלוגינג בתשובה למשתמש — זו פעולת רקע.

עדכון פרומפט `build_uv_task` ב-`send_uv_report.py`: הוספת צעד 5 —
"אחרי שקיבלת תוצאה תקפה מ-`get_current_uv`, קרא גם לכלי
`log_uv_reading` עם הנתונים הרלוונטיים, לפני החזרת התשובה הסופית."

## `alerts_sent` — כתיבה מ-`send_uv_report.py`

פונקציה חדשה, `log_alert_sent(uv_reading_id, chat_id, message_text,
parse_mode, status)`, נקראת מ-`send_agent_answer_to_telegram` **פעם
אחת בלבד**, אחרי שההודעה נשלחה בפועל בהצלחה (בין אם ב-MarkdownV2
ובין אם ב-fallback לטקסט רגיל) — עם `status="sent"` ו-`parse_mode`
המשקף את השיטה שבאמת הצליחה (`"MarkdownV2"` או `None`).

אם גם ניסיון ה-fallback נכשל (חריגת `TelegramError` שנייה): הקוד
הקיים היום פשוט מעביר הלאה את החריגה (`send_agent_answer_to_telegram`
לא תופס אותה) — התנהגות זו **לא** משתנה כאן (זה בדיוק TODO #2 הנפרד:
"שגיאה נשלחת לטלגרם אם הסוכן נכשל", טרם מומש). בהיקף הנוכחי, כשל
כפול כזה פשוט לא נכתב ל-`alerts_sent` בכלל — אין ניסיון "לתפוס
ולתעד כשלון" כאן, כי זה ידרוש להוסיף טיפול בחריגות שעדיין לא קיים
ושייך ל-TODO אחר. אם TODO #2 ייושם קודם, `status="failed"` יתווסף
כחלק מאותה עבודה, לא כאן.

בכל מקרה: כשלון **בכתיבה ל-Supabase עצמה** (למשל תקלת רשת) נתפס
ונרשם ללוג בלבד — לא מפיל את הריצה.

`uv_reading_id` לא זמין ישירות ל-`send_uv_report.py` היום (התוצאה
של `log_uv_reading` נשארת בתוך ה-Agent Loop הפנימי, לא חוזרת דרך
`run()`). בהיקף הנוכחי: `uv_reading_id=None` תמיד ב-`alerts_sent`
(את הקישור בין הטבלאות אפשר להשלים בהמשך, כשה-Agent עצמו יהיה זה
ששולח את ההודעה ויוכל לקשר בין שתי הפעולות באותה קריאה).

## טיפול בשגיאות

- כל קריאת HTTP ל-Supabase עטופה ב-try/except, עם `httpx.HTTPStatusError`
  ו-`httpx.RequestError` נתפסים בנפרד, ולוג ברור לכל אחד.
- אין נפילה בשרשרת: כשל ב-logging (בכל אחת מהטבלאות) לא גורם לכשל
  בשליחת הדוח/ההתראה למשתמש.

## בדיקות

1. יצירת שתי הטבלאות בפועל ב-Supabase (SQL editor, לפי הסכימה למעלה).
2. קריאה ידנית ל-`log_uv_reading` ואימות שהשורה נכתבה ב-Supabase.
3. הרצת `send_uv_report.py "<עיר תקפה>"` מקצה לקצה — לוודא ש-`uv_readings`
   ו-`alerts_sent` שתיהן מקבלות שורה חדשה.
4. הרצת `send_uv_report.py` עם עיר לא קיימת — לוודא ש-`alerts_sent`
   מקבלת שורה עם `status='sent'`/`'failed'` בהתאם, ו-`uv_reading_id=NULL`,
   וש-`uv_readings` **לא** מקבלת שורה.
