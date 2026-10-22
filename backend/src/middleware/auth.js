/**
 * Authentication and authorisation.
 *
 * The desktop app signs in once and then sends the Firebase ID token as a Bearer
 * header on every request. We verify that token here and attach the caller to req.user.
 *
 * Role and organisation live in the token as custom claims, so the common path needs no
 * database read; the tier follows from the organisation. They are refreshed on the token
 * whenever they change (see services/users.service.js), and any endpoint that must not
 * act on a stale claim reads the user document instead.
 */
import { auth, db } from "../config/firebase.js";
import { COLLECTIONS, ROLES, TIERS, tierFor } from "../constants/index.js";
import * as studentVerification from "../services/studentVerification.service.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";

function readBearerToken(req) {
  const header = req.headers.authorization || "";
  if (!header.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length).trim();
  return token || null;
}

/** Rejects the request unless it carries a valid, unrevoked Firebase ID token. */
export const requireAuth = asyncHandler(async (req, res, next) => {
  const token = readBearerToken(req);
  if (!token) {
    throw ApiError.unauthorized("Send a Firebase ID token as 'Authorization: Bearer <token>'.");
  }

  let decoded;
  try {
    // checkRevoked makes a logout or a forced sign-out take effect immediately,
    // rather than lingering until the token would have expired on its own.
    decoded = await auth.verifyIdToken(token, true);
  } catch (error) {
    if (error.code === "auth/id-token-expired") {
      throw ApiError.unauthorized("Your session has expired. Refresh the token and retry.");
    }
    if (error.code === "auth/id-token-revoked") {
      throw ApiError.unauthorized("Your session was ended. Sign in again.");
    }
    throw ApiError.unauthorized("Invalid authentication token.");
  }

  req.user = {
    uid: decoded.uid,
    email: decoded.email || null,
    emailVerified: Boolean(decoded.email_verified),
    role: decoded.role || ROLES.MEMBER,
    organisationId: decoded.organisationId || null,
  };
  req.user.tier = tierFor(req.user);

  next();
});

/** Loads the caller's Firestore profile onto req.profile. Use when claims are not enough. */
export const loadProfile = asyncHandler(async (req, res, next) => {
  const snap = await db.collection(COLLECTIONS.USERS).doc(req.user.uid).get();
  if (!snap.exists) {
    throw ApiError.notFound("Your account record is missing. Contact support.");
  }
  const profile = { id: snap.id, ...snap.data() };
  if (profile.disabled) {
    throw ApiError.forbidden("This account has been suspended.");
  }
  req.profile = profile;
  // Claims can lag behind a change made moments ago, so prefer the stored values.
  req.user.role = profile.role || req.user.role;
  req.user.organisationId = profile.organisationId ?? req.user.organisationId;
  req.user.tier = tierFor(req.user);
  next();
});

/** Gate for team-only features: Compare scan results, Export scan report. */
export const requireTeam = asyncHandler(async (req, res, next) => {
  if (req.user.tier !== TIERS.TEAM) {
    throw ApiError.forbidden(
      "This feature is for team accounts. Join a team with an invitation from its admin to use it."
    );
  }
  next();
});

/**
 * Gate for the free plan's assessment features: a free personal account must have
 * verified a university email first. Needs loadProfile, since the verification state
 * is not carried in the token.
 *
 * Reading and deleting existing history is deliberately left ungated, so an account
 * whose verification lapsed (say, after changing email) never loses sight of its data.
 */
export const requireVerifiedStudent = asyncHandler(async (req, res, next) => {
  if (!studentVerification.isSatisfied(req.profile)) {
    throw ApiError.forbidden(studentVerification.refusalMessage(req.profile));
  }
  next();
});

/**
 * Blocks an admin from the assessment features.
 *
 * An admin account is an oversight role, not a personal one: it exists to supervise a
 * team's assessments, and giving it scanning powers of its own would put the supervisor
 * inside the set of people being supervised. Someone who needs to do both keeps a
 * separate personal account, which is how admin accounts were always meant to work.
 *
 * The one exception is a solo organisation (a freelancer's one-person team) while it still
 * has a single member: there is nobody else to supervise, so the owner may scan. Once a
 * second person joins, the exception ends and the usual separation applies.
 */
export const denyAdmin = asyncHandler(async (req, res, next) => {
  if (req.user.role === ROLES.ADMIN) {
    if (await isSoloOrganisation(req.user.organisationId)) return next();
    throw ApiError.forbidden(
      "Admin accounts oversee a team's assessments rather than running their own. " +
        "Use a personal account to scan an app."
    );
  }
  next();
});

async function isSoloOrganisation(organisationId) {
  if (!organisationId) return false;
  const snap = await db.collection(COLLECTIONS.ORGANISATIONS).doc(organisationId).get();
  const org = snap.exists ? snap.data() : null;
  return Boolean(org?.solo) && (org.memberIds?.length ?? 0) <= 1;
}

/** Gate for organisation admin features. */
export const requireAdmin = asyncHandler(async (req, res, next) => {
  if (req.user.role !== ROLES.ADMIN) {
    throw ApiError.forbidden("Only an organisation admin can do that.");
  }
  if (!req.user.organisationId) {
    throw ApiError.forbidden("Your admin account is not attached to an organisation.");
  }
  next();
});

/**
 * Confirms the admin is acting on their own organisation.
 *
 * Without this an admin could pass another organisation's id in the URL and manage
 * members who are not theirs, which is the most likely way this API could be misused.
 */
export const requireOwnOrganisation = asyncHandler(async (req, res, next) => {
  const { orgId } = req.params;
  if (orgId && orgId !== req.user.organisationId) {
    throw ApiError.forbidden("You can only manage your own organisation.");
  }
  next();
});
