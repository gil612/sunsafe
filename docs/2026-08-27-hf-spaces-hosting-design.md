# אירוח 24/7 על Hugging Face Spaces + Keep-Alive — Design

## הבעיה

`bot_commands.py` (ה-listener האמיתי מול טלגרם: `/dashboard`,
`/set_skin_type`, `/start_session`, `/end_session`, תמונה, מיקום) עד
היום רץ רק כשהמחשב של המשתמש דלוק ו-VS Code פתוח. ברגע שהמחשב נכבה,
נרדם, או מתנתק מהאינטרנט — הבוט מפסיק להגיב, לגמרי בלי תלות באיכות
הקוד עצמו. המטרה: להריץ את אותו קוד בדיוק במקום שרץ תמיד, בלי תלות
במחשב האישי.

## למה Hugging Face Spaces (ולא Railway/Render/Fly.io/Oracle)

נבדקו כמה חלופות (חיפוש עדכני, אוגוסט 2026):

- **Railway / Render / Fly.io**: כולן עברו למסלול בעיקרו בתשלום עבור
  תהליך רץ-מתמיד (Fly.io: "there is no free tier" במפורש בתיעוד
  הרשמי שלהם).
- **Oracle Cloud Always Free VM**: אמנם באמת חינמי-לתמיד, ואפס שינוי
  קוד (מריצים את `bot_commands.py` הקיים בדיוק כמות שהוא) — אבל דורש
  כרטיס אשראי לאימות זהות (לא חיוב בפועל) וניהול VM עצמאי (עדכוני
  מערכת, systemd, אבטחה) — עומס תפעולי לא מוצדק לפרויקט קורס.
- **xhostd**: פלטפורמה חדשה, "agent-first hosting" — נבדקה, אבל אין
  מספיק תיעוד ציבורי לגבי תמיכה ב-Python/תהליכי-רקע, ואין track
  record מוכר. סיכון לא מוצדק לדדליין של קורס.
- **Google Cloud Run**: free tier מאומת ופעיל (2M בקשות/חודש) — אבל
  דורש מעבר ארכיטקטוני מ-polling ל-webhook (שינוי קוד מוכל, אבל שינוי
  אמיתי) וחשבון Google Cloud.
- **Hugging Face Spaces (CPU Basic)**: מאומת חינמי לגמרי, בלי כרטיס
  אשראי (חשבון אישי חינמי מספיק ל-Gradio/Docker Spaces), משמר את קוד
  ה-polling הקיים כמעט ב-1:1 (עוטפים ב-thread ברקע במקום לשכתב ל-
  webhook). המחיר: Spaces חינמיים "נרדמים" אחרי חוסר-פעילות ב-HTTP
  הנכנס (לא ניתן לביטול בחומרה חינמית, לפי התיעוד הרשמי), אז צריך
  keep-alive חיצוני. זה נבחר — הכי פחות מאמץ, המשתמש בחר את זה במפורש
  אחרי שהוסברו הפשרות.

## איך זה עובד

### 1. app.py — עטיפת Gradio מינימלית

Hugging Face Spaces (SDK: Gradio) מצפה ל-`app.py` כנקודת הכניסה.
`app.py` **לא** משנה שום דבר בלוגיקה הקיימת — הוא רק:

1. מייבא את `poll_forever` מ-`bot_commands.py` הקיים (בלי שום שינוי בו).
2. מריץ אותו ב-thread נפרד (`daemon=True`) ברקע, עם flag+lock שמונע
   הפעלה כפולה בטעות (אותה בעיית "שני מאזינים על אותו BOT_TOKEN"
   שכבר נתקלנו בה עם ה-409 Conflict — ראו
   docs/2026-08-26-*, ה-postmortem של אותו אירוע).
3. מציג עמוד Gradio סטטי, סתמי, שלא עושה שום דבר פונקציונלי — קיים
   *רק* כדי לתת ל-Space כתובת URL אפשר "לפנג'" כדי שלא ירדם.

הבוט האמיתי (ה-thread) לא תלוי בעמוד ה-Gradio בשום צורה — גם אם אף
אחד לעולם לא פותח את העמוד בדפדפן, ה-thread ברקע ימשיך לרוץ כל עוד
ה-Space כולו ער (כלומר כל עוד ה-keep-alive מצליח).

