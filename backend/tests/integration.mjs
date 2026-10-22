/**
 * Integration tests against the Firebase emulator suite.
 *
 * These cover everything the offline smoke test cannot: real registration, sign-in,
 * Firestore reads and writes, student and team limits, and the whole organisation workflow. Nothing
 * here touches a live Firebase project.
 *
 * Run with:  npm run test:integration
 * That script starts the emulators, runs this file, and shuts them down again.
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";

const EMULATOR_PROJECT = "bioaudit-test";

// Point the Admin SDK and the sign-in REST calls at the emulators. These have to be set
// before any application module is imported, since both are read at module load.
process.env.NODE_ENV = "test";
process.env.GCLOUD_PROJECT = EMULATOR_PROJECT;
process.env.FIREBASE_PROJECT_ID = EMULATOR_PROJECT;
process.env.FIRESTORE_EMULATOR_HOST = process.env.FIRESTORE_EMULATOR_HOST || "127.0.0.1:8080";
process.env.FIREBASE_AUTH_EMULATOR_HOST = process.env.FIREBASE_AUTH_EMULATOR_HOST || "127.0.0.1:9099";
// No service account is set. The emulators accept any caller, and config/firebase.js
// skips the credential entirely when the emulator variables above are present.
process.env.FIREBASE_WEB_API_KEY = "emulator-key";
process.env.GROQ_API_KEY = "";
process.env.FREE_HISTORY_LIMIT = "3"; // small, so retention is quick to prove
// Every test registers and signs in from the same address, so the production limits
// would start refusing partway through the run.
process.env.AUTH_RATE_LIMIT_PER_15MIN = "1000";
process.env.API_RATE_LIMIT_PER_MINUTE = "100000";
// Lets the tests sign Mailgun bounce reports the way Mailgun would.
const WEBHOOK_KEY = "integration-webhook-signing-key";
process.env.MAILGUN_WEBHOOK_SIGNING_KEY = WEBHOOK_KEY;

const { createApp } = await import("../src/app.js");
const { db, auth } = await import("../src/config/firebase.js");
const { hashCode, signupCodeId } = await import("../src/services/studentVerification.service.js");
const { COLLECTIONS } = await import("../src/constants/index.js");

const app = createApp();
const server = app.listen(0);
await new Promise((resolve) => server.once("listening", resolve));
const BASE = `http://127.0.0.1:${server.address().port}/api`;

// --- test harness ----------------------------------------------------------

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

function group(title) {
  console.log(`\n${title}`);
}

/** Thin request helper that returns status and parsed body together. */
async function call(method, path, { token, body } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await res.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = text;
  }
  return { status: res.status, body: parsed };
}

/** Wipe the emulator between runs so tests do not depend on leftover state. */
async function clearEmulator() {
  const fsHost = process.env.FIRESTORE_EMULATOR_HOST;
  const authHost = process.env.FIREBASE_AUTH_EMULATOR_HOST;
  await fetch(
    `http://${fsHost}/emulator/v1/projects/${EMULATOR_PROJECT}/databases/(default)/documents`,
    { method: "DELETE" }
  );
  await fetch(`http://${authHost}/emulator/v1/projects/${EMULATOR_PROJECT}/accounts`, {
    method: "DELETE",
  });
}

await clearEmulator();

const unique = () => Math.random().toString(36).slice(2, 8);

/**
 * The real code only ever leaves the server by email, so the tests swap a known code's
 * hash into the stored record, which is what the emailed code would have matched.
 */
const KNOWN_CODE = "246810";

async function plantSignupCode(email) {
  await db
    .collection("signupVerifications")
    .doc(signupCodeId(email))
    .update({ codeHash: hashCode(email, KNOWN_CODE) });
}

/** Register a student the way a person does: ask for a code, then sign up with it. */
async function registerStudent(body) {
  const sent = await call("POST", "/auth/register/send-code", {
    body: { email: body.email, universityId: body.universityId },
  });
  assert.equal(sent.status, 200, JSON.stringify(sent.body));
  await plantSignupCode(body.email);
  return call("POST", "/auth/register", { body: { ...body, code: KNOWN_CODE } });
}

async function accountExists(email) {
  return auth.getUserByEmail(email).then(
    () => true,
    () => false
  );
}

// --- registration and sign-in ---------------------------------------------

group("Registration and sign-in");

// A university address, since personal accounts are for verified students.
const userEmail = `dev-${unique()}@example.ac.uk`;
const userPassword = "correct-horse-battery";
let userToken;
let userUid;

await check("a student cannot register without a code for their email", async () => {
  const { status, body } = await call("POST", "/auth/register", {
    body: { email: userEmail, password: userPassword },
  });
  assert.equal(status, 400);
  assert.match(body.error.message, /verify your university email/i);
  assert.equal(await accountExists(userEmail), false, "no account should have been made");
});

