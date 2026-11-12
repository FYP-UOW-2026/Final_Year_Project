/**
 * Student email verification for personal accounts.
 *
 * A personal account exists for students, so it has to prove it belongs to one. The
 * proof is the usual one: the email address is at a university domain, and its owner can
 * read mail sent there. We email a six-digit code and the owner types it in.
 *
 * The proof comes twice in an account's life:
 *   * At sign-up, before the account exists. POST /auth/register needs the code sent by
 *     sendSignupCode, so a personal account is born verified (see the sign-up section).
 *   * After changing the account's email, which lapses the earlier proof. The account
 *     keeps its data but cannot run assessments until the new address is verified.
 *
 * Who this applies to:
 *   * Personal accounts with no organisation. These are the student accounts.
 *   * Not admins, who do not run assessments at all (see denyAdmin).
 *   * Not organisation members, whose access is sponsored by their organisation and who
 *     were invited at whatever address the organisation uses, academic or not.
 *
 * Where the code lives: in its own collection rather than on the user document. The
 * owner can read their own user document under the Firestore rules, and a six-digit
 * code's hash is trivially reversed by trying all million values, so storing it there
 * would let someone "verify" an address they cannot actually receive mail at.
 */
import crypto from "node:crypto";
import { Resolver } from "node:dns/promises";

import { auth, db, FieldValue, Timestamp } from "../config/firebase.js";
import { env } from "../config/env.js";
import {
  COLLECTIONS,
  ROLES,
  STUDENT_ACCESS_YEARS,
  STUDENT_STATUS,
} from "../constants/index.js";
import { findUniversity } from "../data/universities.js";
import { ApiError } from "../utils/ApiError.js";
import * as email from "./email.service.js";

export const CODE_TTL_MINUTES = 15;
const MAX_ATTEMPTS = 5;
const RESEND_COOLDOWN_SECONDS = 60;
/**
 * Codes any one address can be sent in a day, on top of the minute between them. Enough
 * for a typo or two and a slow inbox. It stops the code emails being used to flood
 * someone's inbox, and caps guessing at MAX_ATTEMPTS a code, so 25 tries a day.
 */
const MAX_CODES_PER_DAY = 5;
const DAY_MS = 24 * 60 * 60 * 1000;

const users = () => db.collection(COLLECTIONS.USERS);
const codes = () => db.collection(COLLECTIONS.STUDENT_VERIFICATIONS);
const signupCodes = () => db.collection(COLLECTIONS.SIGNUP_VERIFICATIONS);

/**
 * Domain shapes universities use worldwide: example.ac.uk, example.edu, example.edu.au,
 * example.ac.jp and so on. Institutions outside these patterns can be added with
 * STUDENT_EMAIL_DOMAINS rather than widening the patterns for everyone.
 */
const ACADEMIC_DOMAIN_PATTERNS = [/\.ac\.uk$/, /\.edu$/, /\.edu\.[a-z]{2}$/, /\.ac\.[a-z]{2}$/];

function domainOf(address) {
  const at = (address || "").lastIndexOf("@");
  return at === -1 ? "" : address.slice(at + 1).toLowerCase();
}

/**
 * Whether a domain is one of `domains` or a subdomain of one. Matching on "." + domain
 * means "ox.ac.uk" also covers "st-hughs.ox.ac.uk", but never "fox.ac.uk".
 */
function domainWithin(domain, domains) {
  const dotted = `.${domain}`;
  return domains.some((d) => dotted.endsWith(`.${d}`));
}

// A student is waiting on the answer, so a slow resolver gets a short leash.
const mailResolver = new Resolver({ timeout: 3000, tries: 2 });

/**
 * Whether mail sent to `domain` has anywhere to go, by the rules a mail server delivers
 * by (RFC 5321): the domain's MX records or, with none, its own address. A "null MX"
 * (RFC 7505) is a domain declaring that it takes no mail at all.
 *
 * Resolves to false only when DNS says so plainly. If DNS cannot be asked (a timeout, a
 * resolver outage) it resolves to null: trouble on our side is no reason to turn a
 * student away. This proves the domain receives mail, not that the mailbox exists;
 * only delivering to it can show that, which is what the code is for.
 */
export async function domainReceivesMail(domain, dns = mailResolver) {
  try {
    const records = await dns.resolveMx(domain);
    return records.some((r) => r.exchange && r.exchange !== ".");
  } catch (error) {
    if (error.code === "ENOTFOUND") return false;
    if (error.code !== "ENODATA") return null;
  }
  for (const lookup of ["resolve4", "resolve6"]) {
    try {
      if ((await dns[lookup](domain)).length > 0) return true;
    } catch (error) {
      if (error.code !== "ENODATA" && error.code !== "ENOTFOUND") return null;
    }
  }
  return false;
}

