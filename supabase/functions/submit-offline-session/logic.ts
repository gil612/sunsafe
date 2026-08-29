// SunSafe — Offline Session Sync Edge Function: pure logic (no Deno.serve/
// Deno.env/fetch globals directly — Web Crypto (`crypto.subtle`) is used
// since it's a standard Web API available in both Deno and a JS test
// runner, not a Deno-only global).
//
// אותה מטרה כמו dashboard-data/logic.ts: קל לבדוק בלי תלות ברשת/סביבה.
// כפילות מכוונת מול dashboard-data/logic.ts (למשל CORS_HEADERS, jsonResponse)
// — כל Edge Function כאן self-contained, בלי ספריית קוד משותפת בין
// הפונקציות, אותה קונבנציה שכבר קיימת בפרויקט.
//
// ראה docs/2026-08-29-offline-session-miniapp-design.md לרציונל המלא.

export const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
};

export type ErrorCode =
  | "invalid_body"
  | "invalid_init_data"
  | "missing_username"
  | "not_onboarded"
  | "server_error";

const ERROR_STATUS: Record<ErrorCode, number> = {
  invalid_body: 400,
  invalid_init_data: 401,
  missing_username: 400,
  not_onboarded: 404,
  server_error: 500,
};

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

export function errorResponse(code: ErrorCode): Response {
  return jsonResponse({ error: code }, ERROR_STATUS[code]);
}

// -----------------------------------------------------------------------
// initData validation — אלגוריתם רשמי של Telegram Mini Apps:
// https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
//
// secret_key = HMAC_SHA256(key="WebAppData", data=bot_token)
// computed_hash = HEX(HMAC_SHA256(key=secret_key, data=data_check_string))
//
// החלטה מכוונת: *לא* בודקים כאן טריות auth_date (Telegram ממליצים לרוב
// על עד 24 שעות) — initData נתפס ברגע פתיחת ה-Mini App, שיכול להיות
// ימים לפני שהחיבור חוזר ומתאפשר סנכרון. בדיקת ה-hash הקריפטוגרפית
// עדיין חובה ותמיד מתבצעת. ראו docs/2026-08-29-offline-session-miniapp-design.md סעיף 5.
// -----------------------------------------------------------------------

export interface TelegramUser {
  id: number;
  username?: string;
  first_name?: string;
}

export interface InitDataValidation {
  valid: boolean;
  user?: TelegramUser;
}

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function buildDataCheckString(initData: string): { checkString: string; hash: string | null } {
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  params.delete("hash");

  const pairs: string[] = [];
  const keys = Array.from(params.keys()).sort();
  for (const key of keys) {
    pairs.push(`${key}=${params.get(key)}`);
  }
  return { checkString: pairs.join("\n"), hash };
}

