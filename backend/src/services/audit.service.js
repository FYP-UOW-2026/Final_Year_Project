/**
 * Append-only record of actions that affect someone other than the person doing them.
 *
 * Admin powers in this system include reading a member's scan data and deleting their
 * account, so those actions need to leave a trace. Nothing in the API updates or
 * deletes an audit entry, and the Firestore rules deny writes from clients entirely.
 */
import { db, FieldValue } from "../config/firebase.js";
import { COLLECTIONS } from "../constants/index.js";

export const AUDIT_ACTIONS = {
  MEMBER_INVITED: "member.invited",
  INVITATION_CANCELLED: "member.invitation_cancelled",
  MEMBER_JOINED: "member.joined",
  MEMBER_REMOVED: "member.removed",
  MEMBER_DATA_VIEWED: "member.data_viewed",
  MEMBER_DATA_FLAGGED: "member.data_flagged",
  FLAG_REVIEWED: "flag.reviewed",
  MEMBER_ACCOUNT_DELETED: "member.account_deleted",
  MEMBER_SUSPENDED: "member.suspended",
  MEMBER_REINSTATED: "member.reinstated",
  ADMIN_ADDED: "admin.added",
  TIER_CHANGED: "subscription.tier_changed",
};

/**
 * @param {object} entry
 * @param {string} entry.action - one of AUDIT_ACTIONS
 * @param {string} entry.actorId - who performed it
 * @param {string} [entry.subjectId] - who or what it was performed on
 * @param {string} [entry.organisationId]
 * @param {object} [entry.metadata] - anything else worth keeping, minus secrets
 */
export async function record({ action, actorId, subjectId, organisationId, metadata }) {
  await db.collection(COLLECTIONS.AUDIT).add({
    action,
    actorId,
    subjectId: subjectId ?? null,
    organisationId: organisationId ?? null,
    metadata: metadata ?? {},
    at: FieldValue.serverTimestamp(),
  });
}

/** Read an organisation's audit trail, newest first. Admin only. */
export async function listForOrganisation(organisationId, { limit = 100, action } = {}) {
  let query = db
    .collection(COLLECTIONS.AUDIT)
    .where("organisationId", "==", organisationId)
    .orderBy("at", "desc")
    .limit(limit);

  if (action) query = query.where("action", "==", action);

  const snap = await query.get();
  return withPeople(snap.docs.map((doc) => ({ id: doc.id, ...doc.data() })));
}

/**
 * Name the people in each entry by email, so the log reads "who did what to whom".
 *
 * Entries store uids, which mean nothing to a reader. They are resolved at read time, so
 * an entry shows the address an account has now; for an account since deleted there is
 * no profile left, and the email captured in the entry's metadata (if any) stands in.
 */
async function withPeople(entries) {
  const uids = [...new Set(entries.flatMap((e) => [e.actorId, e.subjectId]).filter(Boolean))];
  const emails = new Map();
  if (uids.length) {
    const refs = uids.map((uid) => db.collection(COLLECTIONS.USERS).doc(uid));
    for (const snap of await db.getAll(...refs)) {
      if (snap.exists) emails.set(snap.id, snap.data().email ?? null);
    }
  }
  return entries.map((e) => ({
    ...e,
    actorEmail: emails.get(e.actorId) ?? null,
    subjectEmail: emails.get(e.subjectId) ?? e.metadata?.email ?? null,
  }));
}
