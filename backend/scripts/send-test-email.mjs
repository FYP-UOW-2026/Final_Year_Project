/**
 * Send a sample student verification email, to check the SMTP settings in .env.
 *
 *   npm run email:test -- you@university.edu.sg
 *
 * Uses the same template and transport as the real thing, with a fixed dummy code, so
 * what arrives is exactly what a student would see. Touches no account and no database.
 */
import { env } from "../src/config/env.js";
import {
  sendStudentVerificationCode,
  verifyConnection,
} from "../src/services/email.service.js";

const to = process.argv[2];
if (!to || !to.includes("@")) {
  console.error("Usage: npm run email:test -- you@example.com");
  process.exit(2);
}

if (!env.email.enabled) {
  console.error(
    env.email.problem ??
      "Email is not configured. Set MAILGUN_API_KEY and MAILGUN_DOMAIN (or SMTP_HOST, " +
        "SMTP_USER and SMTP_PASS) in backend/.env."
  );
  process.exit(1);
}

console.log(`Checking the credentials for ${env.email.via}...`);
const login = await verifyConnection();
if (!login.ok) {
  console.error(`Credential check failed: ${login.error}`);
  console.error(
    env.email.provider === "mailgun"
      ? "Check MAILGUN_API_KEY is your private API key and MAILGUN_DOMAIN is spelt exactly."
      : "Check SMTP_USER and SMTP_PASS. For Gmail, SMTP_PASS must be a 16-character App " +
          "Password, not your normal password."
  );
  process.exit(1);
}

const { sent, recipientNotAllowed } = await sendStudentVerificationCode({
  to,
  code: "123456",
  expiresInMinutes: 15,
  universityName: "your university (this is a test)",
});
if (!sent) {
  console.error(
    recipientNotAllowed
      ? `Not sent: ${to} is not an authorised recipient of this sandbox domain. Add it in ` +
          "Mailgun under Sending > Domain settings > Authorized Recipients."
      : "Not sent: see the reason above."
  );
  process.exit(1);
}
console.log(`Sent from ${env.email.from} to ${to}. Check the inbox, and the spam folder.`);
