
## Conventions

### Git Workflow

- `main` נשאר תמיד "נקי" — לא commit-ים ישירים אליו.
- כל פיצ'ר מקבל branch נפרד: `feature/<name>` (למשל `feature/telegram-bot`,
  `feature/mcp-weather-server`).
- בתוך כל branch — כמה commits קטנים והגיוניים (לא commit ענק אחד בסוף).
  קונבנציית הודעות: `feat: ...`, `chore: ...`, `fix: ...`.
- לפני יצירת branch חדש: **תמיד** `git checkout main && git pull origin main`
  קודם, כדי לא להסתעף מ-branch ישן שלא נמחק.
- מיזוג דרך Pull Request ב-GitHub (base=`main`), לא merge מקומי — כדי לשמור
  היסטוריית PRs קריאה. אחרי מיזוג — מוחקים את ה-branch (גם ב-GitHub וגם מקומית).

### ניהול Secrets

- `BOT_TOKEN`, `CHAT_ID`, `GEMINI_API_KEY` תמיד ב-`.env` (לא בקוד, לא ב-Git).
- `.env.example` מתעדכן במקביל ל-`.env` בכל פעם שמתווסף משתנה חדש, כדי
  שסביבת פיתוח חדשה תדע אילו ערכים נדרשים.

### AI Provider

- המסלול הנבחר: **Gemini Developer API** (`genai.Client(api_key=...)`),
  **לא** Vertex AI — כדי לא לדרוש פרויקט GCP ו-billing. זו החלטה מודעת,
  לא פשרה — הקורס מאפשר את שני המסלולים.
- אם בעתיד יידרש מעבר ל-Vertex AI (למשל בשל קרדיטים זמינים): משנים רק את
  פונקציית ה-`make_client()`, שאר הקוד (Agent Loop, Tools) לא משתנה.

### MCP SDK — גרסה מוצמדת (Pin)

- `mcp>=1.27,<2.0` — **מכוון**. גרסה 2.0.0 (יולי 2026) הכניסה Breaking
  Changes משמעותיים (`FastMCP` → `MCPServer`, שינוי מבנה ה-Client מ-
  `ClientSession` ל-`Client` מאוחד, פרוטוקול MCP חדש/Stateless). הגרסה
  יצאה זמן קצר לפני ההגשה ועדיין לא יציבה מספיק לפרודקשן; לכן הוחלט
  להצמיד לגרסת ה-1.x היציבה עד שה-2.0 תתייצב.

### Error Handling

- כל קריאת רשת חיצונית (Telegram, Open-Meteo, Gemini) עטופה ב-try/except
  עם לוגים ברורים.
- שליחת הודעות לטלגרם: ניסיון ראשון עם MarkdownV2 מפורמט; אם הפרסינג נכשל
  (למשל בגלל טקסט חופשי מה-AI) — נפילה אוטומטית לטקסט רגיל, כדי שההדגמה
  בכיתה לא תיכשל על שגיאת פורמט.
- ולידציית עיר: `send_uv_report.py` **לא** נותן ל-Gemini לנחש קואורדינטות
  מתוך "ידע כללי" — הסוכן מונחה לקרוא תמיד קודם ל-`geocode_city`, וכשהעיר
  לא נמצאת (`found=False`) מוחזרת הודעת שגיאה ברורה במקום ניחוש בביטחון מלא.

### שפה בקוד

- הודעות ללוגים ולמשתמש (Telegram, הודעות שגיאה) — בעברית.
- שמות משתנים/פונקציות/docstrings טכניים — יכולים להיות מעורבים (עברית
  להסבר, אנגלית לשמות קוד סטנדרטיים).

