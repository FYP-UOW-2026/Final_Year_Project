/**
 * Registration and sign-in.
 *
 * Diagram coverage:
 *   Unregistered User  -> Register user account
 *   Admin              -> Register admin account
 *   User               -> Log in, Log out
 */
import { Router } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";

import { env } from "../config/env.js";
import { auth } from "../config/firebase.js";
import { ROLES, TOS_VERSION } from "../constants/index.js";
import { requireAuth } from "../middleware/auth.js";
import { validate } from "../middleware/validate.js";
import * as audit from "../services/audit.service.js";
import * as firebaseAuth from "../services/firebaseAuth.service.js";
import * as organisations from "../services/organisations.service.js";
import * as studentVerification from "../services/studentVerification.service.js";
import * as usersService from "../services/users.service.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";

const router = Router();

/**
 * Sign-in and registration are the two endpoints worth guessing at, so they get a
 * tighter limit than the rest of the API. Without this, an attacker could try passwords
 * as fast as the network allows.
 */
const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: env.rateLimits.authPer15Min,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: { status: 429, message: "Too many attempts. Try again in a few minutes." } },
});

const emailSchema = z.string().trim().toLowerCase().email("Enter a valid email address.");
// Registration is refused without it; the accepted version and time are stored on the
// user doc so a later terms change can ask existing users to accept again.
const tosSchema = z.literal(true, {
  errorMap: () => ({ message: "You must accept the Terms of Use to create an account." }),
});
const passwordSchema = z
  .string()
  .min(8, "Use at least 8 characters.")
  .max(128, "That password is too long.");
// From GET /api/universities. Omitted for "my university isn't listed".
const universityIdSchema = z.string().trim().min(1).max(60).optional();

/**
 * POST /api/auth/register/send-code -- the first step of registering a student
 *
 * Emails the six-digit code that POST /auth/register needs. The address must be one the
 * chosen university issues (or, with none chosen, at a recognised academic domain) and
 * must not have an account yet. Public, since there is no account yet, so it shares the
 * sign-in limit; each address can also be sent a code only once a minute.
 */
router.post(
  "/register/send-code",
  authLimiter,
  validate({ body: z.object({ email: emailSchema, universityId: universityIdSchema }) }),
  asyncHandler(async (req, res) => {
    const { delivered, sendId, expiresInMinutes } = await studentVerification.sendSignupCode(
      req.body
    );
    res.json({
      // Names the address so a typo in it is easy to spot. Delivery is only confirmed by
      // the student receiving the code, so this is the one chance to catch one.
      message: delivered
        ? `A verification code has been sent to ${req.body.email}. It expires in ` +
          `${expiresInMinutes} minutes. Not there? Check that address is spelled right, ` +
          "then send again."
        : "Email is not set up on this server, so the code was written to the server's " +
          "log instead.",
      delivered,
      expiresInMinutes,
      // For POST /auth/register/code-status, to hear whether this email bounced.
      sendId,
    });
  })
);

/**
 * POST /api/auth/register/code-status -- has the sign-up code bounced?
 *
 * Whether an address exists is only known once its mail server answers, after the code
 * has gone. A client asks this every few seconds for a couple of minutes after sending,
 * and tells the student if the address turned out not to exist, rather than leaving them
 * waiting for a code that is never coming. Bounces are reported by Mailgun's webhook
 * (see webhooks.routes.js); with that not set up, every code reads as "sent".
 *
 * Only the general rate limit applies, since polling is the point. Answering needs the
 * sendId from send-code, so it tells nobody but the requester anything.
 */
router.post(
  "/register/code-status",
  validate({
    body: z.object({
      email: emailSchema,
      sendId: z.string().trim().regex(/^[a-f0-9]{32}$/, "That send id is not valid."),
    }),
  }),
  asyncHandler(async (req, res) => {
    const status = await studentVerification.signupCodeStatus(req.body.email, req.body.sendId);
    res.json(
      status === "bounced"
        ? {
            status,
            message:
              `${req.body.email} could not receive the code: its mail server turned it ` +
              "away, which usually means the address does not exist. Check it for typos, " +
              "then send a new code.",
          }
        : { status }
    );
  })
);

/**
 * POST /api/auth/register -- Register user account
 *
 * A personal account is for a student, and has to prove it before it exists: the
 * address must be a university one, and the request must carry the code that
 * POST /auth/register/send-code emailed to it. The account is created already verified.
 *
 * The one other way in is an invitation from a team's admin, whose token is redeemed in
 * the same request. The team sponsors that account, so its address can be anything.
 */
