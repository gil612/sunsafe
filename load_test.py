"""
SunSafe — Load test: N virtual users through the interactive session
lifecycle (Supabase + Open-Meteo + the dashboard-data Edge Function),
run concurrently.

למה זה בכלל בטוח לבדוק ב-200: הנתיב הזה (set_skin_type -> start_session
-> end_session -> dashboard) לא נוגע ב-Gemini בכלל — /start_session
קורא ל-get_current_uv ישירות (Open-Meteo, לא MCP Agent Loop), ו-Gemini
נכנס לתמונה רק בשני מקומות נפרדים: סיווג סוג עור מתמונה, והדוח היזום
היומי (send_uv_report.py, דרך mcp_agent_loop). את שני אלה בכוונה *לא*
בודקים כאן ב-200 בבת אחת — ה-tier החינמי של Gemini מוגבל ל-~15-30
בקשות/דקה, אז 200 בקשות Gemini בו-זמנית יכשלו כמעט כולן ב-429 בלי קשר
לאיכות הקוד. זו לא תוצאה מעניינת לבדוק אמפירית, וזה יבזבז מכסה יומית
בחינם לשווא. אם תרצי לבדוק את זה בכל זאת - עדיף probe קטן ונשלט (10-20
בקשות), לא 200.

שימוש:
    python load_test.py --users 200 --concurrency 20
    python load_test.py --users 20 --concurrency 5 --keep   # לבדיקה ראשונה קטנה, בלי לנקות

חשוב: כל שורות הבדיקה מסומנות בתחילית ריצה ייחודית (RUN_TAG) ומנוקות
בסוף הריצה (מ-users/exposure_log/magic_links) - כדי שלא יישארו 200
משתמשי-דמה קבועים ב-DB האמיתי. --keep משאיר אותן אם רוצים לבדוק ידנית.
"""

import argparse
import concurrent.futures
import random
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

import bot_commands as bc  # noqa: E402  (אחרי load_dotenv, כמו בשאר הפרויקט)
from supabase_client import SupabaseConfig, insert_row, upsert_row  # noqa: E402

DASHBOARD_FUNCTION_URL = "https://wccueensecchmqfwdecw.supabase.co/functions/v1/dashboard-data"

# כמה ערים אמיתיות שמשתמשי הדמה "מתפזרים" עליהן, במקום לקרוא ל-Open-Meteo
# 200 פעם לחינם - שולפים UV פעם אחת לכל עיר ומשתמשים בו חוזר (מטרת
# הבדיקה היא Supabase/ה-Edge Function, לא לוודא ש-Open-Meteo עובד 200 פעם).
CITIES = [
    ("תל אביב", "ישראל", 32.0853, 34.7818),
    ("חיפה", "ישראל", 32.7940, 34.9896),
    ("ירושלים", "ישראל", 31.7683, 35.2137),
    ("באר שבע", "ישראל", 31.2530, 34.7915),
    ("אילת", "ישראל", 29.5581, 34.9482),
]

RUN_TAG = f"loadtest{int(time.time())}"
_uv_cache: dict[str, float] = {}
_uv_lock = threading.Lock()


def cached_uv(city: str, lat: float, lon: float) -> float:
    with _uv_lock:
        if city in _uv_cache:
            return _uv_cache[city]
    with httpx.Client() as client:
        uv = bc.get_current_uv(client, lat, lon)
    with _uv_lock:
        _uv_cache[city] = uv
    return uv