/**
 * Refuse an address whose domain cannot receive mail, which is nearly always a typo
 * ("ox.ac.ku"). Better said now than after the student has waited for a code that was
 * never going to arrive.
 */
async function assertDomainReceivesMail(address) {
  const domain = domainOf(address);
  const receives = await domainReceivesMail(domain);
  if (receives === null) {
    console.warn(`Could not check whether ${domain} receives mail. Sending the code anyway.`);
  } else if (!receives) {
    throw ApiError.badRequest(
      `${domain} does not receive email, so a code sent to ${address} could never ` +
        "arrive. Check the address for typos."
    );
  }
}

/**
 * Whether an address is one we accept for the free plan.
 *
 * With a university chosen from the list, the address must be at one of that
 * university's own domains, the same way GitHub Education and Apple's student pricing
 * tie the school you pick to the email you prove. With none chosen ("my university isn't
 * listed"), any address at a recognised academic domain will do.
 */
export function isAcademicEmail(address, universityId = null) {
  const domain = domainOf(address);
  if (!domain) return false;

  const university = findUniversity(universityId);
  if (university) return domainWithin(domain, university.domains);

  if (domainWithin(domain, env.student.extraDomains)) return true;
  return ACADEMIC_DOMAIN_PATTERNS.some((pattern) => pattern.test(domain));
}

/**
 * Refuse an email that does not belong to the chosen university, with a message that
 * says which domains would. Used when a university is picked, at registration or later.
 */
export function assertEmailMatchesUniversity(address, universityId) {
  if (!universityId) return;
  const university = findUniversity(universityId);
  if (!university) throw ApiError.badRequest("Choose your university from the list.");
  if (!isAcademicEmail(address, universityId)) {
    const endings = university.domains.map((d) => `@${d}`).join(" or ");
    throw ApiError.badRequest(
      `Use your ${university.name} email address. It should end in ${endings}.`
    );
  }
}

function publicUniversity(universityId) {
  const university = findUniversity(universityId);
  return university ? { id: university.id, name: university.name } : null;
}

/** Whether this account has to be a verified student to run assessments. */
export function isRequiredFor(profile) {
  return profile.role !== ROLES.ADMIN && !profile.organisationId;
}

/** A stored timestamp in milliseconds, whichever form it arrives in, or null. */
function millisOf(value) {
  if (!value) return null;
  if (typeof value.toMillis === "function") return value.toMillis();
  if (value instanceof Date) return value.getTime();
  const seconds = value._seconds ?? value.seconds;
  if (typeof seconds === "number") return seconds * 1000;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * When a personal account's student access ends: STUDENT_ACCESS_YEARS after it was
 * registered. Fixed from the day the account was made, so nothing the owner does later
 * moves it. Null for a record with no registration date, which is left without a limit
 * rather than locked out on a guess.
 */
export function studentAccessEndsAt(profile) {
  const registered = millisOf(profile.createdAt);
  if (registered === null) return null;
  const ends = new Date(registered);
  ends.setUTCFullYear(ends.getUTCFullYear() + STUDENT_ACCESS_YEARS);
  return ends;
}

function studentAccessEnded(profile) {
  const ends = studentAccessEndsAt(profile);
  return Boolean(ends) && Date.now() >= ends.getTime();
}

/**
 * The verification state as the client sees it.
 *
 * Verification is tied to the address that was verified, so changing the account's
 * email afterwards makes it unverified again without anything having to remember to
 * reset it. Once student access has ended the status is "expired", whatever was
 * verified, and stays so.
 */
export function statusFor(profile) {
  const stored = profile.studentVerification ?? {};
  const verified = stored.status === STUDENT_STATUS.VERIFIED && stored.email === profile.email;
  const required = isRequiredFor(profile);
  const accessEndsAt = required ? studentAccessEndsAt(profile) : null;

  let status = STUDENT_STATUS.UNVERIFIED;
  if (required && studentAccessEnded(profile)) status = STUDENT_STATUS.EXPIRED;
  else if (verified) status = STUDENT_STATUS.VERIFIED;
  else if (stored.status === STUDENT_STATUS.PENDING && stored.email === profile.email) {
    status = STUDENT_STATUS.PENDING;
  }

  return {
    status,
    required,
    eligible: isAcademicEmail(profile.email, stored.universityId),
    university: publicUniversity(stored.universityId),
    verifiedAt: verified ? (stored.verifiedAt ?? null) : null,
    // So a client can say when student access ends, before it does.
    accessEndsAt: accessEndsAt ? accessEndsAt.toISOString() : null,
  };
}

/** "12 November 2029", for messages. */
function formatDay(date) {
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric",
    timeZone: "UTC" });
}

