# SunSafe — תכנון: Session אופליין דרך Telegram Mini App

תאריך: 2026-08-29
סטטוס: טיוטה לתכנון — טרם מומש

## 1. הבעיה

`/start_session` ו-`/end_session` הקיימים הם פקודות בוט רגילות: כל אחת מהן היא
הודעת טקסט שחייבת לעבור דרך שרתי Telegram (`getUpdates`), ואז הקוד שלנו קורא
ל-Open-Meteo (UV) ול-Supabase (כתיבת/עדכון `exposure_log`). כל השרשרת הזו
דורשת רשת. בטיול/הליכה עם קליטה סלולרית חלקית או אפסית (מצב נפוץ מאוד
לתרחיש שהמוצר הזה בכלל נועד אליו — חשיפה לשמש בטבע) המשתמש פשוט לא יכול
לתעד session בזמן אמת, ומגלה בדיעבד שלא נשמר כלום.

הפתרון שנבחר (לפי בחירת המשתמש): **Telegram Mini App** — עמוד HTML/JS שנטען
בתוך ה-WebView של Telegram, ושיכול לתפוס גם timestamp וגם מיקום GPS אמיתי
לגמרי אופליין (`navigator.geolocation` פונה ישירות לשבב ה-GPS של המכשיר,
בלי תלות ברשת), שומר הכל מקומית, ומסתנכרן לשרת רק כשהחיבור חוזר.

## 2. למה לא משהו אחר

- **אפליקציה native**: כבד מדי לפרויקט קורס, ודורש חנות אפליקציות.
- **רק כפתור "שתפו מיקום" הקיים** (`prompt_location_share`,
  `docs/2026-08-26-location-sharing-design.md`): עדיין שולח הודעת Telegram
  בפועל — לא עובד בלי רשת בכלל, לא רק "לוקח זמן".
  Mini App אחר לגמרי: לא שולח שום דבר לרשת בזמן הלחיצה, רק שומר ב-`localStorage`
  המקומי של הדפדפן/WebView.
- **דף web רגיל (לא Mini App)**: היה עובד טכנית לתפיסה אופליין, אבל מאבד את
  זיהוי המשתמש האוטומטי — היינו צריכים שוב מנגנון מסוג magic-link, ומאבדים
  את הידיעה "מי זה" בלי לבקש התחברות נפרדת בכל פעם. Mini App נותן את זה
  בחינם דרך `initData` (סעיף 5).

## 3. ארכיטקטורה

```
┌─────────────────────────────┐        פעם אחת, כשיש קליטה
│  Telegram (טלפון)           │──────► נטען docs/session/index.html
│  Menu Button / /offline_    │        (GitHub Pages, כמו docs/dashboard)
│  session פותח Mini App      │
└──────────────┬───────────────┘
               │  אופליין: הכל בצד לקוח, localStorage בלבד
               │  🟢 Start (timestamp + GPS) → נשמר כ"session פתוח מקומי"
               │  🔴 End   (timestamp + GPS) → מצטרף לתור pendingSessions
               │
               │  כשהחיבור חוזר (אוטומטי ברקע + כפתור "סנכרן עכשיו")
               ▼
┌───────────────────────────────────────┐
│  Edge Function חדש: submit-offline-   │
│  session (Deno, כמו dashboard-data)   │
│  - מאמת initData (HMAC, BOT_TOKEN)    │
│  - שולף UV היסטורי מ-Open-Meteo       │
│    לפי start_time+lat/lon (סעיף 6)    │
│  - reverse-geocode (Nominatim) לשם עיר│
│  - מחשב exposure_score בשרת (לא סומך  │
│    על הלקוח)                          │
│  - כותב ל-exposure_log עם client_uuid │
│    ל-idempotency (סעיף 8)             │
└───────────────────────────────────────┘
```

## 4. זרימת נתונים — צעד-צעד

1. **הכנה (חובה שיש רשת)**: המשתמש פותח את ה-Mini App פעם אחת לפני שיוצא
   לטיול. זה טוען את כל הדף (קובץ HTML בודד, הכל inline — בלי בקשות רשת
   נוספות אחרי הטעינה הראשונית, אותה קונבנציה כמו `dashboard/index.html`),
   מה שנותן לדפדפן/WebView הזדמנות לשמור אותו ב-cache. מומלץ להוסיף
   Service Worker מינימלי (cache-first לקובץ היחיד) כדי שהזמינות האופליין
   תהיה מובטחת ולא תלויה בהיוריסטיקת cache ברירת-מחדל של הדפדפן.