export async function validateInitData(initData: string, botToken: string): Promise<InitDataValidation> {
  if (!initData || !botToken) return { valid: false };

  const { checkString, hash } = buildDataCheckString(initData);
  if (!hash) return { valid: false };

  const encoder = new TextEncoder();
  const webAppDataKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode("WebAppData"),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const secretKeyBytes = await crypto.subtle.sign("HMAC", webAppDataKey, encoder.encode(botToken));

  const secretKey = await crypto.subtle.importKey(
    "raw",
    secretKeyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", secretKey, encoder.encode(checkString));
  const computedHash = toHex(signature);

  if (computedHash !== hash) return { valid: false };

  const params = new URLSearchParams(initData);
  const userRaw = params.get("user");
  let user: TelegramUser | undefined;
  if (userRaw) {
    try { user = JSON.parse(userRaw); } catch { user = undefined; }
  }
  return { valid: true, user };
}

// -----------------------------------------------------------------------
// Exposure score — פורט מדויק של calculate_exposure_score ב-
// mcp_weather_server.py / bot_commands.py. כפילות מכוונת (TS מול
// Python) — אותה קונבנציה שכבר קיימת: logic.ts לא תלוי בקוד Python.
// -----------------------------------------------------------------------

export const SKIN_TYPE_FACTOR: Record<number, number> = {
  1: 0.5, 2: 0.75, 3: 1.0, 4: 1.5, 5: 2.5, 6: 4.0,
};

export function effectiveSpf(labeledSpf: number | null | undefined): number {
  if (!labeledSpf) return 1;
  return 1 + (labeledSpf - 1) * 0.4;
}

export function calculateExposureScore(
  uvIndex: number,
  durationMinutes: number,
  skinType: number,
  spf: number | null | undefined,
): number {
  const factor = SKIN_TYPE_FACTOR[skinType] ?? 1.0;
  const protection = effectiveSpf(spf);
  const safeMinutes = (200 / uvIndex) * factor * protection;
  return Math.round((durationMinutes / safeMinutes) * 100);
}

// -----------------------------------------------------------------------
// Request/response shapes
// -----------------------------------------------------------------------

export interface OfflineSessionInput {
  client_uuid: string;
  start_time: string;
  start_lat: number;
  start_lon: number;
  end_time: string;
  end_lat: number;
  end_lon: number;
  spf?: number | null;
}

export interface SubmitRequestBody {
  initData: string;
  sessions: OfflineSessionInput[];
}

export interface RejectedItem {
  client_uuid: string;
  reason: string;
}

/** ולידציה בסיסית לפני שממשיכים ל-I/O (UV/geocoding/DB) — זול ומהיר. */
export function validateSessionShape(s: OfflineSessionInput): string | null {
  if (!s || typeof s.client_uuid !== "string" || !s.client_uuid) return "חסר client_uuid";
  if (!s.start_time || !s.end_time) return "חסר start_time/end_time";
  const start = new Date(s.start_time).getTime();
  const end = new Date(s.end_time).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return "start_time/end_time לא תקינים";
  if (end <= start) return "end_time חייב להיות אחרי start_time";
  const oneDayMs = 24 * 60 * 60 * 1000;
  if (end - start > oneDayMs) return "session ארוך מדי (מעל 24 שעות) — כנראה טעות בנתונים";
  for (const [lat, lon] of [[s.start_lat, s.start_lon], [s.end_lat, s.end_lon]]) {
    if (typeof lat !== "number" || typeof lon !== "number" || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      return "קואורדינטות GPS לא תקינות";
    }
  }
  return null;
}

/** כמה ימים אחורה (past_days) צריך לבקש מ-Open-Meteo כדי לכסות start_time. */
export function pastDaysFor(startTimeIso: string, now: Date = new Date()): number | null {
  const start = new Date(startTimeIso);
  const diffDays = Math.ceil((now.getTime() - start.getTime()) / (24 * 60 * 60 * 1000)) + 1;
  if (diffDays < 0) return 0; // בעתיד (שעון לא מסונכרן) — נבקש את היום הנוכחי
  if (diffDays > 92) return null; // מעבר לטווח הנתמך
  return diffDays;
}

/** מוצא את ה-UV הקרוב ביותר לזמן נתון מתוך תשובת Open-Meteo hourly. */
export function nearestHourlyUv(
  hourlyTimes: string[],
  hourlyUv: number[],
  targetIso: string,
): number | null {
  if (!hourlyTimes || !hourlyUv || hourlyTimes.length === 0) return null;
  const target = new Date(targetIso).getTime();
  let bestIdx = -1;
  let bestDiff = Infinity;
  for (let i = 0; i < hourlyTimes.length; i++) {
    const t = new Date(hourlyTimes[i]).getTime();
    const diff = Math.abs(t - target);
    if (diff < bestDiff) { bestDiff = diff; bestIdx = i; }
  }
  if (bestIdx === -1) return null;
  const value = hourlyUv[bestIdx];
  return typeof value === "number" ? value : null;
}