function accessEndedMessage(profile) {
  return (
    `Student access lasts ${STUDENT_ACCESS_YEARS} years from registering, and yours ended ` +
    `on ${formatDay(studentAccessEndsAt(profile))}. To keep running assessments, join a ` +
    "team with an invitation from its admin, or set up your own team (a solo team if you " +
    "work alone). Your history is still here to view."
  );
}

/** Whether the account may use the features gated behind student verification. */
export function isSatisfied(profile) {
  return !isRequiredFor(profile) || statusFor(profile).status === STUDENT_STATUS.VERIFIED;
}

/** The message shown when an unverified student tries a gated feature. */
export function refusalMessage(profile) {
  if (statusFor(profile).status === STUDENT_STATUS.EXPIRED) return accessEndedMessage(profile);
  const university = findUniversity(profile.studentVerification?.universityId);
  if (university && !isAcademicEmail(profile.email, university.id)) {
    return (
      `Personal accounts are for students, and ${profile.email} is not a ${university.name} ` +
      "address. Change your account email to your university address, or join a team " +
      "with an invitation from its admin."
    );
  }
  if (!isAcademicEmail(profile.email, university?.id)) {
    return (
      "Personal accounts are for students. Change your account email to your university " +
      "address and verify it, or join a team with an invitation from its admin."
    );
  }
  // Worded for both front ends: the app's Account menu and `bioaudit verify-student`
  // each offer entering the code and requesting a new one.
  return (
    `Verify your university email to use the free plan. Enter the code sent to ` +
    `${profile.email}, or request a new one.`
  );
}

/**
 * The stored form of a code. `key` is the uid for an account's code and the address for
 * a sign-up code, so a code hash copied from one record never matches in another.
 */
export function hashCode(key, code) {
  return crypto.createHash("sha256").update(`${key}:${code}`).digest("hex");
}

/**
 * Email a fresh code to the account's current address, replacing any earlier one.
 *
 * Returns whether the message actually went out (see issueCode).
 */
export async function sendCode(profile) {
  if (!isRequiredFor(profile)) {
    throw ApiError.badRequest("This account does not need student verification.");
  }
  // Ended for good: a code would verify an address but bring nothing back.
  if (studentAccessEnded(profile)) throw ApiError.forbidden(accessEndedMessage(profile));
  const universityId = profile.studentVerification?.universityId ?? null;
  if (!isAcademicEmail(profile.email, universityId)) {
    throw ApiError.badRequest(
      "Student verification needs a university email address. Change your account email " +
        "to your university address first."
    );
  }
  if (statusFor(profile).status === STUDENT_STATUS.VERIFIED) {
    throw ApiError.conflict("Your student email is already verified.");
  }

  const { delivered } = await issueCode(codes().doc(profile.id), {
    to: profile.email,
    key: profile.id,
    send: studentEmailTo(profile.email, universityId),
  });

  await users()
    .doc(profile.id)
    .update({
      "studentVerification.status": STUDENT_STATUS.PENDING,
      "studentVerification.email": profile.email,
      "studentVerification.verifiedAt": null,
      updatedAt: FieldValue.serverTimestamp(),
    });

  return { delivered, expiresInMinutes: CODE_TTL_MINUTES };
}

/** How a student verification code goes out: the email that names their university. */
function studentEmailTo(to, universityId) {
  const universityName = findUniversity(universityId)?.name;
  return (message) => email.sendStudentVerificationCode({ to, universityName, ...message });
}

/**
 * Store a fresh code at `ref` and email it to `to`, replacing any earlier one.
 *
 * `send({ code, expiresInMinutes, variables })` emails the code and resolves to email
 * service's { sent, recipientNotAllowed }; `kind` names the code in the development log.
 *
 * Resolves to { delivered, sendId }: whether the message actually went out, and the id
 * its bounce report would carry (see recordBounce). With no mail server configured outside
 * production, the code is written to the server log instead, which is what lets the
 * feature be exercised locally without SMTP credentials. A code that could not be
 * delivered is withdrawn and the failure reported, since a student waiting on an email
 * that will never come has no way to tell that from a slow inbox.
 */
