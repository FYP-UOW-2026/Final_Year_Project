/**
 * Scan history.
 *
 * The scanning itself happens in the desktop app, because the APK file and the
 * USB-connected phone are both on the user's own machine. This service stores what the
 * app found, serves it back as history, and enforces the free tier's retention limit.
 */
import { db, FieldValue } from "../config/firebase.js";
import { COLLECTIONS, SEVERITIES, TIERS } from "../constants/index.js";
import { env } from "../config/env.js";
import { ApiError } from "../utils/ApiError.js";

const scans = () => db.collection(COLLECTIONS.SCANS);
const reports = () => db.collection(COLLECTIONS.REPORTS);

// Kept under Firestore's 500-writes-per-batch ceiling with room for a counter update.
const BATCH_SIZE = 400;

/** Count findings by severity so the history list does not have to load every finding. */
function countBySeverity(findings) {
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  for (const finding of findings) {
    const key = String(finding.severity || "").toLowerCase();
    if (key in counts) counts[key] += 1;
  }
  return counts;
}

/**
 * Trim a free account back to its retention limit, oldest first.
 *
 * Doing this on save rather than on read means the stored data always matches what the
 * user can see, so an upgrade does not suddenly reveal scans they thought were gone.
 */
async function enforceRetention(userId, tier) {
  if (tier === TIERS.PREMIUM) return { removed: 0 };

  const limit = env.limits.freeHistory;
  const snap = await scans().where("userId", "==", userId).orderBy("createdAt", "desc").get();
  if (snap.size <= limit) return { removed: 0 };

  // Chunked, since an account just downgraded from premium can be hundreds of scans over
  // the limit and a single batch is capped at 500 writes. Each chunk also takes its own
  // scans off scanCount, so the counter stays true even if a later chunk fails.
  const excess = snap.docs.slice(limit);
  const userRef = db.collection(COLLECTIONS.USERS).doc(userId);
  for (let i = 0; i < excess.length; i += BATCH_SIZE) {
    const chunk = excess.slice(i, i + BATCH_SIZE);
    const batch = db.batch();
    chunk.forEach((doc) => batch.delete(doc.ref));
    batch.update(userRef, { scanCount: FieldValue.increment(-chunk.length) });
    await batch.commit();
  }
  await deleteReportsForScans(excess.map((doc) => doc.id));
  return { removed: excess.length };
}

/** Delete every document a query matches, in batches. */
async function deleteAll(query) {
  let removed = 0;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const snap = await query.limit(BATCH_SIZE).get();
    if (snap.empty) break;
    const batch = db.batch();
    snap.docs.forEach((doc) => batch.delete(doc.ref));
    await batch.commit();
    removed += snap.size;
    if (snap.size < BATCH_SIZE) break;
  }
  return removed;
}

/**
 * Remove the saved report exports of these scans.
 *
 * An export holds the full report HTML, findings and evidence included, so a scan that
 * is deleted without its exports has not really been deleted.
 */
async function deleteReportsForScans(scanIds) {
  // Firestore limits an 'in' query to 30 values.
  for (let i = 0; i < scanIds.length; i += 30) {
    await deleteAll(reports().where("scanId", "in", scanIds.slice(i, i + 30)));
  }
}

/** Remove every saved report export belonging to a user. */
export async function deleteReportsForUser(userId) {
  await deleteAll(reports().where("userId", "==", userId));
}

export async function createScan(user, payload) {
  const findings = payload.findings ?? [];

  const doc = {
    userId: user.uid,
    organisationId: user.organisationId ?? null,
    type: payload.type,
    target: {
      packageName: payload.target?.packageName ?? null,
      apkFileName: payload.target?.apkFileName ?? null,
      deviceSerial: payload.target?.deviceSerial ?? null,
    },
    scope: {
      deviceModel: payload.scope?.deviceModel ?? null,
      appVersion: payload.scope?.appVersion ?? null,
      androidVersion: payload.scope?.androidVersion ?? null,
      loginState: payload.scope?.loginState ?? null,
      testsPerformed: payload.scope?.testsPerformed ?? [],
    },
    toolVersion: payload.toolVersion ?? null,
    authorisationConfirmed: payload.authorisationConfirmed === true,
    findings,
    counts: countBySeverity(findings),
    findingCount: findings.length,
    flagged: false,
    startedAt: payload.startedAt ? new Date(payload.startedAt) : null,
    finishedAt: payload.finishedAt ? new Date(payload.finishedAt) : null,
    createdAt: FieldValue.serverTimestamp(),
  };

  // A device assessment probes a live app, so the authorisation confirmation is not
  // optional. Refusing it here means the record can never show an unconfirmed run.
  if (doc.type === "device" && !doc.authorisationConfirmed) {
    throw ApiError.badRequest(
      "A device assessment must confirm you own or are authorised to test the app."
    );
  }

  const ref = await scans().add(doc);
  await db
    .collection(COLLECTIONS.USERS)
    .doc(user.uid)
    .update({ scanCount: FieldValue.increment(1), updatedAt: FieldValue.serverTimestamp() });

  const retention = await enforceRetention(user.uid, user.tier);

  return { id: ref.id, ...doc, retention };
}

