// SunSafe — logic.ts unit tests ל-submit-offline-session.
//
// logic.ts טהור (בלי Deno.serve/Deno.env/fetch — crypto.subtle הוא Web
// API סטנדרטי, זמין גם תחת Node), אז אפשר להריץ תחת Node 22+:
//   node --experimental-strip-types --test logic.test.ts
// או עם Deno:
//   deno test logic.test.ts
//
// בדיקת initData מחשבת hash *באופן עצמאי* דרך node:crypto (לא קוראת
// ל-validateInitData כדי ליצור את ה-hash ואז מוודאת שהיא מקבלת את מה
// שהיא עצמה יצרה — זה היה מעגלי) — כדי לוודא שהאלגוריתם תואם בפועל
// לספק הרשמי של Telegram Mini Apps.
// ראה docs/2026-08-29-offline-session-miniapp-design.md סעיף 5-6.

import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import {
  calculateExposureScore,
  errorResponse,
  nearestHourlyUv,
  pastDaysFor,
  validateInitData,
  validateSessionShape,
} from "./logic.ts";

function computeInitDataHash(fields: Record<string, string>, botToken: string): string {
  const dataCheckString = Object.keys(fields).sort().map((k) => `${k}=${fields[k]}`).join("\n");
  const secretKey = crypto.createHmac("sha256", "WebAppData").update(botToken).digest();
  return crypto.createHmac("sha256", secretKey).update(dataCheckString).digest("hex");
}

const BOT_TOKEN = "123456:ABC-DEF_test_token";

test("validateInitData: correctly-signed initData is accepted, user is parsed", async () => {
  const user = { id: 999888777, first_name: "Gil", username: "gil612" };
  const fields = { query_id: "AAHdF6IQ", user: JSON.stringify(user), auth_date: "1735000000" };
  const hash = computeInitDataHash(fields, BOT_TOKEN);
  const initData = new URLSearchParams({ ...fields, hash }).toString();

  const result = await validateInitData(initData, BOT_TOKEN);
  assert.equal(result.valid, true);
  assert.equal(result.user?.username, "gil612");
  assert.equal(result.user?.id, 999888777);
});

test("validateInitData: tampered field is rejected", async () => {
  const fields = { auth_date: "1735000000", query_id: "abc" };
  const hash = computeInitDataHash(fields, BOT_TOKEN);
  const tampered = new URLSearchParams({ auth_date: "1735000001", query_id: "abc", hash }).toString();

  const result = await validateInitData(tampered, BOT_TOKEN);
  assert.equal(result.valid, false);
});

test("validateInitData: wrong bot token is rejected", async () => {
  const fields = { auth_date: "1735000000" };
  const hash = computeInitDataHash(fields, BOT_TOKEN);
  const initData = new URLSearchParams({ ...fields, hash }).toString();

  const result = await validateInitData(initData, "a-different-token");
  assert.equal(result.valid, false);
});

test("validateInitData: missing hash is rejected", async () => {
  const result = await validateInitData("auth_date=1735000000", BOT_TOKEN);
  assert.equal(result.valid, false);
});

test("validateInitData: empty initData is rejected", async () => {
  const result = await validateInitData("", BOT_TOKEN);
  assert.equal(result.valid, false);
});

test("calculateExposureScore: matches calculate_exposure_score (Python) — with SPF", () => {
  // factor(skin=3)=1.0, protection=1+(30-1)*0.4=12.6, safe=(200/8)*1*12.6=315, score=round(30/315*100)=10
  assert.equal(calculateExposureScore(8, 30, 3, 30), 10);
});

test("calculateExposureScore: matches calculate_exposure_score (Python) — no SPF", () => {
  // protection=1, safe=(200/8)*1=25, score=round(30/25*100)=120
  assert.equal(calculateExposureScore(8, 30, 3, null), 120);
});

test("calculateExposureScore: uv_index=0 returns 0 instead of dividing by zero", () => {
  assert.equal(calculateExposureScore(0, 120, 3, null), 0);
});

test("calculateExposureScore: unknown skin_type falls back to factor 1.0", () => {
  assert.equal(calculateExposureScore(8, 25, 99, null), calculateExposureScore(8, 25, 3, null));
});

test("pastDaysFor: within 92-day window returns a small positive integer", () => {
  const now = new Date("2026-08-29T12:00:00Z");
  const threeDaysAgo = new Date(now.getTime() - 3 * 86400000).toISOString();
  const result = pastDaysFor(threeDaysAgo, now);
  assert.ok(result !== null && result >= 3 && result <= 5, `expected ~4, got ${result}`);
});

test("pastDaysFor: older than 92 days returns null (unsupported)", () => {
  const now = new Date("2026-08-29T12:00:00Z");
  const tooOld = new Date(now.getTime() - 100 * 86400000).toISOString();
  assert.equal(pastDaysFor(tooOld, now), null);
});

test("pastDaysFor: start_time slightly in the future (clock skew) still returns a usable value", () => {
  const now = new Date("2026-08-29T12:00:00Z");
  const future = new Date(now.getTime() + 3600000).toISOString();
  const result = pastDaysFor(future, now);
  assert.ok(result !== null && result >= 0 && result <= 2, `expected small value, got ${result}`);
});

test("nearestHourlyUv: picks the closest hour's value", () => {
  const times = ["2026-08-26T10:00:00Z", "2026-08-26T11:00:00Z", "2026-08-26T12:00:00Z"];
  const uvs = [3.1, 5.5, 7.2];
  assert.equal(nearestHourlyUv(times, uvs, "2026-08-26T11:20:00Z"), 5.5);
});

test("nearestHourlyUv: empty arrays return null", () => {
  assert.equal(nearestHourlyUv([], [], "2026-08-26T11:20:00Z"), null);
});

test("validateSessionShape: valid session passes", () => {
  const session = {
    client_uuid: "abc-123",
    start_time: "2026-08-29T06:00:00Z",
    start_lat: 32.7940, start_lon: 34.9896,
    end_time: "2026-08-29T07:00:00Z",
    end_lat: 32.8000, end_lon: 35.0000,
    spf: 30,
  };
  assert.equal(validateSessionShape(session), null);
});

test("validateSessionShape: end_time before start_time is rejected", () => {
  const session = {
    client_uuid: "abc-123",
    start_time: "2026-08-29T07:00:00Z",
    start_lat: 32.79, start_lon: 34.98,
    end_time: "2026-08-29T06:00:00Z",
    end_lat: 32.80, end_lon: 35.00,
    spf: null,
  };
  assert.equal(typeof validateSessionShape(session), "string");
});

test("validateSessionShape: out-of-range latitude is rejected", () => {
  const session = {
    client_uuid: "abc-123",
    start_time: "2026-08-29T06:00:00Z",
    start_lat: 999, start_lon: 34.98,
    end_time: "2026-08-29T07:00:00Z",
    end_lat: 32.80, end_lon: 35.00,
    spf: null,
  };
  assert.equal(typeof validateSessionShape(session), "string");
});

test("errorResponse: correct status per error code", async () => {
  const cases = [
    ["invalid_body", 400],
    ["invalid_init_data", 401],
    ["missing_username", 400],
    ["not_onboarded", 404],
    ["server_error", 500],
  ] as const;
  for (const [code, status] of cases) {
    const res = errorResponse(code);
    assert.equal(res.status, status);
    assert.deepEqual(await res.json(), { error: code });
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), "*");
  }
});