export async function issueCode(ref, { to, key, send, kind = "Student verification" }) {
  const existing = await ref.get();
  const sentAt = existing.data()?.sentAt?.toMillis?.() ?? 0;
  const waitSeconds = Math.ceil((sentAt + RESEND_COOLDOWN_SECONDS * 1000 - Date.now()) / 1000);
  if (existing.exists && waitSeconds > 0) {
    throw ApiError.tooManyRequests(
      `A code was sent moments ago. Wait ${waitSeconds} seconds before asking for another.`
    );
  }
  // When each recent code went out, carried from one code to the next. Using a code
  // deletes the record, so only an address that keeps asking without succeeding fills up.
  const now = Date.now();
  const recentSends = (existing.data()?.recentSends ?? []).filter((at) => at > now - DAY_MS);
  if (recentSends.length >= MAX_CODES_PER_DAY) {
    const hours = Math.ceil((Math.min(...recentSends) + DAY_MS - now) / (60 * 60 * 1000));
    throw ApiError.tooManyRequests(
      `${MAX_CODES_PER_DAY} codes have already been sent to ${to} today. Try again in ` +
        `${hours} hour${hours === 1 ? "" : "s"}, or contact the BioAudit team if you need ` +
        "help sooner."
    );
  }

  if (!env.email.enabled && env.isProduction) {
    throw new ApiError(
      503,
      "Email is not set up on this server, so verification codes cannot be sent yet. " +
        "Contact the BioAudit administrator."
    );
  }
  // Only when mail really goes out. Otherwise the code is just logged, and the emulator
  // tests use made-up university domains.
  if (env.email.enabled) await assertDomainReceivesMail(to);

  const code = crypto.randomInt(0, 1_000_000).toString().padStart(6, "0");
  const sendId = crypto.randomBytes(16).toString("hex");

  await ref.set({
    email: to,
    codeHash: hashCode(key, code),
    attempts: 0,
    sendId,
    bounced: false,
    recentSends: [...recentSends, now],
    sentAt: FieldValue.serverTimestamp(),
    expiresAt: Timestamp.fromMillis(Date.now() + CODE_TTL_MINUTES * 60 * 1000),
  });
  let delivered = false;
  if (env.email.enabled) {
    const result = await send({
      code,
      expiresInMinutes: CODE_TTL_MINUTES,
      variables: { codeDoc: ref.path, sendId },
    });
    delivered = result.sent;
    if (!delivered) {
      // Put back whatever was there before. A student who already had a working code
      // keeps it, and one who did not can ask again straight away rather than waiting
      // out the resend cooldown for a code that never left the server.
      if (existing.exists) await ref.set(existing.data()).catch(() => {});
      else await ref.delete().catch(() => {});
      // A Mailgun sandbox only delivers to the recipients authorised in its dashboard.
      // Saying so plainly stops a student hunting for a typo in an address that is fine.
      if (result.recipientNotAllowed) {
        throw new ApiError(
          502,
          `This BioAudit server can only email approved test addresses at the moment, and ` +
            `${to} has not been approved yet. Ask the BioAudit team to add it, ` +
            "then press Send code again."
        );
      }
      // Anything else: a mail service that is briefly down, most likely. The server log
      // has the exact reason.
      throw new ApiError(
        502,
        `We could not send a verification email to ${to}. Check the address ` +
          "is right and try again later. If it keeps failing, contact the BioAudit team."
      );
    }
  } else {
    console.log(`[dev] ${kind} code for ${to}: ${code}`);
  }

  return { delivered, sendId };
}

/** The only documents a bounce report may touch: one code, in either code collection. */
const CODE_DOC_PATH = new RegExp(
  `^(${COLLECTIONS.STUDENT_VERIFICATIONS}|${COLLECTIONS.SIGNUP_VERIFICATIONS})/[A-Za-z0-9]+$`
);

/**
 * Mark a code as bounced, from a Mailgun report that its email could not be delivered.
 *
 * The report names the code document and send it was about (the variables issueCode
 * attached), and is only applied if that document still holds that same send to that
 * same address. A late report about an earlier code therefore never marks a fresh one,
 * and a report cannot be aimed at anything else in the database.
 *
 * Resolves to whether a code was marked.
 */
