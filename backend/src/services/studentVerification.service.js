/**
 * Student email verification for the free plan.
 *
 * The free plan exists for students, so a free personal account has to prove it belongs
 * to one before it can run assessments. The proof is the usual one: the account's email
 * address is at a university domain, and its owner can read mail sent there. We email a
 * six-digit code and the owner types it into the desktop app.
 *
 * Who this applies to:
 *   * Free members with no organisation. These are the accounts the free plan is for.
 *   * Not premium accounts, which are paying and need no eligibility check.
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

import { auth, db, FieldValue, Timestamp } from "../config/firebase.js";
import { env } from "../config/env.js";
import { COLLECTIONS, ROLES, STUDENT_STATUS, TIERS } from "../constants/index.js";
import { findUniversity } from "../data/universities.js";
import { ApiError } from "../utils/ApiError.js";
import * as email from "./email.service.js";

const CODE_TTL_MINUTES = 15;
const MAX_ATTEMPTS = 5;
const RESEND_COOLDOWN_SECONDS = 60;

const users = () => db.collection(COLLECTIONS.USERS);
const codes = () => db.collection(COLLECTIONS.STUDENT_VERIFICATIONS);

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

/** Whether this account has to be a verified student to use its plan. */
export function isRequiredFor(profile) {
  return profile.tier !== TIERS.PREMIUM && profile.role !== ROLES.ADMIN && !profile.organisationId;
}

/**
 * The verification state as the client sees it.
 *
 * Verification is tied to the address that was verified, so changing the account's
 * email afterwards makes it unverified again without anything having to remember to
 * reset it.
 */
export function statusFor(profile) {
  const stored = profile.studentVerification ?? {};
  const verified = stored.status === STUDENT_STATUS.VERIFIED && stored.email === profile.email;

  let status = STUDENT_STATUS.UNVERIFIED;
  if (verified) status = STUDENT_STATUS.VERIFIED;
  else if (stored.status === STUDENT_STATUS.PENDING && stored.email === profile.email) {
    status = STUDENT_STATUS.PENDING;
  }

  return {
    status,
    required: isRequiredFor(profile),
    eligible: isAcademicEmail(profile.email, stored.universityId),
    university: publicUniversity(stored.universityId),
    verifiedAt: verified ? (stored.verifiedAt ?? null) : null,
  };
}

/** Whether the account may use the features the free plan gates behind verification. */
export function isSatisfied(profile) {
  return !isRequiredFor(profile) || statusFor(profile).status === STUDENT_STATUS.VERIFIED;
}

/** The message shown when an unverified student tries a gated feature. */
export function refusalMessage(profile) {
  const university = findUniversity(profile.studentVerification?.universityId);
  if (university && !isAcademicEmail(profile.email, university.id)) {
    return (
      `The free plan is for students, and ${profile.email} is not a ${university.name} ` +
      "address. Change your account email to your university address, or upgrade to premium."
    );
  }
  if (!isAcademicEmail(profile.email, university?.id)) {
    return (
      "The free plan is for students. Change your account email to your university " +
      "address and verify it, or upgrade to premium."
    );
  }
  // Worded for both front ends: the app's Account menu and `bioaudit verify-student`
  // each offer entering the code and requesting a new one.
  return (
    `Verify your university email to use the free plan. Enter the code sent to ` +
    `${profile.email}, or request a new one.`
  );
}

export function hashCode(uid, code) {
  return crypto.createHash("sha256").update(`${uid}:${code}`).digest("hex");
}

/**
 * Email a fresh code to the account's current address, replacing any earlier one.
 *
 * Returns whether the message actually went out. With no mail server configured outside
 * production, the code is written to the server log instead, which is what lets the
 * feature be exercised locally without SMTP credentials. A code that could not be
 * delivered is withdrawn and the failure reported, since a student waiting on an email
 * that will never come has no way to tell that from a slow inbox.
 */
