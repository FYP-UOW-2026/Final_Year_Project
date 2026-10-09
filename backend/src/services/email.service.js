/**
 * Outbound notification email.
 *
 * Every function here is best-effort and never throws: an invitation that was created,
 * a password that was changed, an account that was removed -- those all already
 * succeeded by the time we try to send. Failing the request because a mail server was
 * unreachable would undo work that is done and correct, so send failures are logged and
 * swallowed instead.
 *
 * With no mail provider configured the whole module becomes a no-op, which is what lets
 * the app run locally, in tests, and against the emulators without mail credentials.
 *
 * The one message a caller cannot do without is the student verification code, since
 * the free plan stays locked until it arrives. That caller checks the returned "sent"
 * flag and reports a failure to the user, rather than this module throwing.
 *
 * Note on invitations: BioAudit's client is a desktop app, so there is no web page for
 * an invite link to land on. The email carries the invitation *token*, which the
 * recipient pastes into the app's join dialog. The token is what the invite endpoint
 * already returns for the admin to pass on by hand -- emailing it just saves that step.
 */
import FormData from "form-data";
import Mailgun from "mailgun.js";
import nodemailer from "nodemailer";

import { env } from "../config/env.js";

/*
 * Two ways out, chosen in config/env.js. Mailgun is called over its HTTP API with the
 * official mailgun.js client; anything else goes over SMTP through nodemailer.
 *
 * The credentials live only here on the server. They must never be put in the desktop
 * app, which is handed to every user: anyone could lift them out and send mail as us.
 */
const TIMEOUT_MS = 15_000;

let transport = null;
let mailgunClient = null;

function getTransport() {
  if (!env.email.enabled || env.email.provider !== "smtp") return null;
  if (!transport) {
    transport = nodemailer.createTransport({
      host: env.email.host,
      port: env.email.port,
      // 465 is implicit TLS; anything else (typically 587) upgrades with STARTTLS.
      secure: env.email.port === 465,
      auth: { user: env.email.user, pass: env.email.pass },
      // Without ceilings, a stalled relay would hold a student's "Send code" open.
      connectionTimeout: TIMEOUT_MS,
      greetingTimeout: TIMEOUT_MS,
      socketTimeout: TIMEOUT_MS + 5_000,
    });
  }
  return transport;
}

function getMailgun() {
  if (!env.email.enabled || env.email.provider !== "mailgun") return null;
  if (!mailgunClient) {
    mailgunClient = new Mailgun(FormData).client({
      username: "api",
      key: env.email.mailgun.apiKey,
      url: env.email.mailgun.url,
      timeout: TIMEOUT_MS,
    });
  }
  return mailgunClient;
}

/** Mailgun's own explanation of a failure, which says far more than the status alone. */
function mailgunErrorMessage(error) {
  const detail = error?.details || error?.message || String(error);
  return `Mailgun${error?.status ? ` ${error.status}` : ""}: ${detail}`;
}

/** The message fields as Mailgun's messages.create takes them. */
export function mailgunMessage({ from, to, subject, text, html }) {
  return {
    from,
    to: [to],
    subject,
    text,
    ...(html ? { html } : {}),
    // A verification code is transactional: no open or click tracking, which would
    // rewrite the message and make it look more like marketing to spam filters.
    "o:tracking": "no",
    "o:tracking-clicks": "no",
    "o:tracking-opens": "no",
  };
}

/**
 * Check the credentials once at startup, so a bad key shows up in the server log
 * immediately rather than as a student saying their code never arrived. Neither check
 * sends anything: Mailgun looks the sending domain up, SMTP just logs in.
 */
export async function verifyConnection() {
  if (!env.email.enabled) return { ok: false, error: "not configured" };
  try {
    const mailgun = getMailgun();
    if (mailgun) {
      const domain = await mailgun.domains.get(env.email.mailgun.domain);
      if (domain?.state && domain.state !== "active") {
        return { ok: false, error: `the Mailgun domain is "${domain.state}", not active yet` };
      }
    } else {
      await getTransport().verify();
    }
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: env.email.provider === "mailgun" ? mailgunErrorMessage(error) : error.message,
    };
  }
}