export async function recordBounce({ codeDoc, sendId, recipient, reason }) {
  if (typeof codeDoc !== "string" || !CODE_DOC_PATH.test(codeDoc) || !sendId) return false;
  const ref = db.doc(codeDoc);

  return db.runTransaction(async (tx) => {
    const snap = await tx.get(ref);
    const data = snap.data();
    if (!snap.exists || data.sendId !== sendId || data.email !== recipient) return false;
    tx.update(ref, { bounced: true, bounceReason: String(reason ?? "").slice(0, 500) });
    return true;
  });
}

/**
 * Send a code if this account needs one and can take one, as after changing email.
 *
 * Best-effort: the account change that triggered it has already happened, and the user
 * can always ask for another code, so a failure here must not fail that request.
 */
export async function sendCodeIfDue(profile) {
  const status = statusFor(profile);
  if (
    !status.required ||
    !status.eligible ||
    status.status === STUDENT_STATUS.VERIFIED ||
    status.status === STUDENT_STATUS.EXPIRED
  ) {
    return false;
  }
  try {
    await sendCode(profile);
    return true;
  } catch (error) {
    console.error(`Could not issue a student verification code: ${error.message}`);
    return false;
  }
}

/**
 * Check a code the user typed in, and mark the account verified if it matches.
 */
export async function verifyCode(profile, code) {
  const ref = codes().doc(profile.id);

  await checkCode(ref, {
    email: profile.email,
    key: profile.id,
    code,
    onMatch(tx) {
      tx.delete(ref);
      tx.update(users().doc(profile.id), {
        "studentVerification.status": STUDENT_STATUS.VERIFIED,
        "studentVerification.email": profile.email,
        "studentVerification.verifiedAt": FieldValue.serverTimestamp(),
        updatedAt: FieldValue.serverTimestamp(),
      });
    },
  });

  // Firebase's own flag agrees with ours, so anything else reading the auth record
  // sees the address as verified too. Not revoking tokens: nothing in them changed.
  await auth.updateUser(profile.id, { emailVerified: true }).catch(() => {});
}

/**
 * Check `code` against the one stored at `ref` for `email`, throwing unless it matches.
 * `onMatch` adds whatever writes go with a right code to the same transaction.
 *
 * The attempt counter is bumped inside the transaction and the refusal thrown after it
 * commits, so a wrong guess is always counted: throwing inside would roll the increment
 * back and give unlimited guesses.
 */
export async function checkCode(ref, { email: address, key, code, onMatch }) {
  const outcome = await db.runTransaction(async (tx) => {
    const snap = await tx.get(ref);
    if (!snap.exists) return "missing";

    const data = snap.data();
    if (data.email !== address) {
      tx.delete(ref);
      return "missing";
    }
    if (data.expiresAt.toMillis() < Date.now()) {
      tx.delete(ref);
      return "expired";
    }
    if (data.attempts >= MAX_ATTEMPTS) return "locked";

    const expected = Buffer.from(data.codeHash, "hex");
    const given = Buffer.from(hashCode(key, code), "hex");
    if (!crypto.timingSafeEqual(expected, given)) {
      tx.update(ref, { attempts: FieldValue.increment(1) });
      return data.attempts + 1 >= MAX_ATTEMPTS ? "locked" : "wrong";
    }

    onMatch(tx);
    return "verified";
  });

  switch (outcome) {
    case "verified":
      return;
    case "missing":
      throw ApiError.badRequest("There is no code waiting for this address. Request a new one.");
    case "expired":
      throw ApiError.badRequest("That code has expired. Request a new one.");
    case "locked":
      throw ApiError.tooManyRequests("Too many incorrect codes. Request a new one.");
    default:
      throw ApiError.badRequest("That code is not correct. Check the email and try again.");
  }
}

/**
 * Record which university the student says they attend, or clear it with null for "my
 * university isn't listed". Changing it lapses any verification, since that was proved
 * against the previous choice.
 */
export async function setUniversity(profile, universityId) {
  assertEmailMatchesUniversity(profile.email, universityId);
  const current = profile.studentVerification?.universityId ?? null;
  if (current === universityId) return;

  await users().doc(profile.id).update({
    studentVerification: {
      status: STUDENT_STATUS.UNVERIFIED,
      email: null,
      verifiedAt: null,
      universityId,
    },
    updatedAt: FieldValue.serverTimestamp(),
  });
  await discardCode(profile.id);
  profile.studentVerification = { status: STUDENT_STATUS.UNVERIFIED, universityId };
}

/** Forget a pending code, for when the address it was sent to is no longer the account's. */
export async function discardCode(uid) {
  await codes().doc(uid).delete().catch(() => {});
}

