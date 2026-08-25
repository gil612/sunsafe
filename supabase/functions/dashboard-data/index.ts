// SunSafe — Magic Link Edge Function ("dashboard-data")
// -------------------------------------------------------
// מחזירה את הפרופיל וה-Sessions הסגורים של משתמש, לפי Magic Link Token
// שהבוט יצר ב-/dashboard (ראו create_magic_link ב-bot_commands.py).
//
// REST ישיר מול PostgREST עם ה-service_role key (מוזרק אוטומטית ע"י
// Supabase לכל Edge Function — לא צריך secrets ידניים) — עקבי עם
// supabase_client.py (בלי @supabase/supabase-js, בלי SDK נוסף).
//
// פריסה: supabase functions deploy dashboard-data --no-verify-jwt
// (בלי הדגל הזה, Supabase דוחה כל בקשה בלי Auth JWT עוד לפני שהקוד
// כאן רץ בכלל — הדף הסטטי לא מחובר ל-Supabase Auth, הטוקן עצמו הוא
// מנגנון האימות).
//
// ראה docs/2026-08-25-magic-link-edge-function-design.md לחוזה המלא,
// כולל טבלת קודי השגיאה וההחלטות המכוונות (בלי display_name, בלי
// שימוש בשדה `used`).

import {
  buildDashboardBody,
  CORS_HEADERS,
  errorResponse,
  isTokenExpired,
  jsonResponse,
} from "./logic.ts";
import type { ExposureLogRow } from "./logic.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const REST_HEADERS = {
  apikey: SERVICE_ROLE_KEY,
  Authorization: `Bearer ${SERVICE_ROLE_KEY}`,
};

async function restGet(path: string): Promise<unknown[]> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: REST_HEADERS,
  });
  if (!response.ok) {
    throw new Error(
      `PostgREST GET ${path} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    // Preflight — 204 בלי לגעת ב-DB בכלל.
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const token = new URL(req.url).searchParams.get("token");
  if (!token) {
    return errorResponse("missing_token");
  }

  try {
    const links = (await restGet(
      `magic_links?token=eq.${encodeURIComponent(token)}` +
        `&select=telegram_username,expires_at`,
    )) as { telegram_username: string; expires_at: string }[];

    if (links.length === 0) {
      return errorResponse("invalid_token");
    }
    const { telegram_username, expires_at } = links[0];
    if (isTokenExpired(expires_at, new Date())) {
      return errorResponse("expired_token");
    }

    const encodedUsername = encodeURIComponent(telegram_username);
    const [users, sessions] = await Promise.all([
      restGet(
        `users?telegram_username=eq.${encodedUsername}&select=skin_type`,
      ) as Promise<{ skin_type: number }[]>,
      restGet(
        `exposure_log?telegram_username=eq.${encodedUsername}` +
          `&end_time=not.is.null` +
          `&select=id,city,country,start_time,end_time,uv_index,spf,exposure_score` +
          `&order=start_time.desc`,
      ) as Promise<ExposureLogRow[]>,
    ]);

    const body = buildDashboardBody(telegram_username, users[0], sessions);
    return jsonResponse(body, 200);
  } catch (err) {
    console.error("dashboard-data failed:", err);
    return errorResponse("server_error");
  }
});