/**
 * Whether Mailgun refused because the recipient is not on a sandbox domain's authorised
 * list. That is a setting in the Mailgun dashboard, not a fault, and retrying never helps.
 */
function isUnauthorisedSandboxRecipient(error) {
  return error?.status === 403 && /authori[sz]ed recipients/i.test(String(error?.details ?? ""));
}

/**
 * Send one message. Resolves either way, to { sent, recipientNotAllowed }; the second
 * is true only when the provider refused this particular address outright.
 */
async function deliver({ to, subject, text, html }) {
  if (!env.email.enabled) return { sent: false, recipientNotAllowed: false };

  try {
    const mailgun = getMailgun();
    if (mailgun) {
      await mailgun.messages.create(
        env.email.mailgun.domain,
        mailgunMessage({ from: env.email.from, to, subject, text, html })
      );
    } else {
      await getTransport().sendMail({ from: env.email.from, to, subject, text, html });
    }
    return { sent: true, recipientNotAllowed: false };
  } catch (error) {
    // Deliberately not rethrown -- see the module docstring. The provider's reason for
    // refusing (an unauthorised sandbox recipient, say) is kept for the log.
    const reason = env.email.provider === "mailgun" ? mailgunErrorMessage(error) : error.message;
    console.error(`Could not send "${subject}" to ${to}: ${reason}`);
    return { sent: false, recipientNotAllowed: isUnauthorisedSandboxRecipient(error) };
  }
}

/** For notifications, where all that matters is whether it went. */
async function send(message) {
  return (await deliver(message)).sent;
}

