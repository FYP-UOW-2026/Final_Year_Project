/**
 * Stops passwords being guessed against one account.
 *
 * The rate limit on the sign-in routes counts per IP address and per server instance, so
 * someone rotating addresses can keep guessing. This counts per email address, in
 * Firestore, so it holds across instances. Repeated failures lock the address for a while,
 * and a correct password is refused too during a lock: otherwise guessing could carry on
 * straight through it.
 *
 * Failures are counted for addresses with no account as well, so a lock cannot be used to
 * discover which addresses are registered. Documents are keyed by a hash of the address
 * and hold no personal data.
 *
 * The lock is short and capped on purpose: anyone can lock anyone else's address by
 * guessing badly, so a long lock would hand them a way to keep a person out of their
 * account. Sessions already signed in are not affected.
 */
import crypto from "node:crypto";

import { db } from "../config/firebase.js";
import { env } from "../config/env.js";
import { COLLECTIONS } from "../constants/index.js";
import { ApiError } from "../utils/ApiError.js";
import * as firebaseAuth from "./firebaseAuth.service.js";

const MINUTE = 60_000;
// Failures further apart than this do not add up to an attack.
const FAILURE_WINDOW_MS = 15 * MINUTE;
const MAX_LOCK_MS = 60 * MINUTE;
// A lock this long ago no longer makes the next one longer.
const LOCK_MEMORY_MS = 24 * 60 * MINUTE;

function recordFor(email) {
  const key = crypto.createHash("sha256").update(email.trim().toLowerCase()).digest("hex");
  return db.collection(COLLECTIONS.LOGIN_ATTEMPTS).doc(key);
}

function lockedError(lockedUntil, now) {
  const minutes = Math.max(1, Math.ceil((lockedUntil - now) / MINUTE));
  return ApiError.tooManyRequests(
    `Too many failed attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`
  );
}

async function refuseIfLocked(email) {
  const snap = await recordFor(email).get();
  const lockedUntil = snap.exists ? snap.data().lockedUntil ?? 0 : 0;
  const now = Date.now();
  if (lockedUntil > now) throw lockedError(lockedUntil, now);
}

/** Count one wrong password. Returns the time a lock now ends, or 0 if there is no lock. */
function recordFailure(email) {
  const ref = recordFor(email);
  return db.runTransaction(async (tx) => {
    const snap = await tx.get(ref);
    const previous = snap.exists ? snap.data() : {};
    const now = Date.now();

    const inWindow = now - (previous.firstFailureAt ?? 0) <= FAILURE_WINDOW_MS;
    const failures = (inWindow ? previous.failures ?? 0 : 0) + 1;
    const firstFailureAt = inWindow ? previous.firstFailureAt : now;
    const locks = now - (previous.lockedUntil ?? 0) <= LOCK_MEMORY_MS ? previous.locks ?? 0 : 0;

    if (failures < env.loginGuard.maxFailures) {
      tx.set(ref, { failures, firstFailureAt, locks, lockedUntil: previous.lockedUntil ?? 0 });
      return 0;
    }

    // Each lock in a row is twice as long as the last, up to the cap.
    const lockMs = Math.min(env.loginGuard.lockMinutes * MINUTE * 2 ** locks, MAX_LOCK_MS);
    const lockedUntil = now + lockMs;
    tx.set(ref, { failures: 0, firstFailureAt: now, locks: locks + 1, lockedUntil });
    return lockedUntil;
  });
}

/** Run a password check under the lock, counting a wrong password and clearing on success. */
async function guarded(email, check) {
  await refuseIfLocked(email);
  let result;
  try {
    result = await check();
  } catch (error) {
    // Only a refused credential counts: an outage at Google is nobody's wrong guess.
    if (error.status === 401) {
      const lockedUntil = await recordFailure(email);
      if (lockedUntil) throw lockedError(lockedUntil, Date.now());
    }
    throw error;
  }
  await recordFor(email).delete();
  return result;
}

/** After a password reset: the code proved the inbox, so the owner is not kept waiting. */
export async function clearLock(email) {
  await recordFor(email).delete();
}

export function signIn(email, password) {
  return guarded(email, () => firebaseAuth.signInWithPassword(email, password));
}

/** For changes that re-ask for the password, so a stolen session cannot guess it either. */
export function verifyPassword(email, password) {
  return guarded(email, () => firebaseAuth.verifyPassword(email, password));
}