await check("a sign-up code is only sent to a university address", async () => {
  const email = `hobbyist-${unique()}@example.com`;
  const sent = await call("POST", "/auth/register/send-code", { body: { email } });
  assert.equal(sent.status, 400);
  assert.match(sent.body.error.message, /personal accounts are for students/i);

  const registered = await call("POST", "/auth/register", {
    body: { email, password: "hobbyist-password-1", code: KNOWN_CODE },
  });
  assert.equal(registered.status, 400);
  assert.equal(await accountExists(email), false);
});

await check("a sign-up code is only sent to an address at the chosen university", async () => {
  const { status, body } = await call("POST", "/auth/register/send-code", {
    body: { email: `dev-${unique()}@example.ac.uk`, universityId: "oxford" },
  });
  assert.equal(status, 400);
  assert.match(body.error.message, /@ox\.ac\.uk/);
});

await check("registering with the emailed code creates a verified account and a session", async () => {
  const { status, body } = await registerStudent({
    email: userEmail,
    password: userPassword,
    displayName: "Test Dev",
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.equal(body.user.email, userEmail);
  assert.equal(body.user.tier, "student");
  assert.equal(body.user.role, "member");
  assert.ok(body.session.idToken, "expected an idToken");
  assert.equal(body.user.studentVerification.status, "verified");
  assert.equal(body.user.studentVerification.required, true);
  userToken = body.session.idToken;
  userUid = body.user.id;

  const record = await auth.getUser(userUid);
  assert.equal(record.emailVerified, true);
  const code = await db.collection("signupVerifications").doc(signupCodeId(userEmail)).get();
  assert.ok(!code.exists, "a used sign-up code should be deleted");
});

await check("the chosen university is kept on the new account", async () => {
  const email = `dev-${unique()}@ox.ac.uk`;
  const { status, body } = await registerStudent({
    email,
    password: "oxford-password-1",
    universityId: "oxford",
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.equal(body.user.studentVerification.university.id, "oxford");
  assert.equal(body.user.studentVerification.status, "verified");
});

await check("the same email cannot register twice", async () => {
  const sent = await call("POST", "/auth/register/send-code", { body: { email: userEmail } });
  assert.equal(sent.status, 409, "no code should be sent for an address with an account");

  const { status, body } = await call("POST", "/auth/register", {
    body: { email: userEmail, password: userPassword, code: KNOWN_CODE },
  });
  assert.equal(status, 409);
  assert.match(body.error.message, /already exists/i);
});

await check("a code sent to one address cannot register another", async () => {
  const codeHolder = `holder-${unique()}@example.ac.uk`;
  const other = `other-${unique()}@example.ac.uk`;
  await call("POST", "/auth/register/send-code", { body: { email: codeHolder } });
  await plantSignupCode(codeHolder);

  const { status } = await call("POST", "/auth/register", {
    body: { email: other, password: "other-password-1", code: KNOWN_CODE },
  });
  assert.equal(status, 400);
  assert.equal(await accountExists(other), false);
});

await check("a sign-up code stops working after too many wrong guesses", async () => {
  const email = `guesser-${unique()}@uni.ac.uk`;
  const password = "guesser-password-1";
  await call("POST", "/auth/register/send-code", { body: { email } });
  await plantSignupCode(email);

  const wrong = await call("POST", "/auth/register", { body: { email, password, code: "000000" } });
  assert.equal(wrong.status, 400);
  const stored = await db.collection("signupVerifications").doc(signupCodeId(email)).get();
  assert.equal(stored.data().attempts, 1, "a wrong guess should be counted");

  for (let i = 0; i < 4; i += 1) {
    await call("POST", "/auth/register", { body: { email, password, code: "111111" } });
  }
  const { status } = await call("POST", "/auth/register", {
    body: { email, password, code: KNOWN_CODE },
  });
  assert.equal(status, 429, "the right code must not work once the attempts are used up");
  assert.equal(await accountExists(email), false);
});

await check("asking for another sign-up code straight away is refused", async () => {
  const email = `impatient-${unique()}@example.ac.uk`;
  const first = await call("POST", "/auth/register/send-code", { body: { email } });
  assert.equal(first.status, 200, JSON.stringify(first.body));
  const again = await call("POST", "/auth/register/send-code", { body: { email } });
  assert.equal(again.status, 429);
});

// --- bounced sign-up codes ----------------------------------------------------

group("Bounced sign-up codes");

/** POST a Mailgun webhook signed the way Mailgun signs one. */
async function mailgunWebhook(eventData, { key = WEBHOOK_KEY } = {}) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const token = crypto.randomBytes(25).toString("hex");
  const signature = crypto.createHmac("sha256", key).update(timestamp + token).digest("hex");
  return call("POST", "/webhooks/mailgun", {
    body: { signature: { timestamp, token, signature }, "event-data": eventData },
  });
}

/** Mailgun's report that a message to `recipient` permanently failed. */
function permanentFailure(recipient, variables) {
  return {
    event: "failed",
    severity: "permanent",
    reason: "bounce",
    recipient,
    "delivery-status": { code: 550, description: "5.1.1 Recipient address rejected" },
    "user-variables": variables,
  };
}

/** Ask for a sign-up code, returning what a client needs to follow it up. */
async function sendSignupCodeTo(email) {
  const sent = await call("POST", "/auth/register/send-code", { body: { email } });
  assert.equal(sent.status, 200, JSON.stringify(sent.body));
  assert.match(sent.body.sendId, /^[a-f0-9]{32}$/);
  return { sendId: sent.body.sendId, codeDoc: `signupVerifications/${signupCodeId(email)}` };
}

const codeStatus = (email, sendId) =>
  call("POST", "/auth/register/code-status", { body: { email, sendId } });

await check("a fresh sign-up code reads as sent", async () => {
  const email = `fresh-${unique()}@example.ac.uk`;
  const { sendId } = await sendSignupCodeTo(email);
  const { status, body } = await codeStatus(email, sendId);
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.status, "sent");
});

await check("Mailgun's report of a permanent failure marks the code bounced", async () => {
  const email = `typo-${unique()}@example.ac.uk`;
  const { sendId, codeDoc } = await sendSignupCodeTo(email);

  const report = await mailgunWebhook(permanentFailure(email, { codeDoc, sendId }));
  assert.equal(report.status, 200, JSON.stringify(report.body));

  const { body } = await codeStatus(email, sendId);
  assert.equal(body.status, "bounced");
  assert.match(body.message, /does not exist/);
  assert.ok(body.message.includes(email), "the message should name the address");
});

await check("a report with a bad signature is refused and changes nothing", async () => {
  const email = `forged-${unique()}@example.ac.uk`;
  const { sendId, codeDoc } = await sendSignupCodeTo(email);

  const report = await mailgunWebhook(permanentFailure(email, { codeDoc, sendId }), {
    key: "not-the-signing-key",
  });
  assert.equal(report.status, 401);
  assert.equal((await codeStatus(email, sendId)).body.status, "sent");
});

await check("a report about an earlier send does not mark a newer code", async () => {
  const email = `resent-${unique()}@example.ac.uk`;
  const { sendId, codeDoc } = await sendSignupCodeTo(email);
  // Stand in for a second send, which replaces the code and its sendId.
  const newerSendId = crypto.randomBytes(16).toString("hex");
  await db.doc(codeDoc).update({ sendId: newerSendId });

  await mailgunWebhook(permanentFailure(email, { codeDoc, sendId }));
  assert.equal((await codeStatus(email, newerSendId)).body.status, "sent");
});

await check("temporary failures are left alone, since Mailgun retries them", async () => {
  const email = `greylisted-${unique()}@example.ac.uk`;
  const { sendId, codeDoc } = await sendSignupCodeTo(email);
  const event = { ...permanentFailure(email, { codeDoc, sendId }), severity: "temporary" };

  const report = await mailgunWebhook(event);
  assert.equal(report.status, 200);
  assert.equal((await codeStatus(email, sendId)).body.status, "sent");
});

await check("a report cannot be aimed at anything but a code", async () => {
  const report = await mailgunWebhook(
    permanentFailure(userEmail, { codeDoc: `users/${userUid}`, sendId: "a".repeat(32) })
  );
  assert.equal(report.status, 200);
  const profile = (await db.collection("users").doc(userUid).get()).data();
  assert.equal(profile.bounced, undefined, "the user document must be untouched");
});

await check("asking about a code needs the sendId it was sent with", async () => {
  const email = `nosy-${unique()}@example.ac.uk`;
  await sendSignupCodeTo(email);
  const { status } = await codeStatus(email, "0".repeat(32));
  assert.equal(status, 404);
});

await check("signing in with the right password works", async () => {
  const { status, body } = await call("POST", "/auth/login", {
    body: { email: userEmail, password: userPassword },
  });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.user.id, userUid);
  userToken = body.session.idToken;
});

await check("signing in with the wrong password is refused", async () => {
  const { status } = await call("POST", "/auth/login", {
    body: { email: userEmail, password: "not-the-password" },
  });
  assert.equal(status, 401);
});

await check("an unknown email gives the same answer as a wrong password", async () => {
  // If these differed, the API would be a response oracle: an attacker could discover
  // which accounts exist. That is the weakness this project detects in other apps.
  const wrongPassword = await call("POST", "/auth/login", {
    body: { email: userEmail, password: "not-the-password" },
  });
  const unknownEmail = await call("POST", "/auth/login", {
    body: { email: `nobody-${unique()}@example.com`, password: "not-the-password" },
  });
  assert.equal(wrongPassword.status, unknownEmail.status);
  assert.equal(wrongPassword.body.error.message, unknownEmail.body.error.message);
});

group("Own account");

await check("the profile can be read back", async () => {
  const { status, body } = await call("GET", "/users/me", { token: userToken });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.user.email, userEmail);
  assert.equal(body.user.displayName, "Test Dev");
});

