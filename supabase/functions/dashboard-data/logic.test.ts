// SunSafe — logic.ts unit tests.
//
// logic.ts הוא טהור (בלי Deno.serve/Deno.env/fetch), אז אפשר להריץ את
// הבדיקות האלה גם מחוץ ל-Deno — למשל תחת Node 22+:
//   node --experimental-strip-types --test logic.test.ts
// או, אם יש Deno מותקן:
//   deno test logic.test.ts
//
// מכסה את "בדיקות" 1-6 ב-docs/2026-08-25-magic-link-edge-function-design.md
// (הבדיקה #7, ה-OPTIONS preflight, היא ב-index.ts עצמו — לא נבדקת כאן).

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildDashboardBody,
  errorResponse,
  isTokenExpired,
  jsonResponse,
} from "./logic.ts";

test("isTokenExpired: future expiry -> not expired", () => {
  const now = new Date("2026-08-25T12:00:00Z");
  assert.equal(isTokenExpired("2026-08-26T12:00:00Z", now), false);
});

test("isTokenExpired: past expiry -> expired", () => {
  const now = new Date("2026-08-25T12:00:00Z");
  assert.equal(isTokenExpired("2026-08-24T12:00:00Z", now), true);
});

test("isTokenExpired: exactly at expiry -> treated as expired", () => {
  const now = new Date("2026-08-25T12:00:00Z");
  assert.equal(isTokenExpired("2026-08-25T12:00:00Z", now), true);
});

test("buildDashboardBody: onboarded user with sessions", () => {
  const sessions = [
    {
      id: 12,
      city: "Tel Aviv",
      country: "Israel",
      start_time: "2026-08-25T06:00:00+00:00",
      end_time: "2026-08-25T06:29:00+00:00",
      uv_index: 7.24,
      spf: null,
      exposure_score: 105,
    },
  ];
  const body = buildDashboardBody("gil612", { skin_type: 3 }, sessions);
  assert.deepEqual(body, {
    telegram_username: "gil612",
    onboarded: true,
    skin_type: 3,
    sessions,
  });
});

test("buildDashboardBody: no users row -> onboarded false, empty sessions", () => {
  // sessions לא אמורים להתקיים בכלל למשתמש בלי users row (start_session
  // חוסם את זה), אבל בודקים שגם אם הם היו קיימים, הפלט מתעלם מהם.
  const body = buildDashboardBody("new_user", undefined, [
    { id: 1, city: "x", country: null, start_time: "t", end_time: "t2", uv_index: 1, spf: null, exposure_score: 1 },
  ]);
  assert.deepEqual(body, {
    telegram_username: "new_user",
    onboarded: false,
    skin_type: null,
    sessions: [],
  });
});

test("errorResponse: correct status per error code", async () => {
  const cases = [
    ["missing_token", 400],
    ["invalid_token", 404],
    ["expired_token", 410],
    ["server_error", 500],
  ] as const;
  for (const [code, status] of cases) {
    const res = errorResponse(code);
    assert.equal(res.status, status);
    assert.deepEqual(await res.json(), { error: code });
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), "*");
  }
});

test("jsonResponse: sets content-type and CORS headers", async () => {
  const res = jsonResponse({ ok: true }, 200);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Content-Type"), "application/json");
  assert.equal(res.headers.get("Access-Control-Allow-Methods"), "GET, OPTIONS");
  assert.deepEqual(await res.json(), { ok: true });
});
