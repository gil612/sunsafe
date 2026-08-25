// SunSafe — Magic Link Edge Function: pure logic (no Deno/network globals).
//
// מכוון להישאר טהור (בלי Deno.serve/Deno.env/fetch) כדי שאפשר לבדוק אותו
// גם מחוץ ל-Deno (למשל תחת Node עם --experimental-strip-types) — אותו
// עיקרון כמו calculate_exposure_score ב-mcp_weather_server.py: לוגיקה
// טהורה נפרדת מ-I/O, קלה לבדיקה בלי תלות ברשת/סביבה.
//
// ראה docs/2026-08-25-magic-link-edge-function-design.md לרציונל המלא.

export const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
};

export type ErrorCode =
  | "missing_token"
  | "invalid_token"
  | "expired_token"
  | "server_error";

const ERROR_STATUS: Record<ErrorCode, number> = {
  missing_token: 400,
  invalid_token: 404,
  expired_token: 410,
  server_error: 500,
};

export interface ExposureLogRow {
  id: number;
  city: string;
  country: string | null;
  start_time: string;
  end_time: string;
  uv_index: number;
  spf: number | null;
  exposure_score: number | null;
}

export interface DashboardResponseBody {
  telegram_username: string;
  onboarded: boolean;
  skin_type: number | null;
  sessions: ExposureLogRow[];
}

/** JSON response עם CORS headers מוטמעים — משותף להצלחה ולשגיאה. */
export function jsonResponse(
  body: unknown,
  status: number,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...CORS_HEADERS,
      ...extraHeaders,
    },
  });
}

/** תשובת שגיאה עקבית: {"error": code} + סטטוס HTTP הנכון לאותו code. */
export function errorResponse(code: ErrorCode): Response {
  return jsonResponse({ error: code }, ERROR_STATUS[code]);
}

/**
 * true אם ה-token פג תוקף. `now` מוזרק (לא Date.now() פנימי) כדי
 * שהבדיקה תישאר דטרמיניסטית וניתנת לבדיקה.
 */
export function isTokenExpired(expiresAtIso: string, now: Date): boolean {
  return new Date(expiresAtIso).getTime() <= now.getTime();
}

/**
 * בונה את גוף התשובה המוצלח לפי החוזה במסמך העיצוב. `skinTypeRow` הוא
 * השורה הראשונה (אם יש) מ-`users` — undefined/null אומר שהמשתמש עדיין
 * לא הריץ /set_skin_type, ולכן onboarded=false ו-sessions ריק בכוונה
 * (session בלי סוג עור ידוע לא אמור להיווצר מלכתחילה דרך /start_session,
 * שכבר חוסם את זה — ראו handle_start_session ב-bot_commands.py).
 */
export function buildDashboardBody(
  telegramUsername: string,
  skinTypeRow: { skin_type: number } | null | undefined,
  sessions: ExposureLogRow[],
): DashboardResponseBody {
  if (!skinTypeRow) {
    return {
      telegram_username: telegramUsername,
      onboarded: false,
      skin_type: null,
      sessions: [],
    };
  }
  return {
    telegram_username: telegramUsername,
    onboarded: true,
    skin_type: skinTypeRow.skin_type,
    sessions,
  };
}