await check("the display name can be updated", async () => {
  const { status, body } = await call("PATCH", "/users/me", {
    token: userToken,
    body: { displayName: "Renamed Dev" },
  });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.user.displayName, "Renamed Dev");
});

await check("changing the password needs the current one", async () => {
  const { status } = await call("POST", "/users/me/password", {
    token: userToken,
    body: { currentPassword: "wrong", newPassword: "a-brand-new-password" },
  });
  assert.equal(status, 401, "a wrong current password should be refused");
});

// --- student verification -------------------------------------------------

group("Student verification after changing email");

// A student is verified when their account is created, and changing the account's email
// lapses that. It is the one way an existing account comes to need a code.

async function plantKnownCode(uid) {
  await db.collection("studentVerifications").doc(uid).update({ codeHash: hashCode(uid, KNOWN_CODE) });
}

/** A verified student who has just moved to `newEmail`, with the session to act as them. */
async function lapsedStudent(newEmail) {
  const password = "lapsed-password-1";
  const reg = await registerStudent({ email: `lapsed-${unique()}@example.ac.uk`, password });
  assert.equal(reg.status, 201, JSON.stringify(reg.body));
  const token = reg.body.session.idToken;
  const moved = await call("POST", "/users/me/email", {
    token,
    body: { newEmail, currentPassword: password },
  });
  assert.equal(moved.status, 200, JSON.stringify(moved.body));
  // Firebase ends every session when an account's email changes, so sign in again.
  const relogin = await call("POST", "/auth/login", { body: { email: newEmail, password } });
  assert.equal(relogin.status, 200, JSON.stringify(relogin.body));
  return { token: relogin.body.session.idToken, uid: reg.body.user.id, user: moved.body.user };
}