2. **🟢 Start (אופליין)**: לחיצה → `navigator.geolocation.getCurrentPosition()`
   (עם `enableHighAccuracy: true`; בלי סיוע רשת ל-GPS זה יכול לקחת עד
   כ-30 שניות לתפיסת fix ראשונה — יש להראות spinner + timeout סביר).
   נשמר ב-`localStorage`: `{lat, lon, accuracy, timestamp}` כ"session פתוח
   מקומי" יחיד (תואם למגבלת session-פתוח-אחד הקיימת בשרת ב-`_can_start_session`).
3. **🔴 End (אופליין)**: אותו תהליך GPS, מצרף ל-start ששמור, ודוחף אובייקט
   session שלם (start+end) לתור `pendingSessions[]`. מנקה את סלוט
   ה-"session פתוח מקומי".
4. **מצב מוצג למשתמש**: תמיד ברור על המסך כמה sessions ממתינים לסנכרון —
   קריטי לאמון (המשתמש צריך לדעת שזה *נשמר*, לא רק "נעלם").
5. **סנכרון**: בכל טעינת דף + כפתור "סנכרן עכשיו", ניסיון `fetch` ל-Edge
   Function החדש עם `{initData, sessions: [...]}`. בהצלחה — התשובה מפרטת
   אילו `client_uuid` התקבלו, ורק אלה נמחקים מהתור המקומי (כדי לא לאבד
   נתונים אם רק חלק הצליחו).

## 5. אימות: Telegram `initData`

`window.Telegram.WebApp.initData` מוזרק על-ידי אפליקציית Telegram עצמה
**מקומית, בזמן פתיחת ה-Mini App** — לא דורש קריאת רשת, ולכן זמין וניתן
לשמירה גם ל-session שנתפס לגמרי אופליין. אימות בצד השרת (סטנדרט של
Telegram, ידוע ויציב):

1. `secret_key = HMAC_SHA256(key="WebAppData", data=BOT_TOKEN)`
2. data-check-string = כל השדות מ-`initData` חוץ מ-`hash`, ממוינים
   לפי מפתח, מחוברים כ-`key=value` עם `\n` ביניהם.
3. `computed_hash = HEX(HMAC_SHA256(key=secret_key, data=data_check_string))`
4. תקין אם `computed_hash == hash` שהתקבל.

**החלטה מכוונת**: Telegram ממליצים גם לבדוק ש-`auth_date` "טרי" (בד"כ עד
24 שעות בהמלצות נפוצות) — כאן זה *בכוונה* לא מיושם, כי `initData` נתפס
בזמן פתיחת האפליקציה (שיכול להיות ימים לפני הסנכרון בפועל אם אין קליטה
לאורך זמן). הבדיקה הקריפטוגרפית (`hash`) עדיין חובה ותמיד תקפה — רק בדיקת
הטריות מוותרים עליה, תיעוד מפורש של הפשרה.

## 6. שחזור UV היסטורי — הממצא המרכזי

`get_current_uv()` הקיים קורא ל-Forecast API של Open-Meteo ומחזיר רק את ה-UV
**הנוכחי**. ל-session אופליין אנחנו יודעים את ה-`start_time` בדיעבד (אחרי
שהחיבור חוזר) — צריך UV *לאותו רגע בעבר*, לא לרגע הסנכרון.

בדקתי שתי אפשרויות מול Open-Meteo בפועל:

| API | UV Index זמין? |
|---|---|
| Historical Weather API (`archive-api.open-meteo.com`, מבוסס ERA5) | **לא** — לא מופיע ברשימת המשתנים השעתיים הנתמכים (אימתתי מול התיעוד הרשמי) |
| Forecast API הרגיל (`api.open-meteo.com/v1/forecast`) עם `past_days` (0–92) | **כן** — `hourly=uv_index&past_days=N` מחזיר UV שעתי גם לימים אחורה |

המסקנה: **לא צריך את ה-archive API בכלל.** ל-`past_days` יש טווח של עד 92
יום אחורה — מכסה בנוחות כל תרחיש ריאלי של "המשתמש היה בלי קליטה וחזר
לחיבור" (שעות עד ימים בודדים, לא חודשים). זה גם אותו מקור נתונים בדיוק
שכבר משמש היום ל-UV חי (`get_current_uv`), כך שאין אי-עקביות בין sessions
מקוונים לאופליין.

מימוש בפועל ב-Edge Function: קריאה ל-
`.../v1/forecast?latitude=..&longitude=..&hourly=uv_index&past_days=<מספיק
לכסות את start_time>`, איתור השעה הכי קרובה ל-`start_time` במערך
`hourly.time`, ולקיחת הערך המקביל מ-`hourly.uv_index`. בדיוק כמו היום
בזרימה המקוונת, נלקח UV **בנקודת ההתחלה בלבד** ומשמש לכל משך ה-session
(לא ממוצע/משתנה לאורך הזמן) — עקביות מלאה עם `calculate_exposure_score`
הקיים.