export function sendInvitation({ to, organisationName, token, role, expiresInDays }) {
  const roleWording =
    role === "admin"
      ? "as an administrator, so you will be able to oversee the team's assessments"
      : "as a member";

  return send({
    to,
    subject: `You have been invited to ${organisationName} on BioAudit`,
    text: [
      `You have been invited to join ${organisationName} on BioAudit ${roleWording}.`,
      "",
      "To accept, open BioAudit, choose \"Join an organisation\" from the Account menu,",
      "and paste this invitation token:",
      "",
      `    ${token}`,
      "",
      `The token can be used once and expires in ${expiresInDays} days.`,
      "",
      "Joining lets the organisation's administrators see the assessments run from your",
      "account. If you were not expecting this invitation, you can ignore it -- nothing",
      "happens until you accept.",
    ].join("\n"),
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/**
 * The verification email's subject, plain text and HTML.
 *
 * Kept separate from sending so the smoke test can check the content without a mail
 * server. The HTML uses inline styles and tables only, because that is all most mail
 * clients (Outlook especially, which most universities run) reliably render. There are
 * no images or links: a code-only message is less likely to be filtered as phishing.
 */
export function studentVerificationEmail({ code, expiresInMinutes, universityName }) {
  const where = universityName ? ` at ${universityName}` : "";
  const subject = `${code} is your BioAudit student verification code`;

  const text = [
    `Confirm you're a student${where}.`,
    "",
    "Enter this code in BioAudit to verify your university email address:",
    "",
    `    ${code}`,
    "",
    `The code expires in ${expiresInMinutes} minutes and can only be used once.`,
    "Verifying unlocks BioAudit's free student plan on your account.",
    "",
    "If you did not create a BioAudit account, you can ignore this email. Nobody can",
    "verify an address without the code, and BioAudit will never ask you for it.",
  ].join("\n");

  const spaced = escapeHtml(code).split("").join("&#8202;");
  const html = `<!doctype html>
<html><body style="margin:0;padding:0;background:#f3f4f6">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:12px;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#111827">
<tr><td style="background:#107C10;border-radius:12px 12px 0 0;padding:18px 28px;color:#ffffff;font-size:18px;font-weight:700">BioAudit</td></tr>
<tr><td style="padding:28px 28px 8px 28px">
<p style="margin:0 0 6px 0;font-size:20px;font-weight:700">Confirm you're a student</p>
<p style="margin:0;font-size:14px;line-height:21px;color:#4b5563">Enter this code in BioAudit to verify your university email${escapeHtml(where)} and unlock the free student plan.</p>
</td></tr>
<tr><td align="center" style="padding:20px 28px">
<div style="display:inline-block;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:14px 26px;font-family:Consolas,Menlo,monospace;font-size:34px;font-weight:700;letter-spacing:8px;color:#0c5e0c">${spaced}</div>
</td></tr>
<tr><td style="padding:0 28px 26px 28px;font-size:13px;line-height:20px;color:#6b7280">
The code expires in ${escapeHtml(expiresInMinutes)} minutes and can only be used once.<br><br>
If you did not create a BioAudit account, you can ignore this email. Nobody can verify an address without the code, and BioAudit will never ask you for it.
</td></tr>
</table>
</td></tr>
</table>
</body></html>`;

  return { subject, text, html };
}

/**
 * Unlike the notifications, the caller needs to know why a code did not go, so it can
 * tell the student something they can act on. Resolves to { sent, recipientNotAllowed }.
 */
export function sendStudentVerificationCode({ to, code, expiresInMinutes, universityName }) {
  return deliver({ to, ...studentVerificationEmail({ code, expiresInMinutes, universityName }) });
}

export function sendPasswordChanged({ to }) {
  return send({
    to,
    subject: "Your BioAudit password was changed",
    text: [
      "The password on your BioAudit account was just changed, and every other device",
      "signed in to this account has been signed out.",
      "",
      "If this was you, there is nothing to do.",
      "",
      "If it was not, someone else may have access to your account. Reset your password",
      "immediately and check your assessment history for activity you do not recognise.",
    ].join("\n"),
  });
}

export function sendRemovedFromOrganisation({ to, organisationName }) {
  return send({
    to,
    subject: `You are no longer part of ${organisationName} on BioAudit`,
    text: [
      `Your BioAudit account is no longer linked to ${organisationName}.`,
      "",
      "Your account itself is unaffected: it still exists, you keep your own free or",
      "premium status, and your assessment history stays with you. The organisation's",
      "administrators can no longer see it.",
    ].join("\n"),
  });
}

export function sendAccountDeletedByAdmin({ to, organisationName }) {
  return send({
    to,
    subject: "Your BioAudit account has been deleted",
    text: [
      `An administrator of ${organisationName} has deleted your BioAudit account.`,
      "",
      "Your assessment history has been permanently removed and you can no longer sign",
      "in. If you believe this was a mistake, contact your organisation's administrator.",
    ].join("\n"),
  });
}

export function sendAccountSuspended({ to, organisationName, suspended }) {
  return send({
    to,
    subject: suspended
      ? "Your BioAudit account has been suspended"
      : "Your BioAudit account has been reinstated",
    text: suspended
      ? [
          `An administrator of ${organisationName} has suspended your BioAudit account.`,
          "",
          "You have been signed out and cannot sign in again until the suspension is",
          "lifted. Your account and assessment history have not been deleted. Contact",
          "your organisation's administrator to find out why.",
        ].join("\n")
      : [
          `An administrator of ${organisationName} has lifted the suspension on your`,
          "BioAudit account. You can sign in again as normal.",
        ].join("\n"),
  });
}

export function sendMemberLeft({ to, memberEmail, organisationName }) {
  return send({
    to,
    subject: `A member deleted their BioAudit account (${organisationName})`,
    text: [
      `${memberEmail} has deleted their own BioAudit account and is therefore no longer`,
      `part of ${organisationName}.`,
      "",
      "This notice is for your records. A person can always delete their own account,",
      "and their assessment history is removed with it.",
    ].join("\n"),
  });
}