const minimalScan = {
  type: "device",
  authorisationConfirmed: true,
  target: { packageName: "com.example.app", deviceSerial: "emulator-1" },
  findings: [],
};

let lapsedToken;
let lapsedUid;

await check("moving to a new university address sends a code to it", async () => {
  const lapsed = await lapsedStudent(`moved-${unique()}@example.ac.uk`);
  assert.equal(lapsed.user.studentVerification.status, "pending");
  lapsedToken = lapsed.token;
  lapsedUid = lapsed.uid;
});

await check("an unverified student cannot save a scan", async () => {
  const { status, body } = await call("POST", "/scans", { token: lapsedToken, body: minimalScan });
  assert.equal(status, 403);
  assert.match(body.error.message, /verify your university email/i);
});

await check("the code is never stored on the client-readable user document", async () => {
  const profile = (await db.collection("users").doc(lapsedUid).get()).data();
  assert.ok(!JSON.stringify(profile).includes("codeHash"));
});

await check("asking for another code straight away is refused", async () => {
  const { status } = await call("POST", "/users/me/student-verification/send", {
    token: lapsedToken,
  });
  assert.equal(status, 429);
});

await check("a wrong code is refused and counted", async () => {
  await plantKnownCode(lapsedUid);
  const { status } = await call("POST", "/users/me/student-verification/verify", {
    token: lapsedToken,
    body: { code: "000000" },
  });
  assert.equal(status, 400);
  const stored = await db.collection("studentVerifications").doc(lapsedUid).get();
  assert.equal(stored.data().attempts, 1);
});

await check("the right code verifies the account and unlocks scanning", async () => {
  const { status, body } = await call("POST", "/users/me/student-verification/verify", {
    token: lapsedToken,
    body: { code: KNOWN_CODE },
  });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.user.studentVerification.status, "verified");
  const stored = await db.collection("studentVerifications").doc(lapsedUid).get();
  assert.ok(!stored.exists, "a used code should be deleted");
  const record = await auth.getUser(lapsedUid);
  assert.equal(record.emailVerified, true);
});

await check("a code stops working after too many wrong guesses", async () => {
  const { token, uid } = await lapsedStudent(`guesser-${unique()}@uni.ac.uk`);
  await plantKnownCode(uid);
  for (let i = 0; i < 5; i += 1) {
    await call("POST", "/users/me/student-verification/verify", { token, body: { code: "111111" } });
  }
  const { status } = await call("POST", "/users/me/student-verification/verify", {
    token,
    body: { code: KNOWN_CODE },
  });
  assert.equal(status, 429, "the right code must not work once the attempts are used up");
});

await check("a student who moves to a non-university address is told it is for students", async () => {
  const { token, user } = await lapsedStudent(`hobbyist-${unique()}@example.com`);
  assert.equal(user.studentVerification.eligible, false);

  const send = await call("POST", "/users/me/student-verification/send", { token });
  assert.equal(send.status, 400);

  const scan = await call("POST", "/scans", { token, body: minimalScan });
  assert.equal(scan.status, 403);
  assert.match(scan.body.error.message, /personal accounts are for students/i);
});