**מגבלה ידועה**: אם המשתמש נשאר בלי קליטה יותר מ-92 יום (לא ריאלי לתרחיש
המוצר), השחזור ההיסטורי ייכשל. יטופל כשגיאה ברורה בסנכרון ("לא הצלחנו
לשחזר נתוני UV להתחלה כל כך ישנה"), לא כקריסה שקטה.

## 7. Edge Function חדש: `submit-offline-session`

בקשה (`POST`):
```json
{
  "initData": "<raw initData string מה-Mini App>",
  "sessions": [
    {
      "client_uuid": "<crypto.randomUUID(), נוצר אופליין>",
      "start_time": "2026-08-29T06:10:00Z",
      "start_lat": 32.79, "start_lon": 34.99,
      "end_time": "2026-08-29T07:05:00Z",
      "end_lat": 32.80, "end_lon": 35.00,
      "spf": 30
    }
  ]
}
```

תשובה (`200`):
```json
{ "accepted": ["<client_uuid1>", ...], "rejected": [{"client_uuid": "...", "reason": "..."}] }
```

קודי שגיאה עקביים עם `dashboard-data` (`invalid_init_data` 401,
`not_onboarded` 404 אם אין `skin_type`, `server_error` 500). CORS זהה
(`Access-Control-Allow-Origin: *` + טיפול ב-`OPTIONS`).

**חשוב לפריסה**: בניגוד ל-`dashboard-data`, הפונקציה הזו צריכה את
`BOT_TOKEN` כדי לאמת `initData` — וזה **לא** מוזרק אוטומטית (בניגוד ל-
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`). צריך להגדיר אותו ידנית:
`supabase secrets set BOT_TOKEN=...` לפני הפריסה.

## 8. שינוי סכימה: idempotency

עמודה חדשה ב-`exposure_log`: `client_uuid text unique null` (אינדקס
ייחודי). ה-Edge Function כותב עם `Prefer: resolution=ignore-duplicates`
(אותה טכניקת upsert כמו `upsert_row` הקיים) — אם `client_uuid` כבר קיים
(למשל retry אחרי שהתשובה הראשונה אבדה ברשת), הכתיבה השנייה לא יוצרת כפילות
והתשובה עדיין מדווחת "accepted" ללקוח.

## 9. מגבלות ידועות והחלטות מכוונות (סיכום)

- חובה לפתוח את ה-Mini App פעם אחת **לפני** אובדן הקליטה (כדי שהדף ייכנס
  ל-cache) — יתועד גם כהודעת טיפ מהבוט בעצמו כשהוא שולח את הקישור.
- GPS בלי רשת יכול לקחת עד ~30 שניות ל-fix ראשון — UX צריך spinner+timeout,
  לא רק spinner אינסופי.
- אין אכיפת "session פתוח אחד" מול השרת בזמן אמת כשאין רשת (אי אפשר —
  אין רשת) — רק מקומית, ומתואם מחדש בסנכרון. אם המשתמש גם פתח session
  אונליין באותו חלון זמן, ייתכן חפיפה — לא נמנעת אוטומטית, לא קריטי
  להיקף פרויקט קורס.
- בדיקת טריות `auth_date` של `initData` מוותרים עליה בכוונה (סעיף 5).
- UV נלקח בנקודת ההתחלה בלבד לכל ה-session, תואם להתנהגות הקיימת
  ב-`_begin_session`/`calculate_exposure_score`.

## 10. שלבי פריסה (לביצוע)

1. `docs/session/index.html` — עמוד ה-Mini App (GitHub Pages), inline JS/CSS
   בלבד, טוען `https://telegram.org/js/telegram-web-app.js`.
2. `supabase/functions/submit-offline-session/` — Edge Function + לוגיקה
   טהורה נפרדת (`logic.ts`) לפי אותה קונבנציה כמו `dashboard-data`.
3. `supabase secrets set BOT_TOKEN=...` ואז
   `supabase functions deploy submit-offline-session --no-verify-jwt`.
4. הוספת עמודת `client_uuid` ל-`exposure_log` (מיגרציה קטנה ב-
   `supabase_schema.sql`).
5. בוט: פקודת `/offline_session` ששולחת inline keyboard עם כפתור
   `web_app` לעמוד; מומלץ גם להגדיר אותו כ-Menu Button קבוע של הבוט
   (`setChatMenuButton`, פעם אחת דרך ה-API) כדי שיהיה זמין תמיד גם בלי
   לגלול היסטוריה.
6. בדיקת קצה-לקצה: מצב טיסה בפועל על טלפון אמיתי (לא רק "בלי לקרוא ל-API"
   בקוד) — כדי לוודא ש-cache/Service Worker אכן עובדים.
