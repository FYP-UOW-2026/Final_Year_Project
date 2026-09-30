/**
 * Loads and validates configuration once at startup.
 *
 * Failing fast here is deliberate. A missing service account or web API key would
 * otherwise surface much later as a confusing runtime error on the first request.
 */
import dotenv from "dotenv";

dotenv.config();

function required(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing required environment variable ${name}. Copy .env.example to .env and fill it in.`
    );
  }
  return value;
}

function optional(name, fallback = "") {
  return process.env[name] || fallback;
}

function int(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isNaN(parsed) ? fallback : parsed;
}

const serviceAccountPath = optional("GOOGLE_APPLICATION_CREDENTIALS");
const hasInlineCredentials =
  process.env.FIREBASE_PROJECT_ID &&
  process.env.FIREBASE_CLIENT_EMAIL &&
  process.env.FIREBASE_PRIVATE_KEY;

/**
 * The Firebase emulators accept any caller, so credentials are neither needed nor
 * checked when these variables are present. The emulator sets them itself, which is what
 * lets the integration tests run with no service account anywhere.
 */
const usingEmulators = Boolean(
  process.env.FIRESTORE_EMULATOR_HOST || process.env.FIREBASE_AUTH_EMULATOR_HOST
);

/**
 * A fourth credential source: Google Cloud's own hosting products (Cloud Run, Cloud
 * Functions, App Engine, GCE) hand every process Application Default Credentials via
 * an on-instance metadata server, so no key ever needs to exist on disk or in an env
 * var. K_SERVICE/K_REVISION/K_CONFIGURATION are set automatically by Cloud Run;
 * GOOGLE_CLOUD_PROJECT and GAE_SERVICE cover Cloud Functions and App Engine.
 */
const runningOnGoogleCloud = Boolean(
  process.env.K_SERVICE || process.env.GOOGLE_CLOUD_PROJECT || process.env.GAE_SERVICE
);

if (!usingEmulators && !serviceAccountPath && !hasInlineCredentials && !runningOnGoogleCloud) {
  throw new Error(
    "No Firebase credentials found. Set GOOGLE_APPLICATION_CREDENTIALS to a service " +
      "account JSON path, or set FIREBASE_PROJECT_ID, FIREBASE_CLIENT_EMAIL and " +
      "FIREBASE_PRIVATE_KEY."
  );
}

export const env = {
  port: int("PORT", 4000),
  nodeEnv: optional("NODE_ENV", "development"),
  isProduction: optional("NODE_ENV", "development") === "production",

  corsOrigins: optional("CORS_ORIGINS")
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean),

  usingEmulators,

  firebase: {
    serviceAccountPath,
    projectId: optional("FIREBASE_PROJECT_ID") || optional("GCLOUD_PROJECT"),
    clientEmail: optional("FIREBASE_CLIENT_EMAIL"),
    // Hosting panels usually store the key as one line with literal \n sequences.
    privateKey: optional("FIREBASE_PRIVATE_KEY").replace(/\\n/g, "\n"),
    webApiKey: required("FIREBASE_WEB_API_KEY"),
  },

  groq: {
    apiKey: optional("GROQ_API_KEY"),
    // gpt-oss-120b is one of Groq's current free-tier chat models (1,000 req/day,
    // 30/min, no card required) and more than capable of rephrasing a finding that
    // has already been confirmed and grounded -- this task doesn't need a frontier
    // model. Pinned rather than left to a "latest" alias for the same reason a
    // pinned Gemini model was used before: predictable behaviour beats whatever a
    // provider quietly repoints an alias to.
    model: optional("GROQ_MODEL", "openai/gpt-oss-120b"),
    // Ceiling on a single attempt. Without one an overloaded model could leave the
    // request open for minutes, which is worse than failing: the caller has long
    // since given up, and the retries in groq.service.js never get their turn.
    timeoutMs: int("GROQ_TIMEOUT_MS", 20000),
    get enabled() {
      return Boolean(this.apiKey);
    },
  },

  /**
   * Outbound email. Optional in the same way the AI layer is: with nothing configured
   * the app runs normally and simply does not send, so a developer running locally is
   * never blocked by not having mail credentials. In production it is needed, because
   * student verification codes are only ever delivered by email.
   *
   * Mailgun (MAILGUN_API_KEY + MAILGUN_DOMAIN) is used when configured; otherwise any
   * SMTP provider works through SMTP_HOST, SMTP_USER and SMTP_PASS.
   */
  email: {
    mailgun: {
      apiKey: optional("MAILGUN_API_KEY"),
      domain: optional("MAILGUN_DOMAIN"),
      // Only an EU-region domain needs this: https://api.eu.mailgun.net
      url: optional("MAILGUN_URL", "https://api.mailgun.net"),
    },
    host: optional("SMTP_HOST"),
    port: int("SMTP_PORT", 587),
    user: optional("SMTP_USER"),
    pass: optional("SMTP_PASS"),
    get provider() {
      if (this.mailgun.apiKey) return "mailgun";
      return this.host ? "smtp" : null;
    },
    // Providers reject or rewrite a From address the account does not own, and a
    // mismatched sender is the quickest way into a university's spam folder. So Mailgun
    // defaults to its own domain's postmaster, and SMTP to the account signed in as.
    get from() {
      if (this.provider === "mailgun") {
        return (
          optional("MAILGUN_FROM") ||
          (this.mailgun.domain ? `BioAudit <postmaster@${this.mailgun.domain}>` : "")
        );
      }
      return optional("SMTP_FROM") || (this.user.includes("@") ? `BioAudit <${this.user}>` : "");
    },
    get enabled() {
      // Never against the emulators. They hold test accounts at made-up addresses, and
      // .env is loaded there too, so real mail would go out and bounce, which damages
      // the sending account's reputation with its mail provider.
      if (usingEmulators || !this.from) return false;
      if (this.provider === "mailgun") return Boolean(this.mailgun.domain);
      return Boolean(this.provider === "smtp" && this.user && this.pass);
    },
    /** Why sending is off, for the startup log; null when it is on or not attempted. */
    get problem() {
      if (usingEmulators || this.enabled || !this.provider) return null;
      if (this.provider === "mailgun") {
        return "MAILGUN_API_KEY is set but MAILGUN_DOMAIN is not. Set it to your Mailgun sending domain.";
      }
      if (!this.from) {
        return 'SMTP_FROM is not set. Set it to the address to send from, e.g. "BioAudit <you@example.com>".';
      }
      return "SMTP_HOST, SMTP_USER and SMTP_PASS must all be set.";
    },
    /** For log lines: where mail goes out through. */
    get via() {
      return this.provider === "mailgun"
        ? `Mailgun (${this.mailgun.domain})`
        : `${this.host}:${this.port}`;
    },
  },

  limits: {
    freeHistory: int("FREE_HISTORY_LIMIT", 10),
    // Free-tier ceiling on AI explanations per calendar month. Premium is uncapped, so
    // there is no matching setting for it. This is a BioAudit-side courtesy limit, not
    // a mirror of Groq's own per-account quota -- the two are independent, and it is
    // possible to exhaust either one first.
    freeAiPerMonth: int("FREE_AI_MONTHLY_LIMIT", 20),
  },

  /**
   * Free-plan eligibility. The usual university domain shapes (.ac.uk, .edu, .edu.xx,
   * .ac.xx) are built in; this adds institutions that use something else. Each entry
   * also covers its subdomains, so "uni.example" accepts "student.uni.example".
   */
  student: {
    extraDomains: optional("STUDENT_EMAIL_DOMAINS")
      .split(",")
      .map((d) => d.trim().toLowerCase().replace(/^[@.]+/, ""))
      .filter(Boolean),
  },

  /**
   * Rate limits, configurable so a test run is not fighting production tuning.
   *
   * The auth limit is deliberately much tighter than the general one, since sign-in and
   * registration are the two endpoints worth guessing at. Raise them only for local
   * testing against the emulators.
   */
  rateLimits: {
    apiPerMinute: int("API_RATE_LIMIT_PER_MINUTE", 120),
    authPer15Min: int("AUTH_RATE_LIMIT_PER_15MIN", 20),
  },
};