**נבדק בסביבת פיתוח**: `app.py` יובא בהצלחה עם משתני סביבה מדומים —
`demo` נוצר כ-`gradio.blocks.Blocks` תקין, ה-thread עלה ("SunSafe bot
polling thread started"), ו-`poll_forever` בפנים התחיל לרוץ ("Listening
for commands..."). כשל הרשת (חסימת proxy בסביבת הבדיקה) נתפס נכון
ע"י התיקון הקיים ב-`poll_forever` (retry אחרי 5 שניות, לא קריסה) —
מוודא בעקיפין שגם התיקון ההוא עדיין עובד.

### 2. Secrets — לא .env

ב-Spaces, סודות (BOT_TOKEN, GEMINI_API_KEY, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY) מוגדרים דרך Settings → "Variables and
secrets" של ה-Space, לא דרך קובץ `.env` (`.env` ממילא לא נכנס ל-git,
לפי `.gitignore` הקיים). Hugging Face מזריק אותם כמשתני סביבה בזמן
ריצה — `os.environ[...]`/`os.environ.get(...)` הקיימים בקוד ממשיכים
לעבוד בלי שום שינוי. **קריטי**: כל הסודות חייבים להיות מוגדרים *לפני*
ההפעלה הראשונה — `bot_commands.py` קורא `os.environ["BOT_TOKEN"]`
(לא `.get`) ברמת המודול, אז חוסר סוד יגרום לקריסה מיידית של ה-Space
כבר בעליית הקוד (נראה בלוג של ה-Space כ-`KeyError`).

### 3. Keep-alive — GitHub Actions, לא שירות חיצוני נוסף

הפרויקט כבר על GitHub (`github.com/gil612/sunsafe`), אז GitHub
Actions הוא אפס-הרשמה-נוספת: `.github/workflows/keep-alive.yml` עם
`schedule: cron` כל 10 דקות, ששולח GET לכתובת ה-Space
(`https://<hf-username>-<space-name>.hf.space`). לא נכתב שום קוד
שרת — זה request HTTP רגיל שרק "מוכיח פעילות" ל-Spaces.

**מגבלה ידועה**: GitHub משבית אוטומטית workflows מתוזמנים במאגר בלי
שום commit במשך 60 יום — לא רלוונטי בטווח הפרויקט (קורס פעיל), אבל
שווה לזכור אם הפרויקט "יישן" בעתיד.

### 4. פריסה (git push, לא CI/CD אוטומטי)

בכוונה **לא** בנינו auto-deploy (GitHub→Space בכל push) — זה עוד
מערכת CI להחזיק. הפריסה נשארת `git push` ידני לריפו של ה-Space
(בדיוק כמו ה-deploy הידני הקיים ל-Supabase Edge Function דרך ה-CLI)
— עקבי עם המוסכמה הקיימת בפרויקט. רק ה-keep-alive באמת *חייב* להיות
אוטומטי (אי אפשר לצפות ממישהו לפנג' כל 10 דקות ידנית).

### 5. חשוב: לא להריץ שני מופעים במקביל

אחרי שה-Space עולה ומאזין בהצלחה, **צריך להפסיק להריץ
`bot_commands.py` מקומית** (ב-VS Code) — שני מאזינים על אותו
BOT_TOKEN בו-זמנית גורמים בדיוק לאותו 409 Conflict שכבר ראינו וטיפלנו
בו פעם אחת בעבר.

## לא בהיקף הזה

- **`send_uv_report.py --broadcast`** (הדוח היזום היומי) — לא עובר
  לאירוח היום. זה תהליך *חד-פעמי מתוזמן*, לא listener מתמיד — דורש
  מנגנון שונה (endpoint ב-Space + GitHub Actions cron יומי שמפעיל
  אותו, או Cloud Scheduler נפרד). שיפור עתידי, לא נדרש כדי לפתור את
  "הקוד רץ גם כשהמחשב סגור" עבור הפקודות האינטראקטיביות.
- **`DASHBOARD_BASE_URL`** — עדיין `http://localhost:8080` כברירת
  מחדל; קישורי ה-Magic Link מ-`/dashboard` ימשיכו להצביע למחשב
  המקומי עד שגם עמוד ה-dashboard עצמו יתארח איפשהו אמיתי. פער נפרד,
  לא נפתר כאן.
- Auto-deploy/CI מ-GitHub ל-Space — נשאר `git push` ידני (ראו סעיף 4).

## בדיקות

1. `app.py` רץ מקומית (או ב-Space) בלי לקרוס — הלוג מראה גם
   "SunSafe bot polling thread started" וגם "Listening for commands"
   (מ-`bot_commands.py` הקיים).
2. הבוט מגיב לפקודה אמיתית בטלגרם (למשל `/dashboard`) בזמן שהוא רץ
   על ה-Space, לא על המחשב המקומי.
3. ה-Space נשאר ער אחרי 20+ דקות בלי שום פעולה ידנית (ה-keep-alive
   של GitHub Actions עובד בפועל — נבדק ב-Actions tab של הריפו).
4. הרצה מקומית מכובה לגמרי (`bot_commands.py` לא רץ יותר ב-VS Code)
   — הבוט עדיין מגיב, כי הוא רץ על ה-Space בלבד.
