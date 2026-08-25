# Magic Link Edge Function — Design

## מטרה

שלב 4 בתלות של `2026-08-25-exposure-log-schema-design.md`: פונקציית Edge
ב-Supabase שקוראת את ה-Magic Link Token מה-URL (`?token=...`), מוודאת
תוקף, ומחזירה JSON עם פרופיל המשתמש וה-Sessions שלו — כדי שדף ההדגמה
(`sunsafe_demo.html`, שלב 5, **לא בהיקף המסמך הזה**) יוכל להציג נתונים
אמיתיים במקום ה-`USERS` המדומה שבקוד.

זו הדרך היחידה שהדפדפן (עם `anon key` בלבד) יכול לגעת ב-`users`/
`exposure_log`/`magic_links` — שלוש הטבלאות בכוונה בלי policies
(`supabase_schema.sql`), כך שרק קוד עם `service_role key` יכול לקרוא
מהן. ה-Edge Function היא קוד שרת (רץ אצל Supabase, לא בדפדפן) — בדיוק
כמו `bot_commands.py`.

## למה Edge Function ולא REST ישיר מהדפדפן

הדפדפן לא יכול לקבל `service_role key` (ייחשף לכל מי שיפתח DevTools).
Edge Function פותרת את זה: הטוקן ב-`service_role key` נשאר בצד השרת
(ב-Supabase, לא בקוד שנשלח לדפדפן), והדפדפן שולח רק את ה-Magic Link
Token שכבר קיבל בטלגרם — זהה בעיקרון לזרימת `create_magic_link` ב-
`bot_commands.py`, רק בכיוון ההפוך (קריאה במקום כתיבה).

## חוזה ה-API

`GET /functions/v1/dashboard-data?token=<magic-link-token>`

### הצלחה — `200 OK`

```json
{
  "telegram_username": "gil612",
  "onboarded": true,
  "skin_type": 3,
  "sessions": [
    {
      "id": 12,
      "city": "Tel Aviv",
      "country": "Israel",
      "start_time": "2026-08-25T06:00:00+00:00",
      "end_time": "2026-08-25T06:29:00+00:00",
      "uv_index": 7.24,
      "spf": null,
      "exposure_score": 105
    }
  ]
}
```

- רק sessions **סגורים** (`end_time IS NOT NULL`) — ל-session פתוח אין
  עדיין `exposure_score`/`end_time` משמעותיים להצגה בהיסטוריה; `/dashboard`
  לא אמור לשקף session שעדיין לא הסתיים. ממוינים `start_time desc`.