## איך מריצים מקומית

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
# מלאו .env לפי .env.example
python mcp_agent_loop.py             # בדיקת Agent Loop + MCP
python send_uv_report.py "אילת"      # בדיקת שליחה בפועל לטלגרם, לעיר לפי בחירה
```

## סטטוס נוכחי

✅ Telegram Bot מחובר ונבדק (`gil612Bot`) <br>
✅ Agent Loop מול Gemini API, דרך MCP Client בלבד (`mcp_agent_loop.py`) —
  הגרסה הישנה עם Tool מוגדר ידנית (`agent_loop.py`) הוסרה לגמרי מהריפו<br>
✅ MCP Weather Server עם 3 כלים: `geocode_city`, `get_current_uv`,
  `get_uv_forecast`<br>
✅ חיבור Agent → Telegram עם fallback לטקסט רגיל, כולל הדגשת bold על
  מספר ה-UV והסיווג (`send_uv_report.py`)<br>
✅ עיר כפרמטר argv (`python send_uv_report.py "<עיר>"`) — עם ולידציה
  אמיתית מול Open-Meteo Geocoding (לא ניחוש lat/lon), כולל טיפול בשגיאות
  הקלדה/עיר לא מוכרת עם הודעת שגיאה ברורה במקום ניחוש<br>
✅ Git עם feature branches ו-PRs מסודרים; docstrings תורגמו לאנגלית<br>
✅ Supabase logging חי לשתי הטבלאות הראשונות: `uv_readings` (נכתב על-ידי
  הסוכן עצמו, דרך כלי ה-MCP `log_uv_reading`, מיד אחרי `get_current_uv`)
  ו-`alerts_sent` (נכתב מקוד חיצוני ב-`send_uv_report.py`, אחרי כל שליחה
  אמיתית לטלגרם — גם בהצלחה וגם בנתיב "עיר לא זוהתה"). נבדק קצה-לקצה:
  נתיב הצלחה כותב לשתי הטבלאות, נתיב "עיר לא זוהתה" כותב רק ל-`alerts_sent`
  (`uv_reading_id` נשאר NULL) בלי לגעת ב-`uv_readings`

## TODO (לפי סדר עדיפות)

1. שני Tools נוספים לשרת ה-MCP, כדי להגיע ל-5 הנדרשים בקורס
   (כרגע יש 3: geocode_city, get_current_uv, get_uv_forecast):
   - calculate_exposure_score(uv, duration_minutes, skin_type, spf) —
     מימוש בקוד production של נוסחת ה-exposure_score (כרגע קיימת רק
     כ-JS בדף ההדגמה, לא ב-backend האמיתי)
   - send_telegram_report(message) — ה-Tool האחרון שהסוכן עצמו קורא לו
     כפעולה מסיימת בתוך ה-Agent Loop (ולא, כמו היום, קוד חיצוני
     ב-send_uv_report.py ששולח *אחרי* שהלולאה כבר הסתיימה) —
     דרישת קורס מפורשת

2. Error handling: שגיאה נשלחת לטלגרם אם הסוכן נכשל — כרגע אם
   max_iterations נחרג (RuntimeError) או קורה Exception אחר בהרצת
   ה-Agent, שום דבר לא נתפס ולא נשלח למשתמש. יש לעטוף את
   run_agent_via_mcp(task) ב-try/except ולשלוח התראת שגיאה בפועל

3. Logging עם timestamp אמיתי — logging.basicConfig(level=logging.INFO)
   לא כולל timestamp בפורמט ברירת המחדל. להוסיף
   format="%(asctime)s %(levelname)s %(name)s: %(message)s"

4. Cost Tracking — לספור Tokens מכל תשובה של Gemini (usage_metadata),
   לחשב עלות לפי תמחור gemini-3.5-flash-lite, ולצרף/לשלוח בסוף כל
   ריצה (כולל בהודעת הטלגרם עצמה)

5. בניית זרימת שיחה מלאה לבוט הטלגרם (conversation flow) — כרגע כל
   הרצה היא שאילתת עיר בודדת חד-פעמית, לא שיחה רב-שלבית עם המשתמש
   [ממפגישת מעקב 20.08.2026 עם שון גרייס]

6. מודל נתונים ב-Supabase — פלח ראשון **בוצע**: `uv_readings` ו-`alerts_sent`
   קיימות וחיות (ראו "סטטוס נוכחי" למעלה). נותרו: `users`, `locations`,
   `exposure_log` — אלה תלויות בנוסחת ה-exposure_score (calculate_exposure_score,
   סעיף 1 למעלה) שעדיין לא מומשה, ולכן ממתינות לה

7. Dashboard ב-Next.js: היסטוריה, גרפים, הגדרות פרופיל (סוג עור, מיקום)

8. Deploy לפרודקשן (Cloudflare Workers/Pages) + Cloudflare Cron Trigger
   מוגדר ב-wrangler.toml, בזמן ישראל — דרישת קורס מפורשת

9. חישוב עלות מדויק ל-100 בקשות/יום — שונה מ-Cost Tracking בסעיף 4:
   זו תחזית ברמת production (100 בקשות ביום), לא מדידה בפועל בריצה בודדת

## החלטות פתוחות (טרם הוכרעו — לא TODO טכני)

- האם להוסיף תכונת אבחון מצב עור לאחר חשיפה לשמש? — נדון כרעיון בפגישה,
  טרם הוחלט [ממפגישת מעקב 20.08.2026]
- מהו טווח ה-dashboard: למשתמש קצה, לניהול, או שניהם?
  [ממפגישת מעקב 20.08.2026]

## לפני ההדגמה בכיתה

- לאחר שסעיפים 1-4 יושמו — לבדוק שוב את כל השטף חי מקצה לקצה
  (הסוכן קורא ל-5 הכלים כנדרש, כולל שליחה עצמאית לטלגרם + cost report),
  כי הארכיטקטורה תשתנה משמעותית לעומת מה שנבדק היום