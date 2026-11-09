/**
 * Resetting a forgotten password with a code emailed to the account's address.
 *
 * Works like student verification, whose code machinery this reuses: a six-digit code
 * is emailed, and entering it along with a new password sets that password. The
 * difference is that the person asking is signed out, so the code is filed under the
 * address, and nothing here may reveal whether an address has an account. Sign-in gives
 * one answer for an unknown email and a wrong password for the same reason.
 */
import crypto from "node:crypto";

import { auth, db } from "../config/firebase.js";
import { COLLECTIONS } from "../constants/index.js";
import { ApiError } from "../utils/ApiError.js";
import * as email from "./email.service.js";
import * as firebaseAuth from "./firebaseAuth.service.js";
import { CODE_TTL_MINUTES, checkCode, issueCode } from "./studentVerification.service.js";
import * as usersService from "./users.service.js";

const resets = () => db.collection(COLLECTIONS.PASSWORD_RESETS);

/**
 * Every request for a code takes at least this long. Sending an email takes a moment and
 * not sending one takes none, so without it a quick answer would mean "no account".
 */
const MIN_SEND_MS = 1500;

/** Where an address's reset code is kept. Exported for the tests, which plant codes. */
export function resetCodeId(address) {
  return crypto.createHash("sha256").update(address).digest("hex");
}

async function findAccount(address) {
  try {
    return await auth.getUserByEmail(address);
  } catch (error) {
    if (error.code === "auth/user-not-found") return null;
    throw error;
  }
}

/**
 * Email a reset code to `address` if it has an account, and otherwise do nothing.
 *
 * Never throws. A refusal only an existing account could get (a resend cooldown, a mail
 * failure) would give away that the account exists, so failures go to the server log
 * and the caller always gives the same answer.
 */
export async function sendResetCode(address) {
  const started = Date.now();
  try {
    const account = await findAccount(address);
    if (account) {
      await issueCode(resets().doc(resetCodeId(address)), {
        to: address,
        key: account.uid,
        kind: "Password reset",
        send: (message) => email.sendPasswordResetCode({ to: address, ...message }),
      });
    }
  } catch (error) {
    console.error(`Password reset code for ${address} was not sent: ${error.message}`);
  }
  const wait = MIN_SEND_MS - (Date.now() - started);
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  return { expiresInMinutes: CODE_TTL_MINUTES };
}

/**
 * Set a new password, given the code emailed to the account's address.
 *
 * An address with no account has no code, so it is refused exactly as a missing code
 * is. The code is hashed with the account's uid, so one sent to an account that was
 * since deleted cannot reset a new account made at the same address. Every session is
 * signed out, as on any password change, and the owner is told by email.
 *
 * The code is only used up once the password has changed, so a refusal after it is
 * checked (the new password being the current one) can be fixed without a new code.
 */
export async function resetPassword(address, code, newPassword) {
  const account = await findAccount(address);
  const ref = resets().doc(resetCodeId(address));
  await checkCode(ref, { email: address, key: account?.uid ?? "", code, onMatch() {} });
  if (!account) {
    throw ApiError.badRequest("There is no code waiting for this address. Request a new one.");
  }
  // Asked only of someone who has just proved they hold the code, so saying it is the
  // current password tells them nothing they could not find out by signing in.
  if (await isCurrentPassword(address, newPassword)) {
    throw ApiError.badRequest(
      "That is already your password. Choose a different one, or sign in with it."
    );
  }

  await usersService.changePassword(account.uid, newPassword);
  await ref.delete().catch(() => {});
  await email.sendPasswordChanged({ to: address });
}

async function isCurrentPassword(address, password) {
  try {
    return await firebaseAuth.verifyPassword(address, password);
  } catch {
    // A wrong password, or Firebase not answering. Either way, carry on with the reset.
    return false;
  }
}