// --- Sign-up ----------------------------------------------------------------
//
// A personal account cannot be created until its address is proved, so these codes
// belong to an address rather than an account. Each is stored under a hash of the
// address: a document id cannot hold every character an address can, and hashing keeps
// addresses that never became accounts out of the ids.

/** Where an address's sign-up code is kept. Exported for the tests, which plant codes. */
export function signupCodeId(address) {
  return crypto.createHash("sha256").update(address).digest("hex");
}

/**
 * Refuse an address a personal account cannot be made with: one outside the chosen
 * university or, with none chosen, outside every recognised academic domain.
 */
export function assertStudentEmail(address, universityId) {
  assertEmailMatchesUniversity(address, universityId);
  if (!isAcademicEmail(address, universityId)) {
    throw ApiError.badRequest(
      "Personal accounts are for students, so sign up with your university email " +
        "address. Joining a team? Use the invitation its admin sent you instead."
    );
  }
}

async function accountExists(address) {
  try {
    await auth.getUserByEmail(address);
    return true;
  } catch (error) {
    if (error.code === "auth/user-not-found") return false;
    throw error;
  }
}

/**
 * Refuse an address that already has an account. Only ever reached by someone holding
 * the right sign-up code, so only the owner of the address can learn this.
 */
async function assertNoAccount(address) {
  if (await accountExists(address)) {
    throw ApiError.conflict(
      "You already have an account with this email address. Sign in, or use Forgot " +
        "password? on the sign-in screen."
    );
  }
}

/**
 * Email the code someone signs up with, replacing any earlier one for the address.
 * `accountType` is "student" (a university address is required) or "team". The sendId
 * it resolves with is what signupCodeStatus asks about.
 *
 * An address that already has an account is answered exactly like a new one, with the
 * same cooldowns and limits, so sign-up cannot be used to find out who has an account.
 * Its owner is emailed a notice saying they already have one, instead of the code. A
 * code is still stored, so that everything after this behaves the same too, but it is
 * never sent, so it cannot be entered.
 */
export async function sendSignupCode({
  email: address,
  universityId = null,
  accountType = "student",
}) {
  if (accountType === "student") assertStudentEmail(address, universityId);

  let send = studentEmailTo(address, universityId);
  let kind = "Student verification";
  if (await accountExists(address)) {
    send = ({ variables }) => email.sendAccountExistsNotice({ to: address, variables });
    kind = "Sign-up (this address already has an account, so this code was not emailed)";
  } else if (accountType === "team") {
    send = (message) => email.sendTeamSignupCode({ to: address, ...message });
    kind = "Team sign-up";
  }

  const { delivered, sendId } = await issueCode(signupCodes().doc(signupCodeId(address)), {
    to: address,
    key: address,
    send,
    kind,
  });
  return { delivered, sendId, expiresInMinutes: CODE_TTL_MINUTES };
}

/**
 * Whether a sign-up code has bounced: "bounced" once Mailgun reports the address could
 * not receive it, "sent" until then. A client asks every few seconds after sending, so
 * it can say so while the student is still waiting, instead of leaving them waiting.
 *
 * Asking needs the sendId that only the requester of the send was given. Without that,
 * anyone could use this to find out which university mailboxes exist.
 */
export async function signupCodeStatus(address, sendId) {
  const snap = await signupCodes().doc(signupCodeId(address)).get();
  const data = snap.data();
  if (!snap.exists || data.sendId !== sendId || data.email !== address) {
    throw ApiError.notFound("There is no code waiting for this address. Request a new one.");
  }
  return data.bounced ? "bounced" : "sent";
}

/**
 * Check the code someone signs up with, throwing unless it is right.
 *
 * The code is checked before whether the address already has an account, so only the
 * owner of the address, who has the code, can be told that. Anyone else gets the same
 * "wrong code" or "no code" answer as for any other address.
 *
 * A right code is left in place for the caller to discard once the account exists, so a
 * sign-up that fails after this point (a Firebase hiccup, say) can be retried without
 * waiting out the resend cooldown. It can still only ever create the one account: once
 * that exists, assertNoAccount turns the code away.
 */
export async function checkSignupCode(address, code) {
  await checkCode(signupCodes().doc(signupCodeId(address)), {
    email: address,
    key: address,
    code,
    onMatch() {},
  });
  await assertNoAccount(address);
}

export async function discardSignupCode(address) {
  await signupCodes().doc(signupCodeId(address)).delete().catch(() => {});
}
