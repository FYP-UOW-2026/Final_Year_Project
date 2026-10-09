/**
 * Offline smoke test.
 *
 * Boots the real Express app and exercises the paths that need no Firebase round trip:
 * the health check, the 404 handler, authentication rejection, and request validation.
 * Anything that reads or writes Firestore is out of scope here, since that needs a live
 * project. Run with: node tests/smoke.mjs
 *
 * A throwaway RSA key is generated so the Admin SDK accepts the credential format and
 * initialises without contacting Google. No real project is touched.
 */
import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";

const { privateKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
  publicKeyEncoding: { type: "spki", format: "pem" },
});

process.env.NODE_ENV = "test";
process.env.PORT = "0";
process.env.FIREBASE_PROJECT_ID = "smoke-test-project";
process.env.FIREBASE_CLIENT_EMAIL = "smoke@smoke-test-project.iam.gserviceaccount.com";
process.env.FIREBASE_PRIVATE_KEY = privateKey.replace(/\n/g, "\\n");
process.env.FIREBASE_WEB_API_KEY = "smoke-test-web-key";
process.env.GROQ_API_KEY = "";
process.env.FREE_HISTORY_LIMIT = "10";
// Set (even empty) so dotenv leaves them alone: mail settings in backend/.env must never
// let this offline test reach a real mail provider.
process.env.MAILGUN_API_KEY = "";
process.env.SMTP_HOST = "";

const { createApp } = await import("../src/app.js");
const { redact } = await import("../src/utils/redact.js");
const { renderScanReportHtml } = await import("../src/services/report.service.js");
const { compareScans } = await import("../src/services/scans.service.js");
const { mailgunMessage, studentVerificationEmail } = await import(
  "../src/services/email.service.js"
);
const { UNIVERSITIES } = await import("../src/data/universities.js");

const app = createApp();
const server = app.listen(0);
await new Promise((resolve) => server.once("listening", resolve));
const base = `http://127.0.0.1:${server.address().port}`;

let passed = 0;
const failures = [];

async function check(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (error) {
    failures.push({ name, error });
    console.log(`  FAIL ${name}\n       ${error.message}`);
  }
}

console.log("\nHTTP layer");

await check("GET /api/health returns ok and reports the AI layer as disabled", async () => {
  const res = await fetch(`${base}/api/health`);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.status, "ok");
  assert.equal(body.aiExplanations, "disabled");
});

await check("unknown route returns a 404 in the standard error shape", async () => {
  const res = await fetch(`${base}/api/does-not-exist`);
  assert.equal(res.status, 404);
  const body = await res.json();
  assert.equal(body.error.status, 404);
  assert.match(body.error.message, /No route for GET/);
});

await check("a protected route with no token returns 401", async () => {
  const res = await fetch(`${base}/api/users/me`);
  assert.equal(res.status, 401);
  const body = await res.json();
  assert.match(body.error.message, /Bearer/);
});

await check("a protected route with a malformed token returns 401", async () => {
  const res = await fetch(`${base}/api/scans`, {
    headers: { Authorization: "Bearer not-a-real-token" },
  });
  assert.equal(res.status, 401);
});

await check("login with an invalid body is rejected by validation, not by Firebase", async () => {
  const res = await fetch(`${base}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "not-an-email", password: "" }),
  });
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.equal(body.error.status, 400);
  assert.ok(Array.isArray(body.error.details), "expected a details array");
  const fields = body.error.details.map((d) => d.field);
  assert.ok(fields.includes("email"), "expected the email field to be flagged");
});

await check("registering with a short password is rejected with a clear message", async () => {
  const res = await fetch(`${base}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "someone@example.com", password: "short" }),
  });
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.match(JSON.stringify(body.error.details), /at least 8 characters/);
});

console.log("\nSecret redaction");

await check("redacts a token assignment but keeps its label", () => {
  const out = redact("token=abcdef1234567890abcdef and nothing else");
  assert.ok(!out.includes("abcdef1234567890abcdef"), "the secret survived redaction");
  assert.match(out, /token=\[REDACTED\]/);
});

await check("redacts a Google API key", () => {
  const out = redact("key AIzaSyA1234567890abcdefghijklmnopqrstuvw found in strings");
  assert.ok(!out.includes("AIzaSyA1234567890abcdefghijklmnopqrstuvw"));
  assert.match(out, /\[REDACTED\]/);
});

await check("redacts a JWT", () => {
  const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r";
  const out = redact(`Authorization: Bearer ${jwt}`);
  assert.ok(!out.includes(jwt), "the JWT survived redaction");
});

await check("leaves ordinary evidence text alone", () => {
  const text = "`am start -n com.example.app/.SecretActivity` -> Starting: Intent";
  assert.equal(redact(text), text);
});

console.log("\nScan comparison");