// --- scans and history ----------------------------------------------------

group("Scans and history");

function scanPayload(pkg, findings = []) {
  return {
    type: "device",
    authorisationConfirmed: true,
    target: { packageName: pkg, deviceSerial: "emulator-1" },
    findings,
  };
}

const sampleFinding = {
  category: "exported-auth-bypass",
  title: "Exported activity reachable without authentication",
  severity: "critical",
  owasp: ["M3", "M1"],
  evidence: "`am start -n com.example.app/.SecretActivity` -> Starting: Intent{...}",
  source: "ipc_oracle",
  confidence: "confirmed",
  component: "com.example.app.SecretActivity",
};

let firstScanId;

await check("a scan is stored with its severity counts worked out", async () => {
  const { status, body } = await call("POST", "/scans", {
    token: userToken,
    body: scanPayload("com.example.app", [
      sampleFinding,
      { ...sampleFinding, category: "logcat-leak", severity: "high", component: null },
    ]),
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.equal(body.scan.findingCount, 2);
  assert.equal(body.scan.counts.critical, 1);
  assert.equal(body.scan.counts.high, 1);
  firstScanId = body.scan.id;
});

await check("a device assessment without an authorisation confirmation is refused", async () => {
  const { status, body } = await call("POST", "/scans", {
    token: userToken,
    body: { ...scanPayload("com.example.app"), authorisationConfirmed: false },
  });
  assert.equal(status, 400);
  assert.match(body.error.message, /authorised/i);
});

await check("history comes back newest first", async () => {
  const { status, body } = await call("GET", "/scans", { token: userToken });
  assert.equal(status, 200, JSON.stringify(body));
  assert.ok(body.scans.length >= 1);
  assert.equal(body.tier, "student");
  assert.equal(body.historyLimit, 3, "the student limit should be reported");
});

await check("a stored scan can be fetched with its findings", async () => {
  const { status, body } = await call("GET", `/scans/${firstScanId}`, { token: userToken });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.scan.findings.length, 2);
  assert.equal(body.scan.findings[0].category, "exported-auth-bypass");
});

await check("a student account trims the oldest scan once past its limit", async () => {
  // The limit is 3 for this run, and one scan already exists. Saving three more should
  // push the total over and drop the oldest.
  for (let i = 0; i < 3; i += 1) {
    const { status } = await call("POST", "/scans", {
      token: userToken,
      body: scanPayload(`com.example.app${i}`, [sampleFinding]),
    });
    assert.equal(status, 201);
  }

  const { body } = await call("GET", "/scans", { token: userToken });
  assert.equal(body.scans.length, 3, "a student account should keep only 3 scans");

  const gone = await call("GET", `/scans/${firstScanId}`, { token: userToken });
  assert.equal(gone.status, 404, "the oldest scan should have been removed");
});

await check("comparing scans is refused on a student account", async () => {
  const { body: list } = await call("GET", "/scans", { token: userToken });
  const [a, b] = list.scans;
  const { status } = await call("GET", `/scans/compare/results?baseline=${b.id}&current=${a.id}`, {
    token: userToken,
  });
  assert.equal(status, 403, "a team-only feature should be refused to a student");
});

await check("exporting a report is refused on a student account", async () => {
  const { body: list } = await call("GET", "/scans", { token: userToken });
  const { status } = await call("GET", `/scans/${list.scans[0].id}/report`, { token: userToken });
  assert.equal(status, 403);
});

await check("there is no subscription to buy or cancel", async () => {
  const upgrade = await call("POST", "/subscription/upgrade", { token: userToken, body: {} });
  assert.equal(upgrade.status, 404, "the upgrade endpoint should be gone");
});

// --- organisations and admin ---------------------------------------------

group("Organisations and admin");

const adminEmail = `admin-${unique()}@example.com`;
const adminPassword = "admin-password-1234";
let adminToken;
let orgId;

await check("registering an admin also creates their organisation", async () => {
  const { status, body } = await call("POST", "/auth/register-admin", {
    body: {
      email: adminEmail,
      password: adminPassword,
      displayName: "Org Owner",
      organisationName: "Test Security Team",
    },
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.equal(body.user.role, "admin");
  assert.equal(body.organisation.name, "Test Security Team");
  assert.equal(body.user.organisationId, body.organisation.id);
  adminToken = body.session.idToken;
  orgId = body.organisation.id;
});

let inviteToken;

await check("an admin can invite a team member and gets a one-time token", async () => {
  const { status, body } = await call("POST", `/organisations/${orgId}/invitations`, {
    token: adminToken,
    body: { email: "invitee@example.com", role: "member" },
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.ok(body.token, "expected an invitation token");
  inviteToken = body.token;
});

await check("the invitation token is stored hashed, not in the clear", async () => {
  const snap = await db.collection("invitations").get();
  assert.ok(snap.size >= 1);
  const doc = snap.docs[0].data();
  assert.ok(doc.tokenHash, "expected a tokenHash field");
  assert.equal(doc.token, undefined, "the raw token must not be stored");
  assert.notEqual(doc.tokenHash, inviteToken, "the stored value must not equal the token");
});

await check("a duplicate pending invitation is refused", async () => {
  const { status } = await call("POST", `/organisations/${orgId}/invitations`, {
    token: adminToken,
    body: { email: "invitee@example.com", role: "member" },
  });
  assert.equal(status, 409);
});

let memberToken;
let memberUid;

await check("an invitation token that does not exist creates no account", async () => {
  const email = `stranger-${unique()}@example.com`;
  const { status } = await call("POST", "/auth/register", {
    body: { email, password: "stranger-password-1", invitationToken: "f".repeat(64) },
  });
  assert.equal(status, 404);
  assert.equal(await accountExists(email), false);
});

await check("an invited person registers and joins with the token in one step", async () => {
  // Any address will do: the team sponsors the account, so it is not a student one.
  const registered = await call("POST", "/auth/register", {
    body: {
      email: "invitee@example.com",
      password: "member-password-1",
      displayName: "Member",
      invitationToken: inviteToken,
    },
  });
  assert.equal(registered.status, 201, JSON.stringify(registered.body));
  assert.equal(registered.body.organisation.id, orgId);
  assert.equal(registered.body.user.organisationId, orgId);
  assert.equal(registered.body.user.tier, "team");
  assert.equal(registered.body.user.studentVerification.required, false);
  memberUid = registered.body.user.id;
  // Signed in after joining, so the session already carries the team's claims.
  memberToken = registered.body.session.idToken;

  const audit = await call("GET", `/organisations/${orgId}/audit`, { token: adminToken });
  const joined = audit.body.entries.find((e) => e.action === "member.joined");
  assert.ok(joined, "the join should be in the audit log");
  assert.equal(joined.actorId, memberUid);
});

await check("the same invitation token cannot be used twice", async () => {
  const email = `other-${unique()}@example.com`;
  const { status } = await call("POST", "/auth/register", {
    body: { email, password: "other-password-1", invitationToken: inviteToken },
  });
  assert.equal(status, 409, "a redeemed invitation should be refused");
  assert.equal(await accountExists(email), false, "no account should be left behind");
});

await check("an admin can list the members of their own organisation", async () => {
  const { status, body } = await call("GET", `/organisations/${orgId}/members`, {
    token: adminToken,
  });
  assert.equal(status, 200, JSON.stringify(body));
  const emails = body.members.map((m) => m.email);
  assert.ok(emails.includes(adminEmail));
  assert.ok(emails.includes("invitee@example.com"));
});

await check("a plain member cannot use the admin endpoints", async () => {
  const { status } = await call("GET", `/organisations/${orgId}/members`, { token: memberToken });
  assert.equal(status, 403);
});

await check("an admin cannot manage a different organisation", async () => {
  const { status } = await call("GET", "/organisations/some-other-org/members", {
    token: adminToken,
  });
  assert.equal(status, 403, "passing another org id in the URL must be refused");
});

let memberScanId;

await check("an admin sees a member's scan summaries but not their findings", async () => {
  const saved = await call("POST", "/scans", {
    token: memberToken,
    body: scanPayload("com.member.app", [sampleFinding]),
  });
  assert.equal(saved.status, 201, JSON.stringify(saved.body));
  memberScanId = saved.body.scan.id;

  const { status, body } = await call("GET", `/organisations/${orgId}/members/${memberUid}/scans`, {
    token: adminToken,
  });
  assert.equal(status, 200, JSON.stringify(body));
  assert.ok(body.scans.length >= 1);
  // This is the privacy line the project promises: an admin sees that a scan happened
  // and how many problems it found, never the evidence taken from the member's app.
  assert.equal(body.scans[0].findings, undefined, "findings must not be exposed to an admin");
  assert.ok(body.scans[0].counts, "but the counts should be visible");
});

await check("an admin can open a member's run, even one saved before they joined", async () => {
  const opened = await call("GET", `/scans/${memberScanId}`, { token: adminToken });
  assert.equal(opened.status, 200, JSON.stringify(opened.body));
  assert.equal(opened.body.scan.findings.length, 1);

  // A run saved before joining carries no organisation. It is listed for the admin, so
  // it must open too; the flag checks below then run against this unstamped run.
  await db.collection("scans").doc(memberScanId).update({ organisationId: null });
  const preJoin = await call("GET", `/scans/${memberScanId}`, { token: adminToken });
  assert.equal(preJoin.status, 200, JSON.stringify(preJoin.body));
});

await check("reading a member's data is written to the audit log", async () => {
  const { status, body } = await call("GET", `/organisations/${orgId}/audit`, {
    token: adminToken,
  });
  assert.equal(status, 200, JSON.stringify(body));
  const actions = body.entries.map((e) => e.action);
  assert.ok(actions.includes("member.data_viewed"), `audit actions were: ${actions.join(", ")}`);
  assert.ok(actions.includes("member.invited"));
  assert.ok(actions.includes("member.joined"));

  // Each entry names who acted and who it affected, by email rather than uid.
  const viewed = body.entries.find((e) => e.action === "member.data_viewed");
  assert.equal(viewed.actorEmail, adminEmail);
  assert.equal(viewed.subjectEmail, "invitee@example.com");
});

await check("a team member's account reports the team tier and no limits", async () => {
  const { status, body } = await call("GET", "/users/me", { token: memberToken });
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.user.tier, "team");
  assert.equal(body.user.aiQuota.limit, null, "a team account has no AI cap");
});

await check("a team member keeps history beyond the student limit", async () => {
  for (let i = 0; i < 3; i += 1) {
    await call("POST", "/scans", {
      token: memberToken,
      body: scanPayload(`com.team.app${i}`, [sampleFinding]),
    });
  }
  const { body } = await call("GET", "/scans", { token: memberToken });
  assert.ok(body.scans.length > 3, `expected more than 3 scans, got ${body.scans.length}`);
  assert.equal(body.historyLimit, null, "a team account should report no limit");
});

let teamScanA;
let teamScanB;

await check("comparing two scans reports what changed", async () => {
  const a = await call("POST", "/scans", {
    token: memberToken,
    body: scanPayload("com.compare.app", [
      sampleFinding,
      { ...sampleFinding, category: "logcat-leak", severity: "high", component: null },
    ]),
  });
  const b = await call("POST", "/scans", {
    token: memberToken,
    body: scanPayload("com.compare.app", [
      sampleFinding, // still present
      { ...sampleFinding, category: "allow-backup", severity: "medium", component: null }, // new
    ]),
  });
  teamScanA = a.body.scan.id;
  teamScanB = b.body.scan.id;

  const { status, body } = await call(
    "GET",
    `/scans/compare/results?baseline=${teamScanA}&current=${teamScanB}`,
    { token: memberToken }
  );
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.comparison.summary.unchanged, 1);
  assert.equal(body.comparison.summary.resolved, 1, "logcat-leak should read as resolved");
  assert.equal(body.comparison.summary.introduced, 1, "allow-backup should read as introduced");
});

await check("exporting a report returns HTML with the findings in it", async () => {
  const res = await fetch(`${BASE}/scans/${teamScanB}/report`, {
    headers: { Authorization: `Bearer ${memberToken}` },
  });
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type") || "", /html/);
  const html = await res.text();
  assert.match(html, /com\.compare\.app/);
  assert.match(html, /CRITICAL/);
});

await check("viewing a report inline does not persist a reports document", async () => {
  const before = await db.collection(COLLECTIONS.REPORTS).where("scanId", "==", teamScanB).get();
  await fetch(`${BASE}/scans/${teamScanB}/report`, {
    headers: { Authorization: `Bearer ${memberToken}` },
  });
  const after = await db.collection(COLLECTIONS.REPORTS).where("scanId", "==", teamScanB).get();
  assert.equal(after.size, before.size, "an inline view should not create a reports document");
});

await check("downloading a report saves it to the reports collection", async () => {
  const res = await fetch(`${BASE}/scans/${teamScanB}/report?download=true`, {
    headers: { Authorization: `Bearer ${memberToken}` },
  });
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-disposition") || "", /attachment/);

  // The save is fire-and-forget in the route, so give it a moment to land.
  await new Promise((resolve) => setTimeout(resolve, 500));

  const snap = await db.collection(COLLECTIONS.REPORTS).where("scanId", "==", teamScanB).get();
  assert.ok(snap.size >= 1, "expected at least one saved report for this scan");
  const doc = snap.docs[snap.docs.length - 1].data();
  assert.equal(doc.format, "html");
  assert.equal(doc.fileName, `bioaudit-${teamScanB}.html`);
  assert.match(doc.html, /com\.compare\.app/);
});

let flagId;

await check("an admin can flag a member's scan", async () => {
  const { status, body } = await call("POST", `/organisations/${orgId}/scans/${memberScanId}/flag`, {
    token: adminToken,
    body: { reason: "Checking this was an authorised target" },
  });
  assert.equal(status, 201, JSON.stringify(body));
  assert.equal(body.flag.status, "open");
  flagId = body.flag.id;
});

await check("the open flag appears in the review list", async () => {
  const { status, body } = await call("GET", `/organisations/${orgId}/flags`, { token: adminToken });
  assert.equal(status, 200);
  assert.equal(body.flags.length, 1);
  assert.equal(body.flags[0].id, flagId);
});

await check("reviewing the flag closes it and records the decision", async () => {
  const { status, body } = await call(
    "POST",
    `/organisations/${orgId}/flags/${flagId}/review`,
    { token: adminToken, body: { decision: "dismiss", note: "Target was authorised" } }
  );
  assert.equal(status, 200, JSON.stringify(body));
  assert.equal(body.flag.status, "dismissed");

  const list = await call("GET", `/organisations/${orgId}/flags`, { token: adminToken });
  assert.equal(list.body.flags.length, 0, "a reviewed flag should leave the open list");
});

await check("the same flag cannot be reviewed twice", async () => {
  const { status } = await call("POST", `/organisations/${orgId}/flags/${flagId}/review`, {
    token: adminToken,
    body: { decision: "uphold" },
  });
  assert.equal(status, 409);
});

await check("an admin can promote a member to admin", async () => {
  const { status, body } = await call("POST", `/organisations/${orgId}/admins`, {
    token: adminToken,
    body: { uid: memberUid },
  });
  assert.equal(status, 200, JSON.stringify(body));

  const snap = await db.collection("users").doc(memberUid).get();
  assert.equal(snap.data().role, "admin");
});

await check("removing a member leaves their account and scans intact", async () => {
  const { status } = await call("DELETE", `/organisations/${orgId}/members/${memberUid}`, {
    token: adminToken,
  });
  assert.equal(status, 200);

  const profile = await db.collection("users").doc(memberUid).get();
  assert.ok(profile.exists, "the account should still exist");
  assert.equal(profile.data().organisationId, null);
  assert.equal(profile.data().role, "member", "they should be demoted back to member");

  const stillThere = await db.collection("scans").doc(memberScanId).get();
  assert.ok(stillThere.exists, "their scans should not have been deleted");

  const opened = await call("GET", `/scans/${memberScanId}`, { token: adminToken });
  assert.equal(opened.status, 403, "a removed member's runs are no longer the admin's to read");
});

await check("an admin cannot remove themselves", async () => {
  const owner = await auth.getUserByEmail(adminEmail);
  const { status } = await call("DELETE", `/organisations/${orgId}/members/${owner.uid}`, {
    token: adminToken,
  });
  assert.equal(status, 400);
});

await check("deleting a member account needs an explicit confirmation and a reason", async () => {
  // Re-invite so there is a member to act on.
  const invite = await call("POST", `/organisations/${orgId}/invitations`, {
    token: adminToken,
    body: { email: `doomed-${unique()}@example.com` },
  });
  const email = invite.body.invitation.email;
  const registered = await call("POST", "/auth/register", {
    body: { email, password: "doomed-password-1", invitationToken: invite.body.token },
  });
  const doomedUid = registered.body.user.id;

  const missingConfirm = await call(
    "DELETE",
    `/organisations/${orgId}/members/${doomedUid}/account`,
    { token: adminToken, body: { reason: "no confirmation supplied" } }
  );
  assert.equal(missingConfirm.status, 400, "a missing confirmation must be refused");

  const done = await call("DELETE", `/organisations/${orgId}/members/${doomedUid}/account`, {
    token: adminToken,
    body: { confirm: "DELETE_ACCOUNT", reason: "Left the company" },
  });
  assert.equal(done.status, 200, JSON.stringify(done.body));

  const gone = await db.collection("users").doc(doomedUid).get();
  assert.ok(!gone.exists, "the account record should be gone");

  const audit = await call("GET", `/organisations/${orgId}/audit`, { token: adminToken });
  const deletion = audit.body.entries.find((e) => e.action === "member.account_deleted");
  assert.ok(deletion, "the deletion should be in the audit log");
  assert.equal(deletion.metadata.reason, "Left the company");
});

group("Account deletion");

await check("deleting your own account removes your scan history with it", async () => {
  const email = `throwaway-${unique()}@example.ac.uk`;
  const password = "throwaway-password-1";
  const registered = await registerStudent({ email, password });
  const token = registered.body.session.idToken;
  const uid = registered.body.user.id;

  await call("POST", "/scans", { token, body: scanPayload("com.throwaway.app", [sampleFinding]) });

  const before = await db.collection("scans").where("userId", "==", uid).get();
  assert.ok(before.size >= 1, "expected a scan to exist before deletion");

  const { status } = await call("DELETE", "/users/me", {
    token,
    body: { currentPassword: password, confirm: "DELETE" },
  });
  assert.equal(status, 200);

  const after = await db.collection("scans").where("userId", "==", uid).get();
  assert.equal(after.size, 0, "their scans should have been deleted too");

  const code = await db.collection("studentVerifications").doc(uid).get();
  assert.ok(!code.exists);

  const profile = await db.collection("users").doc(uid).get();
  assert.ok(!profile.exists);
});

// --- done ------------------------------------------------------------------

server.close();

console.log(`\n${passed} passed, ${failures.length} failed.`);
if (failures.length > 0) {
  for (const { name, error } of failures) {
    console.error(`\n--- ${name} ---\n${error.stack}`);
  }
  process.exitCode = 1;
}
