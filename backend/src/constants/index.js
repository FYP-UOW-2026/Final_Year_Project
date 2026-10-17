/** Shared vocabulary. Keeping these in one place stops string typos from becoming bugs. */

export const COLLECTIONS = {
  USERS: "users",
  ORGANISATIONS: "organisations",
  INVITATIONS: "invitations",
  SCANS: "scans",
  REPORTS: "reports",
  FLAGS: "flags",
  AUDIT: "auditLog",
  STUDENT_VERIFICATIONS: "studentVerifications",
};

/**
 * Which features a signed-in user may reach. There is no paid plan: an account on a
 * team (admin or member) has everything, and a personal student account keeps the
 * limited history, no compare or export, and a monthly cap on AI explanations.
 */
export const TIERS = {
  STUDENT: "student",
  TEAM: "team",
};

/**
 * An account's tier, derived from team membership rather than stored, so joining or
 * leaving a team changes it with nothing else to keep in step.
 */
export function tierFor({ organisationId } = {}) {
  return organisationId ? TIERS.TEAM : TIERS.STUDENT;
}

/** Role within an organisation. */
export const ROLES = {
  MEMBER: "member",
  ADMIN: "admin",
};

/** Where a free account is in proving it belongs to a student. */
export const STUDENT_STATUS = {
  UNVERIFIED: "unverified",
  PENDING: "pending",
  VERIFIED: "verified",
};

/** Bump when docs/TERMS.md changes materially; stored with each acceptance. */
export const TOS_VERSION = "2026-10";

export const SCAN_TYPES = {
  APK: "apk",
  DEVICE: "device",
};

export const INVITATION_STATUS = {
  PENDING: "pending",
  ACCEPTED: "accepted",
  CANCELLED: "cancelled",
  EXPIRED: "expired",
};

export const FLAG_STATUS = {
  OPEN: "open",
  REVIEWED: "reviewed",
  DISMISSED: "dismissed",
};

/** Ordered so a report can sort most serious first. */
export const SEVERITIES = ["critical", "high", "medium", "low", "info"];

export const INVITATION_TTL_DAYS = 14;