def run_virtual_user(i: int) -> dict:
    username = f"{RUN_TAG}_{i}"
    chat_id = 900_000_000 + i
    city, country, lat, lon = random.choice(CITIES)
    result = {"username": username, "steps": {}, "ok": False}
    t0 = time.time()

    try:
        t = time.time()
        upsert_row(
            "users",
            {"telegram_username": username, "skin_type": random.randint(1, 6), "chat_id": chat_id},
            on_conflict="telegram_username",
        )
        result["steps"]["upsert_user"] = time.time() - t
    except Exception as e:
        result["error"] = f"upsert_user: {e}"
        return result

    try:
        t = time.time()
        uv_index = cached_uv(city, lat, lon)
        result["steps"]["get_uv"] = time.time() - t
    except Exception as e:
        result["error"] = f"get_uv: {e}"
        return result

    try:
        t = time.time()
        start = datetime.now(timezone.utc) - timedelta(minutes=20)
        end = datetime.now(timezone.utc)
        score = bc.calculate_exposure_score(uv_index, 20, random.randint(1, 6), None)
        insert_row(
            "exposure_log",
            {
                "telegram_username": username,
                "city": city,
                "country": country,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "uv_index": uv_index,
                "spf": None,
                "exposure_score": score,
            },
        )
        result["steps"]["insert_session"] = time.time() - t
    except Exception as e:
        result["error"] = f"insert_session: {e}"
        return result

    try:
        t = time.time()
        link = bc.create_magic_link(username)
        token = link.rsplit("token=", 1)[-1]
        result["steps"]["create_magic_link"] = time.time() - t
    except Exception as e:
        result["error"] = f"create_magic_link: {e}"
        return result

    try:
        t = time.time()
        resp = httpx.get(DASHBOARD_FUNCTION_URL, params={"token": token}, timeout=15.0)
        ok = resp.status_code == 200 and resp.json().get("onboarded") is True
        result["steps"]["dashboard_fetch"] = time.time() - t
        result["dashboard_ok"] = ok
        if not ok:
            result["error"] = f"dashboard_fetch: status={resp.status_code} body={resp.text[:200]}"
            return result
    except Exception as e:
        result["error"] = f"dashboard_fetch: {e}"
        return result

    result["total"] = time.time() - t0
    result["ok"] = True
    return result


def cleanup() -> None:
    config = SupabaseConfig.from_env()
    headers = {
        "apikey": config.service_role_key,
        "Authorization": f"Bearer {config.service_role_key}",
    }
    base = config.url.rstrip("/") + "/rest/v1"
    pattern = f"like.{RUN_TAG}*"
    # סדר חשוב: exposure_log/magic_links לפני users (FK constraint על telegram_username).
    for table in ["magic_links", "exposure_log", "users"]:
        try:
            resp = httpx.delete(f"{base}/{table}", headers=headers, params={"telegram_username": pattern}, timeout=30.0)
            resp.raise_for_status()
        except Exception as e:
            print(f"  אזהרת ניקוי ({table}): {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SunSafe load test — N virtual users, non-Gemini path")
    parser.add_argument("--users", type=int, default=200, help="כמה משתמשי-דמה לסמלץ (ברירת מחדל 200)")
    parser.add_argument("--concurrency", type=int, default=20, help="כמה בו-זמנית (ברירת מחדל 20)")
    parser.add_argument("--keep", action="store_true", help="לא לנקות את שורות הבדיקה בסוף")
    args = parser.parse_args()

    print(f"מתחיל load test: {args.users} משתמשים, concurrency={args.concurrency}, tag={RUN_TAG}")
    t_start = time.time()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_virtual_user, i) for i in range(args.users)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    elapsed = time.time() - t_start

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    print("\n=== תוצאות ===")
    print(f"סה\"כ: {len(results)}  הצליחו: {len(ok)}  נכשלו: {len(failed)}")
    print(f"זמן כולל (wall clock): {elapsed:.1f} שניות")

    if ok:
        totals = [r["total"] for r in ok]
        print(f"זמן לכל משתמש (מקצה לקצה): min={min(totals):.2f}s avg={statistics.mean(totals):.2f}s max={max(totals):.2f}s")
        for step in ["upsert_user", "get_uv", "insert_session", "create_magic_link", "dashboard_fetch"]:
            times = [r["steps"][step] for r in ok if step in r["steps"]]
            if times:
                print(f"  {step}: avg={statistics.mean(times):.3f}s max={max(times):.3f}s")

    if failed:
        print(f"\nכשלים (עד 10 ראשונים מתוך {len(failed)}):")
        for r in failed[:10]:
            print(f"  {r['username']}: {r.get('error')}")

    if not args.keep:
        print(f"\nמנקה {len(results)} שורות בדיקה (tag={RUN_TAG})...")
        cleanup()
        print("נוקה.")
    else:
        print(f"\nשורות הבדיקה נשארו ב-DB (tag={RUN_TAG}) — לנקות ידנית כשתרצי.")


if __name__ == "__main__":
    main()