await check("matches findings by category and component, not by generated id", async () => {
  const baseline = {
    id: "a",
    counts: {},
    findings: [
      { id: "id-1", category: "exported-auth-bypass", component: "A", severity: "critical", evidence: "x", title: "t", source: "s" },
      { id: "id-2", category: "logcat-leak", component: null, severity: "high", evidence: "y", title: "t", source: "s" },
    ],
  };
  const current = {
    id: "b",
    counts: {},
    findings: [
      // Same finding, different generated id. Must count as unchanged, not as both
      // resolved and introduced.
      { id: "id-9", category: "exported-auth-bypass", component: "A", severity: "critical", evidence: "x", title: "t", source: "s" },
      { id: "id-8", category: "allow-backup", component: null, severity: "medium", evidence: "z", title: "t", source: "s" },
    ],
  };

  const result = await compareScans(baseline, current);
  assert.equal(result.summary.unchanged, 1, "the shared finding should be unchanged");
  assert.equal(result.summary.resolved, 1, "logcat-leak should be resolved");
  assert.equal(result.summary.introduced, 1, "allow-backup should be introduced");
});

console.log("\nReport rendering");

await check("renders a report and escapes markup in the evidence", () => {
  const html = renderScanReportHtml(
    {
      id: "scan-123",
      type: "device",
      counts: { critical: 1, high: 0, medium: 0, low: 0, info: 0 },
      authorisationConfirmed: true,
      target: { packageName: "com.example.app" },
      findings: [
        {
          category: "exported-auth-bypass",
          title: "Exported activity reachable without authentication",
          severity: "critical",
          owasp: ["M3", "M1"],
          evidence: "<script>alert('xss')</script>",
          source: "ipc_oracle",
          confidence: "confirmed",
        },
      ],
    },
    { owner: { email: "dev@example.com" } }
  );

  assert.match(html, /<!doctype html>/i);
  assert.match(html, /com\.example\.app/);
  assert.match(html, /CRITICAL/);
  assert.ok(!html.includes("<script>alert"), "evidence was not escaped");
  assert.match(html, /&lt;script&gt;/);
});

await check("says so plainly when a scan found nothing", () => {
  const html = renderScanReportHtml({ id: "s", type: "apk", counts: {}, findings: [] });
  assert.match(html, /No problems were found/);
});

console.log("\nStudent verification email");

await check("the code is in the subject, the text and the HTML", () => {
  const mail = studentVerificationEmail({
    code: "048213",
    expiresInMinutes: 15,
    universityName: "National University of Singapore (NUS)",
  });
  assert.match(mail.subject, /^048213 /);
  assert.match(mail.text, /048213/);
  // The HTML puts a hair space between the digits for readability.
  assert.ok(mail.html.replaceAll("&#8202;", "").includes("048213"));
  assert.match(mail.text, /National University of Singapore/);
  assert.match(mail.text, /15 minutes/);
});

await check("markup in a university name is escaped in the HTML", () => {
  const mail = studentVerificationEmail({
    code: "111111",
    expiresInMinutes: 15,
    universityName: "<script>alert(1)</script>",
  });
  assert.ok(!mail.html.includes("<script>"));
  assert.match(mail.html, /&lt;script&gt;/);
});

await check("the Mailgun message carries both bodies and turns tracking off", () => {
  const msg = mailgunMessage({
    from: "BioAudit <postmaster@mg.example.com>",
    to: "student@u.nus.edu",
    subject: "048213 is your code",
    text: "plain",
    html: "<p>html</p>",
  });
  assert.deepEqual(msg.to, ["student@u.nus.edu"]);
  assert.equal(msg.from, "BioAudit <postmaster@mg.example.com>");
  assert.equal(msg.text, "plain");
  assert.equal(msg.html, "<p>html</p>");
  assert.equal(msg["o:tracking"], "no");
});

console.log("\nUniversity list");

await check("every university has a unique id and well-formed domains", () => {
  const ids = new Set();
  for (const uni of UNIVERSITIES) {
    assert.ok(!ids.has(uni.id), `duplicate id ${uni.id}`);
    ids.add(uni.id);
    assert.ok(uni.domains.length > 0, `${uni.id} has no domains`);
    for (const d of uni.domains) {
      assert.match(d, /^[a-z0-9-]+(\.[a-z0-9-]+)+$/, `${uni.id}: bad domain ${d}`);
    }
  }
});

await check("Singapore's universities and private institutions are listed", () => {
  for (const id of ["nus", "ntu", "smu", "sutd", "sit", "suss", "sim", "sim-uow", "sim-uol",
    "sim-rmit", "kaplan-sg", "psb"]) {
    assert.ok(UNIVERSITIES.some((u) => u.id === id), `missing ${id}`);
  }
});

await check("every current SIM Global Education partner is listed with the SIM domain", () => {
  const partners = ["sim-uow", "sim-uol", "sim-rmit", "sim-ub", "sim-monash", "sim-usyd",
    "sim-uob", "sim-cardiff", "sim-stirling", "sim-warwick", "sim-ualberta", "sim-gem"];
  for (const id of partners) {
    const uni = UNIVERSITIES.find((u) => u.id === id);
    assert.ok(uni, `missing ${id}`);
    assert.ok(uni.domains.includes("sim.edu.sg"), `${id} should accept @mymail.sim.edu.sg`);
  }
});

await check("SIM partner students may use either their SIM or partner address", () => {
  const simUow = UNIVERSITIES.find((u) => u.id === "sim-uow");
  assert.ok(simUow.domains.includes("sim.edu.sg"));
  assert.ok(simUow.domains.includes("uowmail.edu.au"));
});

server.close();

console.log(`\n${passed} passed, ${failures.length} failed.`);
if (failures.length > 0) {
  for (const { name, error } of failures) {
    console.error(`\n${name}:\n${error.stack}`);
  }
  process.exitCode = 1;
}
