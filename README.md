
# ☀️ SunSafe

מד חשיפה לשמש חכם — מעקב UV Index בזמן אמת, התראות אישיות בטלגרם, מבוסס
Gemini API ו-MCP (Model Context Protocol).

פרויקט גמר בקורס AI Dev.

## מה זה עושה

SunSafe עוקב אחרי רמת קרינת ה-UV במיקום שלך בזמן אמת, ומחשב כמה זמן אפשר
לשהות בשמש בבטחה — בהתאם לסוג העור שלך ולשימוש בקרם הגנה. כשה-UV גבוה,
הבוט שולח התראה בטלגרם עם המלצה ברורה (קרם הגנה, כובע, הימנעות מחשיפה
ישירה).

**דוגמה לשימוש:** שואלים את הבוט "מה ה-UV עכשיו בתל אביב?" — סוכן AI (מבוסס
Gemini) קורא בזמן אמת לשרת MCP ייעודי שמביא נתוני UV חיים, ומחזיר תשובה
עם המלצה מותאמת אישית תוך שניות.

## ארכיטקטורה

```
משתמש (טלגרם) ⇄ Agent Loop (Gemini API + Function Calling)
                        │
                        ▼
                 MCP Client ⇄ MCP Weather Server ⇄ Open-Meteo API
```

<div dir="rtl">
Agent Loop: מיושם ידנית ב-Python מול Gemini API (`google-genai`), ללא Framework חיצוני.

<div dir="rtl">
MCP Weather Server: שרת עצמאי שעוטף את Open-Meteo (חינמי, ללא API key) וחושף כלי מזג-אוויר (`get_current_uv`, `get_uv_forecast`) כ-MCP Tools סטנדרטיים. אותו שרת אפשר לחבר גם ל-Claude Desktop לבדיקה ידנית.
<div dir="rtl">
Telegram Bot: ממשק המשתמש להתראות ולשאילתות בזמן אמת.

<br>

לפירוט טכני מלא, קונבנציות פיתוח, והחלטות ארכיטקטורה — ראו [`CLAUDE.md`](./CLAUDE.md).

## התקנה והרצה

דרישות: Python 3.12+, חשבון Telegram, מפתח Gemini API חינמי.

```powershell
git clone https://github.com/gil612/sunsafe.git
cd sunsafe

python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

העתיקו את `.env.example` ל-`.env` ומלאו:

```
BOT_TOKEN=          # מ-@BotFather בטלגרם
CHAT_ID=             # ה-chat_id שלכם (ראו הוראות ב-CLAUDE.md)
GEMINI_API_KEY=      # מפתח חינמי מ-https://aistudio.google.com/apikey
```

הרצה:

```powershell
python mcp_agent_loop.py     # בדיקת Agent Loop מול MCP Server
python send_uv_report.py     # שליחת התראת UV בפועל לטלגרם
```

## סטטוס פרויקט

בפיתוח פעיל — ראו רשימת TODO מפורטת ב-[`CLAUDE.md`](./CLAUDE.md#todo-לפי-סדר-עדיפות).

## רישיון

פרויקט אקדמי — קורס AI Dev.

</div>