- `onboarded: false` + `skin_type: null` + `sessions: []` — כאשר הטוקן
  תקין אבל אין עדיין שורה ב-`users` (המשתמש מעולם לא הריץ
  `/set_skin_type`). זה **לא** שגיאה — `handle_dashboard` ב-
  `bot_commands.py` לא בודק שהמשתמש קיים לפני יצירת הקישור, אז המצב הזה
  אפשרי וצפוי. ה-UI (שלב 5) אמור להציג הודעה מתאימה ("עדיין לא הגדרת
  סוג עור, שלח/י `/set_skin_type` לבוט") במקום דשבורד ריק.

### שגיאות

| מצב | סטטוס | body |
|---|---|---|
| בלי `?token=` בכלל | 400 | `{"error": "missing_token"}` |
| טוקן לא קיים ב-`magic_links` | 404 | `{"error": "invalid_token"}` |
| טוקן קיים אבל `expires_at` עבר | 410 | `{"error": "expired_token"}` |
| כשל לא צפוי (רשת/Supabase) | 500 | `{"error": "server_error"}` — פרטים רק ב-log, לא בתשובה |

הודעות בעברית למשתמש (כמו `"משתמש לא נמצא"` בדף ההדגמה) הן באחריות
ה-Frontend לפי קוד ה-`error` — לא מוטמעות בתשובת ה-API, כדי לא לערבב
פורמט-הודעה עם קוד-שגיאה (עקבי עם `SupabaseError` ב-Python, ששם *כן*
נותנים טקסט עברי ישירות, כי היעד שם הוא לוג/הודעת טלגרם, לא JSON contract).

## החלטות מכוונות (ופתוחות לשלב 5)

**בלי `display_name`:** בטבלת `users` יש רק `telegram_username` +
`skin_type` — אין שם מלא מאוחסן בשום מקום (בניגוד ל-`USERS` המדומה
בדף ההדגמה, שיש בו `"Gil Baram"` וכו'). ה-API מחזיר `telegram_username`
בלבד; שלב 5 יצטרך להחליט אם להציג `@username` במקום שם מלא, או להוסיף
עמודת `display_name` ל-`users` בעתיד. לא פותר את זה כאן — מחוץ להיקף.

**שדה `used` ב-`magic_links` לא נגע בו:** הטבלה כבר כוללת עמודת
`used boolean`, אבל שום קוד קיים לא קורא/כותב אותה, ובכוונה — ה-TTL
(`LINK_TTL_MINUTES = 60*24`, ראו התיעוד ב-`bot_commands.py`) נבחר כדי
לאפשר שימוש חוזר בקישור לאורך 24 שעות (למשל רענון הדף כמה פעמים), לא
Single-Use. הפונקציה הזו בודקת רק `expires_at`, לא `used` — כדי לא
לחסום שימוש חוזר לגיטימי. אם בעתיד ירצו לעקוב "האם נפתח אף פעם" זה
Follow-up נפרד, לא כאן.

**ולידציה בלי Supabase Auth (JWT):** ברירת המחדל של Supabase Edge
Functions דורשת JWT תקף (`Authorization: Bearer <anon-or-user-key>`)
ודוחה בקשות בלי זה. הדף הסטטי (`sunsafe_demo.html`) לא מחובר ל-Supabase
Auth בכלל — ה-Token *הוא* מנגנון האימות (בדיוק כמו Magic Link בכל
מוצר אחר). לכן הפריסה **חייבת** את הדגל `--no-verify-jwt` (ראו "פריסה"
למטה), אחרת כל קריאה מהדפדפן תיכשל ב-401 עוד לפני שהקוד שלנו רץ.

**REST ישיר, בלי `@supabase/supabase-js`:** עקבי עם ההחלטה המתועדת
ב-`CLAUDE.md`/`supabase_client.py` (REST ישיר מול PostgREST, בלי SDK
נוסף) — כאן זה אומר `fetch()` רגיל מול `${SUPABASE_URL}/rest/v1/...`
עם ה-headers של `service_role`, בלי import מ-`esm.sh`.

## מימוש

`supabase/functions/dashboard-data/index.ts` — פונקציה אחת, `Deno.serve`,
שלושה שלבים רצופים:

1. **Parse + validate token param** → 400 אם חסר.
2. **`GET /rest/v1/magic_links?token=eq.<token>&select=telegram_username,expires_at`**
   עם headers של `service_role` → 404 אם ריק, 410 אם `expires_at` עבר.
3. אם תקין: שתי קריאות REST במקביל —
   `GET /rest/v1/users?telegram_username=eq.<u>&select=skin_type`
   ו-`GET /rest/v1/exposure_log?telegram_username=eq.<u>&end_time=not.is.null&select=...&order=start_time.desc`
   — ומרכיבים את ה-JSON הסופי לפי החוזה למעלה.

`SUPABASE_URL` ו-`SUPABASE_SERVICE_ROLE_KEY` **לא** צריך להגדיר ידנית
כ-secrets — Supabase מזריק אותם אוטומטית לכל Edge Function (משתני
סביבה שמורים). ראו גם `references/env-vars` בתיעוד הרשמי של Supabase
Edge Functions אם רוצים לוודא.

### CORS

הדף הסטטי יכול לרוץ מכל origin (localhost בזמן פיתוח, GitHub Pages/
Cloudflare Pages בפרודקשן — טרם הוחלט, ראו TODO #5 ב-`CLAUDE.md`).
לכן `Access-Control-Allow-Origin: *` (הרשאת הקריאה כבר נאכפת ע"י
הטוקן עצמו, לא ע"י origin) + טיפול מפורש ב-`OPTIONS` (preflight).

## פריסה (ידני, לא מהסביבה הזו — כאן אין Supabase CLI מחובר)

```bash
supabase functions deploy dashboard-data --no-verify-jwt
```

בדיקה ידנית אחרי הפריסה:

```bash
curl "https://<project-ref>.supabase.co/functions/v1/dashboard-data?token=<טוקן אמיתי מ/dashboard>"
```

## בדיקות

1. טוקן תקין, למשתמש עם `skin_type` ו-sessions סגורים → 200, הנתונים
   הנכונים, ממוינים מהחדש לישן.
2. טוקן תקין, למשתמש בלי שורה ב-`users` → 200, `onboarded: false`,
   `skin_type: null`, `sessions: []`.
3. טוקן שלא קיים בכלל → 404 `invalid_token`.
4. טוקן שפג תוקפו (`expires_at` בעבר) → 410 `expired_token`.
5. בלי `?token=` → 400 `missing_token`.
6. Session פתוח (`end_time IS NULL`) לא מופיע ב-`sessions` שמוחזר.
7. בקשת `OPTIONS` (preflight) מחזירה 204 עם headers של CORS, בלי לגעת
   ב-DB.

## לא בהיקף הזה

- עדכון `sunsafe_demo.html` לקרוא בפועל ל-Endpoint הזה (שלב 5 בתלות).
- Rate limiting / הגנה מפני ניחוש טוקנים בכוח גס (הטוקן הוא
  `secrets.token_urlsafe(32)` — 256 ביט אנטרופיה, מספיק חסין נגד ניחוש
  בפועל; Rate limiting הוא שיפור הגנה-בעומק עתידי, לא כאן).
- שינוי סכימה — אין צורך, שלוש הטבלאות כבר קיימות.