export async function sendCode(profile) {
  if (!isRequiredFor(profile)) {
    throw ApiError.badRequest("This account does not need student verification.");
  }
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

  const ref = codes().doc(profile.id);
  const existing = await ref.get();
  const sentAt = existing.data()?.sentAt?.toMillis?.() ?? 0;
  const waitSeconds = Math.ceil((sentAt + RESEND_COOLDOWN_SECONDS * 1000 - Date.now()) / 1000);
  if (existing.exists && waitSeconds > 0) {
    throw ApiError.tooManyRequests(
      `A code was sent moments ago. Wait ${waitSeconds} seconds before asking for another.`
    );
  }

  if (!env.email.enabled && env.isProduction) {
    throw new ApiError(
      503,
      "Email is not set up on this server, so verification codes cannot be sent yet. " +
        "Contact the BioAudit administrator."
    );
  }

  const code = crypto.randomInt(0, 1_000_000).toString().padStart(6, "0");

  await ref.set({
    email: profile.email,
    codeHash: hashCode(profile.id, code),
    attempts: 0,
    sentAt: FieldValue.serverTimestamp(),
    expiresAt: Timestamp.fromMillis(Date.now() + CODE_TTL_MINUTES * 60 * 1000),
  });
  let delivered = false;
  if (env.email.enabled) {
    const result = await email.sendStudentVerificationCode({
      to: profile.email,
      code,
      expiresInMinutes: CODE_TTL_MINUTES,
      universityName: findUniversity(universityId)?.name,
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
            `${profile.email} has not been approved yet. Ask the BioAudit team to add it, ` +
            "then press Send code again."
        );
      }
      // Anything else: a mail service that is briefly down, most likely. The server log
      // has the exact reason.
      throw new ApiError(
        502,
        `We could not send a verification email to ${profile.email}. Check the address ` +
          "is right and try again later. If it keeps failing, contact the BioAudit team."
      );
    }
  } else {
    console.log(`[dev] Student verification code for ${profile.email}: ${code}`);
  }

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

/**
 * Send a code if this account needs one and can take one, as after registering or
 * changing email.
 *
 * Best-effort: the account change that triggered it has already happened, and the user
 * can always ask for another code, so a failure here must not fail that request.
 */
export async function sendCodeIfDue(profile) {
  const status = statusFor(profile);
  if (!status.required || !status.eligible || status.status === STUDENT_STATUS.VERIFIED) {
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
 *
 * The attempt counter is bumped inside the transaction and the refusal thrown after it
 * commits, so a wrong guess is always counted: throwing inside would roll the increment
 * back and give unlimited guesses.
 */
export async function verifyCode(profile, code) {
  const ref = codes().doc(profile.id);
  const userRef = users().doc(profile.id);

  const outcome = await db.runTransaction(async (tx) => {
    const snap = await tx.get(ref);
    if (!snap.exists) return "missing";

    const data = snap.data();
    if (data.email !== profile.email) {
      tx.delete(ref);
      return "missing";
    }
    if (data.expiresAt.toMillis() < Date.now()) {
      tx.delete(ref);
      return "expired";
    }
    if (data.attempts >= MAX_ATTEMPTS) return "locked";

    const expected = Buffer.from(data.codeHash, "hex");
    const given = Buffer.from(hashCode(profile.id, code), "hex");
    if (!crypto.timingSafeEqual(expected, given)) {
      tx.update(ref, { attempts: FieldValue.increment(1) });
      return data.attempts + 1 >= MAX_ATTEMPTS ? "locked" : "wrong";
    }

    tx.delete(ref);
    tx.update(userRef, {
      "studentVerification.status": STUDENT_STATUS.VERIFIED,
      "studentVerification.email": profile.email,
      "studentVerification.verifiedAt": FieldValue.serverTimestamp(),
      updatedAt: FieldValue.serverTimestamp(),
    });
    return "verified";
  });

  switch (outcome) {
    case "verified":
      // Firebase's own flag agrees with ours, so anything else reading the auth record
      // sees the address as verified too. Not revoking tokens: nothing in them changed.
      await auth.updateUser(profile.id, { emailVerified: true }).catch(() => {});
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
