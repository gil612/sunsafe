# שיתוף מיקום מהטלפון ל-/start_session — Design

## מטרה

היום `/start_session <עיר>` דורש מהמשתמש להקליד שם עיר. המטרה כאן:
לאפשר גם לחיצה אחת על כפתור "שתפו מיקום" בטלגרם (native location sharing,
לא Mini App) — הבוט מקבל lat/lon ישירות מהטלפון, הופך אותם לשם עיר דרך
Nominatim (reverse geocoding), ומתחיל session בלי הקלדה.

**חשוב להבחין**: זה *לא* אותו דבר כמו רעיון ה-Telegram Mini App שדיברנו
עליו קודם. ה-Mini App נועד לפתור בעיה אחרת — timestamp אמין ל-start/end
כשאין קליטה בכלל (המשתמש כותב הודעה כשהטלפון offline, וטלגרם שולח אותה
מאוחר יותר עם `message.date` של זמן ה*קליטה בשרת*, לא זמן הכתיבה בפועל).
שיתוף מיקום, לעומת זאת, הוא פיצ'ר סטנדרטי של Telegram Bot API
(`request_location` על כפתור ב-reply keyboard) שעובד היום בלי שום קוד
בצד הלקוח — לא צריך Mini App בשביל זה בכלל. שתי הבעיות עדיין נפרדות;
הבעיה של "אין קליטה" נשארת פתוחה ולא נפתרת כאן.

## איך זה עובד ב-Telegram Bot API

1. הבוט שולח הודעה עם `reply_markup.keyboard` שמכיל כפתור אחד עם
   `request_location: true`. זה כפתור מובנה בטלגרם — לחיצה עליו גורמת
   ללקוח (עם אישור המשתמש) לשלוח הודעת `location` עם `latitude`/
   `longitude` אמיתיים מה-GPS של הטלפון, בלי קוד JS/Mini App custom.
2. הבוט מקבל `update.message.location` (כמו שהוא כבר מקבל
   `update.message.photo` להצעת סוג עור).
3. Reverse geocoding: הופכים lat/lon לשם עיר. Open-Meteo (המקור הקיים
   ל-`geocode_city`) תומך רק ב-forward geocoding (שם->קואורדינטות), לא
   reverse — אז המקור הפשוט ביותר בלי מפתח API הוא **Nominatim**
   (OpenStreetMap).

## Nominatim — עמידה ב-Usage Policy

