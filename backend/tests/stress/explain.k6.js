/**
 * k6 stress test for POST /api/scans/:scanId/findings/:findingIndex/explain -- the AI
 * explanation endpoint, the one call in this codebase that goes through aiGraph.service.js
 * (model router, circuit breaker, repair loop).
 *
 * Needs real auth + real data: the endpoint requires a signed-in Firebase ID token and an
 * existing scan owned by that account. Get a token via `python -m bioaudit login` then
 * reading the saved session file, or the backend's own /api/auth/login in a pinch. Get a
 * scanId/findingIndex from any scan that already has findings (scan-apk a sample APK first
 * if you have none).
 *
 * Usage:
 *   BASE_URL=http://127.0.0.1:4000 AUTH_TOKEN=... SCAN_ID=... FINDING_INDEX=0 \
 *     k6 run tests/stress/explain.k6.js
 *
 * Stage names match the load table in the AI generation system plan (smoke/normal/high/
 * stress/recovery). `force: true` in the body bypasses the stored-explanation cache so
 * every request actually reaches the AI layer instead of being served from Firestore.
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:4000";
const AUTH_TOKEN = __ENV.AUTH_TOKEN;
const SCAN_ID = __ENV.SCAN_ID;
const FINDING_INDEX = __ENV.FINDING_INDEX || "0";
const STAGE = __ENV.STAGE || "smoke"; // smoke | normal | high | stress | recovery

if (!AUTH_TOKEN || !SCAN_ID) {
  throw new Error("AUTH_TOKEN and SCAN_ID env vars are required -- see file header for how to get them.");
}

const providerFallbackRate = new Rate("provider_fallback_rate");
const validationFailureRate = new Rate("validation_failure_rate");
const timeoutCount = new Counter("timeout_count");
const aiLatency = new Trend("ai_latency_ms", true);

const STAGES = {
  smoke: [{ duration: "2m", target: 2 }],
  normal: [{ duration: "10m", target: 10 }],
  high: [{ duration: "15m", target: 25 }],
  // Ramps until failure shows up in the results (error rate, timeouts) -- read the summary,
  // don't expect a pass/fail here.
  stress: [
    { duration: "5m", target: 50 },
    { duration: "10m", target: 100 },
    { duration: "5m", target: 150 },
  ],
  recovery: [
    { duration: "1m", target: 50 }, // the preceding stress load
    { duration: "10m", target: 10 }, // back to normal -- watch how fast latency recovers
  ],
};

export const options = {
  scenarios: {
    [STAGE]: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: STAGES[STAGE],
      gracefulRampDown: "30s",
    },
  },
  thresholds: {
    // Informational, not a hard gate -- stress/high load is expected to breach these.
    http_req_duration: ["p(95)<30000"],
  },
};

export default function () {
  const url = `${BASE_URL}/api/scans/${SCAN_ID}/findings/${FINDING_INDEX}/explain`;
  const started = Date.now();
  const res = http.post(url, JSON.stringify({ force: true }), {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AUTH_TOKEN}`,
    },
    timeout: "60s",
  });
  aiLatency.add(Date.now() - started);

  const ok = check(res, {
    "status is 200": (r) => r.status === 200,
    "has explanation body": (r) => {
      try {
        return typeof JSON.parse(r.body)?.explanation === "string";
      } catch {
        return false;
      }
    },
  });

  timeoutCount.add(res.status === 0 || res.error_code === 1050 ? 1 : 0);
  validationFailureRate.add(res.status === 500 ? 1 : 0);
  providerFallbackRate.add(!ok ? 1 : 0);

  sleep(1);
}
