# CLAUDE.md

מסמך זה מיועד לספק הקשר ל-Claude Code (ולכל מפתח/ת) כשעובדים על הפרויקט.
הוא מתאר את הפרויקט, את ה-Tech Stack, ואת הקונבנציות שיש לשמור עליהן.

## על הפרויקט

**SunSafe** — אפליקציה למעקב אחר חשיפה אישית לקרינת UV. המערכת קוראת UV Index
בזמן אמת לפי מיקום, ומחשבת "זמן חשיפה בטוח" מותאם אישית (לפי סוג עור בסולם
Fitzpatrick ושימוש בקרם הגנה), ומתריעה למשתמש בטלגרם כשצריך הגנה מהשמש.

הפרויקט נבנה כפרויקט גמר בקורס AI Dev, ומבוסס על וריאציה של "פרויקט ג' —
סוכן Intelligence יומי עם Telegram" מה-SPEC של הקורס, עם תוספת של MCP Server
עצמאי במקום כלים המוגדרים ידנית בקוד.

## Tech Stack

| רכיב | טכנולוגיה | הערות |
|---|---|---|
| שפה | Python 3.12 | סביבה וירטואלית (`venv`) |
| AI Model | Gemini API (`google-genai`), מודל `gemini-3.5-flash-lite` | מסלול Developer API עם `GEMINI_API_KEY` — **לא** Vertex AI. נבחר כברירת המחדל של הקורס; לא דורש פרויקט GCP או billing. |
| Agent Loop | מיושם ידנית (`agent_loop.py` / `mcp_agent_loop.py`) | ללא Framework חיצוני (לא LangChain/AutoGen) |
| Tool Layer | MCP (Model Context Protocol), חבילת `mcp` | ראה "החלטות טכניות" למטה לגבי גרסה |
| מקור מזג אוויר | Open-Meteo API | חינמי, ללא API key |
| התראות | Telegram Bot API | Bot: `gil612Bot` |
| DB (מתוכנן) | Supabase (Postgres) | טרם מומש בקוד |
| Dashboard (מתוכנן) | Next.js + Tailwind | טרם מומש |
| Hosting (מתוכנן) | Cloudflare Workers + Pages, Cloudflare Cron Triggers | טרם מומש |
| ניהול גרסאות | Git + GitHub, `github.com/gil612/sunsafe` | ראה Conventions למטה |

## מבנה הקבצים הנוכחי

```
sunsafe/
├── telegram_client.py       # עטיפת Telegram Bot API: send_text_message, send_uv_alert, escape_markdown_v2
├── agent_loop.py             # Agent Loop מול Gemini API עם Tool אחד מוגדר ידנית (get_current_uv) — גרסה ראשונית לבדיקה
├── mcp_weather_server.py     # שרת MCP עצמאי שעוטף Open-Meteo: get_current_uv, get_uv_forecast
├── mcp_agent_loop.py         # גרסת ה-Agent Loop שמתחברת ל-mcp_weather_server.py כ-MCP Client (הגרסה הנוכחית/production)
├── send_uv_report.py         # מחבר בין agent_loop לבין Telegram: מריץ שאלה, ממיר Markdown, שולח בפועל
├── .env / .env.example       # BOT_TOKEN, CHAT_ID, GEMINI_API_KEY (הקובץ .env אף פעם לא ב-Git)
├── requirements.txt
├── .gitignore                # כולל .env, venv/, __pycache__/
├── README.md
└── CLAUDE.md                 # המסמך הזה
```

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
python mcp_agent_loop.py          # בדיקת Agent Loop + MCP
python send_uv_report.py          # בדיקת שליחה בפועל לטלגרם
```

## סטטוס נוכחי

✅ Telegram Bot מחובר ונבדק (`gil612Bot`)
✅ Agent Loop מול Gemini API + Tool ראשוני (`agent_loop.py`)
✅ MCP Weather Server + Agent Loop דרך MCP Client (`mcp_weather_server.py`,
  `mcp_agent_loop.py`)
✅ חיבור Agent → Telegram עם fallback לטקסט רגיל (`send_uv_report.py`)
✅ Git עם feature branches ו-PRs מסודרים

## TODO (לפי סדר עדיפות)

1. חיבור `send_uv_report.py` לגרסת ה-MCP (`mcp_agent_loop`) במקום
   `agent_loop` הישן
2. `exposure_score` — נוסחה שמשלבת UV Index, זמן שהייה, סוג עור (Fitzpatrick)
   ושימוש בקרם הגנה (כולל SPF ותפוגת תוקף) למדד חשיפה יחיד 0-100+
3. מודל נתונים ב-Supabase: `users`, `locations`, `uv_readings`,
   `exposure_log`, `alerts_sent`
4. Dashboard ב-Next.js: היסטוריה, גרפים, הגדרות פרופיל (סוג עור, מיקום)
5. Deploy לפרודקשן (Cloudflare Workers/Pages) + Cloudflare Cron Triggers
   לתזמון אוטומטי
6. חישוב עלות מדויק ל-100 בקשות/יום