router.post(
  "/register",
  authLimiter,
  validate({
    body: z.object({
      email: emailSchema,
      password: passwordSchema,
      tosAccepted: tosSchema,
      displayName: z.string().trim().min(1).max(80).optional(),
      universityId: universityIdSchema,
      code: z
        .string()
        .trim()
        .regex(/^\d{6}$/, "Enter the six-digit code from the email.")
        .optional(),
      invitationToken: z.string().trim().min(16, "That invitation link is not valid.").optional(),
    }),
  }),
  asyncHandler(async (req, res) => {
    if (req.body.invitationToken) return registerInvitee(req, res);

    // Checked before the account exists, so a wrong address or code is a form error
    // rather than an account that can never be verified.
    studentVerification.assertStudentEmail(req.body.email, req.body.universityId);
    if (!req.body.code) {
      throw ApiError.badRequest(
        "Verify your university email first: request a code, then enter the six-digit " +
          "code from the email."
      );
    }
    await studentVerification.checkSignupCode(req.body.email, req.body.code);

    const profile = await usersService.createAccount({
      email: req.body.email,
      password: req.body.password,
      displayName: req.body.displayName,
      role: ROLES.MEMBER,
      tosVersion: TOS_VERSION,
      universityId: req.body.universityId ?? null,
      studentVerified: true,
    });
    await studentVerification.discardSignupCode(req.body.email);

    const session = await firebaseAuth.signInWithPassword(req.body.email, req.body.password);
    const finalProfile = await usersService.getProfile(profile.uid);

    res.status(201).json({ user: usersService.toPublicProfile(finalProfile), session });
  })
);

/**
 * Registration for someone a team's admin invited: the account is created and joins
 * the team in one step. The token is checked before anything is created, and the account
 * is removed again if redeeming it still fails (say it was cancelled in the meantime),
 * so a bad invitation never leaves behind a personal account with nothing to join.
 */
async function registerInvitee(req, res) {
  const token = req.body.invitationToken;
  await organisations.checkInvitation(token);

  const profile = await usersService.createAccount({
    email: req.body.email,
    password: req.body.password,
    displayName: req.body.displayName,
    role: ROLES.MEMBER,
    tosVersion: TOS_VERSION,
  });

  let joined;
  try {
    joined = await organisations.acceptInvitation({ token, uid: profile.uid });
  } catch (error) {
    await usersService.deleteAccount(profile.uid).catch(() => {});
    throw error;
  }

  await audit.record({
    action: audit.AUDIT_ACTIONS.MEMBER_JOINED,
    actorId: profile.uid,
    subjectId: profile.uid,
    organisationId: joined.organisationId,
    metadata: { role: joined.role },
  });

  // Joining revoked the account's tokens to make way for the team's claims, so signing
  // in only now gives a session that already carries them.
  const session = await firebaseAuth.signInWithPassword(req.body.email, req.body.password);
  const finalProfile = await usersService.getProfile(profile.uid);

  res.status(201).json({
    user: usersService.toPublicProfile(finalProfile),
    organisation: { id: joined.organisationId, name: joined.organisationName },
    session,
  });
}

/**
 * POST /api/auth/register-admin -- Register admin account
 *
 * An admin account only means anything alongside an organisation to administer, so this
 * creates both together and makes the new account its owner.
 */
router.post(
  "/register-admin",
  authLimiter,
  validate({
    body: z.object({
      email: emailSchema,
      password: passwordSchema,
      tosAccepted: tosSchema,
      displayName: z.string().trim().min(1).max(80).optional(),
      organisationName: z.string().trim().min(2, "Give your organisation a name.").max(120),
      // A freelancer's one-person team: its owner may also run scans (see denyAdmin).
      solo: z.boolean().optional(),
    }),
  }),
  asyncHandler(async (req, res) => {
    const profile = await usersService.createAccount({
      email: req.body.email,
      password: req.body.password,
      displayName: req.body.displayName,
      role: ROLES.ADMIN,
      tosVersion: TOS_VERSION,
    });

    const organisation = await organisations.createOrganisation({
      name: req.body.organisationName,
      ownerId: profile.uid,
      solo: req.body.solo,
    });

    // The organisation id only exists once the organisation has been created, so the
    // account is attached to it here rather than at creation time.
    await usersService.setOrganisation(profile.uid, {
      organisationId: organisation.id,
      role: ROLES.ADMIN,
    });

    const session = await firebaseAuth.signInWithPassword(req.body.email, req.body.password);
    const finalProfile = await usersService.getProfile(profile.uid);

    res.status(201).json({
      user: usersService.toPublicProfile(finalProfile),
      organisation,
      session,
    });
  })
);

/** POST /api/auth/login -- Log in */
router.post(
  "/login",
  authLimiter,
  validate({ body: z.object({ email: emailSchema, password: z.string().min(1) }) }),
  asyncHandler(async (req, res) => {
    const session = await firebaseAuth.signInWithPassword(req.body.email, req.body.password);
    const profile = await usersService.getProfile(session.uid);

    if (profile.disabled) {
      // Checked after the password so a suspended account gives nothing away to
      // someone who does not already know the password.
      return res.status(403).json({
        error: { status: 403, message: "This account has been suspended." },
      });
    }

    res.json({ user: usersService.toPublicProfile(profile), session });
  })
);

/** POST /api/auth/refresh -- keep a signed-in session alive */
router.post(
  "/refresh",
  validate({ body: z.object({ refreshToken: z.string().min(1) }) }),
  asyncHandler(async (req, res) => {
    const session = await firebaseAuth.refreshIdToken(req.body.refreshToken);
    res.json({ session });
  })
);

/**
 * POST /api/auth/logout -- Log out
 *
 * Revoking the refresh tokens is what makes this more than a client-side action: the
 * current ID token stops being accepted immediately, because requireAuth checks for
 * revocation on every call.
 */
router.post(
  "/logout",
  requireAuth,
  asyncHandler(async (req, res) => {
    await auth.revokeRefreshTokens(req.user.uid);
    res.json({ message: "Signed out." });
  })
);

export default router;