/** History list. Findings are omitted so the response stays small. */
export async function listScans(userId, { limit = 50, type } = {}) {
  let query = scans().where("userId", "==", userId).orderBy("createdAt", "desc").limit(limit);
  if (type) query = query.where("type", "==", type);

  const snap = await query.get();
  return snap.docs.map((doc) => {
    const { findings, ...rest } = doc.data();
    return { id: doc.id, ...rest };
  });
}

export async function getScan(scanId) {
  const snap = await scans().doc(scanId).get();
  if (!snap.exists) throw ApiError.notFound("That scan does not exist.");
  return { id: snap.id, ...snap.data() };
}

/**
 * Fetch a scan the caller is allowed to see.
 *
 * A member sees only their own. An admin may also read a scan belonging to a member of
 * their organisation, which is what the Admin "View member data" function needs, and
 * that read is written to the audit log by the route that calls it.
 *
 * "A member" means a current one. A scan keeps the organisationId it was saved under, so
 * without the membership check an admin could go on reading a person's history after
 * removing them, or after they left.
 */
export async function getScanForCaller(scanId, user) {
  const scan = await getScan(scanId);

  if (scan.userId === user.uid) return scan;

  const isAdminOfSameOrg =
    user.role === "admin" &&
    user.organisationId &&
    scan.organisationId === user.organisationId &&
    (await isCurrentMember(user.organisationId, scan.userId));

  if (!isAdminOfSameOrg) {
    throw ApiError.forbidden("You do not have access to that scan.");
  }
  return scan;
}

/** Whether a user is in an organisation's member list right now. */
export async function isCurrentMember(organisationId, uid) {
  const snap = await db.collection(COLLECTIONS.ORGANISATIONS).doc(organisationId).get();
  return snap.exists && (snap.data().memberIds ?? []).includes(uid);
}

/** Persist an AI explanation onto the finding it belongs to. */
export async function saveFindingExplanation(scanId, user, findingIndex, payload) {
  const scan = await getScanForCaller(scanId, user);
  if (scan.userId !== user.uid) {
    throw ApiError.forbidden("You can only save explanations for your own scans.");
  }

  const findings = [...(scan.findings ?? [])];
  const finding = findings[findingIndex];
  if (!finding) throw ApiError.notFound("That finding does not exist in this scan.");

  const saved = {
    explanation: payload.explanation,
    mitigation: payload.mitigation,
    references: payload.references ?? [],
  };

  findings[findingIndex] = { ...finding, ...saved };
  await scans().doc(scan.id).update({
    findings,
    updatedAt: FieldValue.serverTimestamp(),
  });

  return saved;
}

/** Record a report export (HTML/PDF) against its scan. */
export async function saveReportExport(scanId, user, payload = {}) {
  const scan = await getScanForCaller(scanId, user);
  if (scan.userId !== user.uid) {
    throw ApiError.forbidden("You can only save reports for your own scans.");
  }

  const doc = {
    userId: user.uid,
    organisationId: user.organisationId ?? null,
    scanId: scan.id,
    format: payload.format ?? "html",
    fileName: payload.fileName ?? null,
    html: payload.html ?? null,
    createdAt: FieldValue.serverTimestamp(),
  };

  const ref = await reports().add(doc);
  return { id: ref.id, ...doc };
}

export async function deleteScan(scanId, userId) {
  const scan = await getScan(scanId);
  if (scan.userId !== userId) {
    throw ApiError.forbidden("You can only delete your own scans.");
  }
  await scans().doc(scanId).delete();
  await db
    .collection(COLLECTIONS.USERS)
    .doc(userId)
    .update({ scanCount: FieldValue.increment(-1) });
  await deleteReportsForScans([scanId]);
}

/** Clear an entire history. Batched, since a heavy user can exceed one write batch. */
export async function deleteAllScans(userId) {
  const removed = await deleteAll(scans().where("userId", "==", userId));
  await deleteReportsForUser(userId);
  await db.collection(COLLECTIONS.USERS).doc(userId).update({ scanCount: 0 });
  return removed;
}

/**
 * Compare two scans of the same app. Premium only.
 *
 * Grouping by category and component rather than by the finding's id is what makes this
 * useful: the desktop app generates a fresh id per run, so matching on id would report
 * every finding as both new and resolved.
 */
export async function compareScans(scanA, scanB) {
  const key = (f) => `${f.category}::${f.component ?? ""}`;
  const mapA = new Map((scanA.findings ?? []).map((f) => [key(f), f]));
  const mapB = new Map((scanB.findings ?? []).map((f) => [key(f), f]));

  const resolved = [];
  const introduced = [];
  const unchanged = [];

  for (const [k, finding] of mapA) {
    if (mapB.has(k)) unchanged.push(finding);
    else resolved.push(finding);
  }
  for (const [k, finding] of mapB) {
    if (!mapA.has(k)) introduced.push(finding);
  }

  return {
    baseline: { id: scanA.id, createdAt: scanA.createdAt, counts: scanA.counts },
    current: { id: scanB.id, createdAt: scanB.createdAt, counts: scanB.counts },
    summary: {
      resolved: resolved.length,
      introduced: introduced.length,
      unchanged: unchanged.length,
    },
    resolved,
    introduced,
    unchanged,
  };
}
