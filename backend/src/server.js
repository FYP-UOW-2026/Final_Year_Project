/**
 * Server entry point.
 *
 * Configuration and Firebase are loaded before the app is built, so a missing service
 * account or web API key stops the process with a clear message instead of failing on
 * the first request.
 */
import { createApp } from "./app.js";
import { env } from "./config/env.js";
import "./config/firebase.js";
import { verifyConnection } from "./services/email.service.js";

const app = createApp();

const server = app.listen(env.port, () => {
  console.log(`BioAudit API listening on port ${env.port} in ${env.nodeEnv} mode.`);
  if (!env.groq.enabled) {
    console.log("AI explanations are disabled. Set GROQ_API_KEY to turn them on.");
  }
  reportEmailStatus();
});

/** Say at startup whether verification emails will actually go out, and if not, why. */
async function reportEmailStatus() {
  if (env.email.problem) {
    console.log(`Email is NOT working: ${env.email.problem}`);
    return;
  }
  if (!env.email.enabled) {
    console.log(
      env.isProduction
        ? "Email is NOT configured: students cannot receive verification codes. Set " +
            "MAILGUN_API_KEY and MAILGUN_DOMAIN (or SMTP_HOST, SMTP_USER and SMTP_PASS)."
        : "Email is not configured: verification codes will be printed here instead."
    );
    return;
  }
  const { ok, error } = await verifyConnection();
  console.log(
    ok
      ? `Email ready: sending as ${env.email.from} via ${env.email.via}.`
      : `Email is configured but not working (${error}).`
  );
}

/** Finish in-flight requests before exiting, so a deploy does not cut a scan upload short. */
function shutdown(signal) {
  console.log(`${signal} received. Closing server.`);
  server.close(() => process.exit(0));
  // If connections refuse to drain, stop anyway rather than hanging the deploy.
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

process.on("unhandledRejection", (reason) => {
  console.error("Unhandled promise rejection:", reason);
});