לפי [מדיניות השימוש הרשמית](https://operations.osmfoundation.org/policies/nominatim/)
(אומתה קודם דרך WebFetch באותה שיחה):

- **User-Agent מזהה חובה** — לא ברירת המחדל של httpx/requests. משתמשים
  ב-`SunSafe-Bot/1.0 (student course project)` בכותרת הבקשה.
- **מקסימום בקשה אחת בשנייה** — לא רלוונטי כבעיה בפועל כאן: יש לכל היותר
  קריאת reverse geocoding *אחת* לכל `/start_session` (לא לולאה, לא batch),
  אז אין סיכון לחרוג מהמגבלה.
- שירות חינמי, בלי מפתח API, endpoint: `https://nominatim.openstreetmap.org/reverse`.
- תוצאה: `address` dict עם שדות משתנים לפי סוג המקום (`city`/`town`/
  `village`/`municipality`/`county`) — לא תמיד יש `city` נקי (למשל כפר
  קטן). לכן `reverse_geocode_location` בודקת כמה שדות בסדר עדיפות ונופלת
  חזרה ל"לא זוהתה עיר" אם אף אחד לא קיים.

## זרימה

1. `/start_session` בלי ארגומנט (במקום הודעת שימוש שגויה כמו היום) →
   בדיקות קיימות (יש סוג עור? אין session פתוח?) ואז שליחת כפתור "שתפו
   מיקום" (`prompt_location_share`), עם הבהרה ש-`/start_session <עיר>`
   הידני עדיין עובד.
2. `handle_update` מזהה `message["location"]` (בדיוק כמו שהוא כבר מזהה
   `message["photo"]`) → `handle_start_session_location(chat_id,
   username, lat, lon)`.
3. אותן בדיקות קיימות (`_can_start_session` — פונקציה משותפת שחולצה
   מ-`handle_start_session` כדי לא לשכפל לוגיקה).
4. `reverse_geocode_location` מול Nominatim → שם עיר + מדינה.
5. `get_current_uv` (הפונקציה הקיימת, בלי שינוי) — עם ה-lat/lon *המדויקים
   מהטלפון*, לא מרכז-העיר המשוער כמו בנתיב ההקלדה. זה בפועל שיפור דיוק
   קטן גם למי שכבר משתמש בזרימה הזו.
6. `_begin_session` — פונקציה משותפת שחולצה מ-`handle_start_session`
   (כתיבת `exposure_log` + הודעת אישור) — עכשיו משמשת גם את נתיב ההקלדה
   וגם את נתיב המיקום, כדי לא לשכפל את לוגיקת הכתיבה ל-DB.
7. הודעת אישור + `reply_markup: {remove_keyboard: true}` כדי להעלים את
   כפתור "שתפו מיקום" אחרי שהוא שימש את מטרתו (לא רלוונטי בנתיב ההקלדה,
   שם לא הוצג כפתור מלכתחילה).

## למה בלי state/session בזיכרון

לא שומרים "המשתמש X נמצא באמצע זרימת start_session ומחכה למיקום" באף
מקום. כל הודעת `location` נכנסת מתפרשת תמיד כ"התחל session כאן" (בדיוק
כמו שכל הודעת `photo` מתפרשת תמיד כ"הצע סוג עור"). זה עקבי עם העיצוב
הקיים של הבוט כולו — כל handler עצמאי, בלי זיכרון שיחה בין הודעות (ראו
ההערה על "TODO #5" ב-`bot_commands.py`) — ונשאר נכון כל עוד לבוט אין
עוד שימוש למיקום חוץ מהתחלת session.

## טיפול בשגיאות

- Nominatim לא מחזיר עיר מזוהה (`found=False`) → הודעה עברית, הפניה
  ל-`/start_session <עיר>` ידני.
- כשל רשת/HTTP ב-Nominatim או ב-Open-Meteo (timeout, 5xx וכו') — לא
  נתפס באופן ספציפי בתוך ה-handler; ממשיך לבעבע לבלוק ה-`try/except
  Exception` הכללי סביב `handle_update` בתוך `poll_forever` (קיים כבר),
  שרושם ללוג וממשיך ללולאה — לא מפיל את הבוט. זהה לטיפול הקיים בכשל
  רשת ב-`handle_start_session` (נתיב הקלדה), לא שינוי התנהגות.

## תיקון קטן נלווה (לא בהיקף המקורי, אבל קשור ישירות)

תוך כדי הבדיקה החיה של פיצ'ר התמונה גילינו 409 Conflict אמיתי מטלגרם
(שני מאזינים על אותו טוקן) שהפיל את כל התהליך — כי `response.
raise_for_status()` על קריאת ה-`getUpdates` עצמה, בתוך `poll_forever`,
נמצא *מחוץ* ל-`try/except` שכבר עוטף את עיבוד כל update. תוקן: עטיפת
כל גוף הלולאה (כולל קריאת `getUpdates`) ב-`try/except`, עם לוג + המתנה
קצרה (מנגנון backoff מינימלי) לפני ניסיון חוזר, כדי שכשל HTTP חד-פעמי
(409 מקרי, timeout, Telegram למטה לרגע) לא יפיל את כל הבוט.

## בדיקות

1. `reverse_geocode_location` מול קואורדינטות אמיתיות (למשל תל אביב,
   32.08/34.78) → שם עיר סביר בעברית.
2. קואורדינטות באמצע הים/מקום בלי כתובת → `found=False`, בלי קריסה.
3. זרימה מלאה: `/start_session` בלי ארגומנט → מופיע כפתור מיקום →
   שיתוף מיקום → session נפתח עם UV+עיר נכונים, הכפתור נעלם.
4. `/start_session <עיר>` הישן ממשיך לעבוד בדיוק כמו קודם (רגרסיה).
5. שיתוף מיקום כשיש כבר session פתוח / בלי סוג עור מוגדר → אותן הודעות
   שגיאה כמו בנתיב ההקלדה (כי `_can_start_session` משותפת).

## לא בהיקף הזה

- Mini App / פתרון לבעיית "אין קליטה" — נשאר כיוון עתידי נפרד.
- שמירת lat/lon מדויקים ב-DB (רק שם עיר/מדינה, כמו היום) — לא נדרש כרגע.
- Caching של תוצאות reverse geocoding — לא נדרש בהיקף השימוש הנוכחי
  (בקשה בודדת לכל session, לא לולאה).
