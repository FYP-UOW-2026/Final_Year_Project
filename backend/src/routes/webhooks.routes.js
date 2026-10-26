/**
 * Webhooks from outside services. These authenticate by the sender's signature rather
 * than by a user's token, so they sit outside requireAuth.
 */
import crypto from "node:crypto";

import { Router } from "express";

import { env } from "../config/env.js";
import * as studentVerification from "../services/studentVerification.service.js";
import { ApiError } from "../utils/ApiError.js";
import { asyncHandler } from "../utils/asyncHandler.js";

const router = Router();

/**
 * Whether a webhook really came from Mailgun, which signs timestamp + token with the
 * account's webhook signing key (HMAC-SHA256).
 *
 * The signature covers only those two values, not the body, so it proves who sent a
 * request but not what it says. That is why recordBounce also insists on the random
 * sendId of the exact send, which a forger would have no way of knowing.
 */
export function verifyMailgunSignature({ timestamp, token, signature } = {}, key) {
  if (!key || !timestamp || !token || typeof signature !== "string") return false;
  const expected = crypto.createHmac("sha256", key).update(`${timestamp}${token}`).digest();
  const given = Buffer.from(signature, "hex");
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

/**
 * POST /api/webhooks/mailgun -- Mailgun reports an email it could not deliver
 *
 * Set up in the Mailgun dashboard as a "Permanent failure" webhook. Only failures of a
 * verification code are acted on: the code is marked bounced, which
 * POST /auth/register/code-status then passes on to the student. Anything else is
 * acknowledged and ignored, since Mailgun keeps retrying a webhook until it gets a 2xx.
 */
router.post(
  "/mailgun",
  asyncHandler(async (req, res) => {
    const key = env.email.mailgun.webhookSigningKey;
    if (!key) throw new ApiError(503, "Mailgun webhooks are not set up on this server.");
    if (!verifyMailgunSignature(req.body?.signature, key)) {
      throw ApiError.unauthorized("That webhook signature is not valid.");
    }

    const event = req.body["event-data"] ?? {};
    const variables = event["user-variables"] ?? {};
    if (event.event === "failed" && event.severity === "permanent" && variables.codeDoc) {
      const status = event["delivery-status"] ?? {};
      await studentVerification.recordBounce({
        codeDoc: variables.codeDoc,
        sendId: variables.sendId,
        recipient: String(event.recipient ?? "").toLowerCase(),
        reason: [status.code, status.description || status.message].filter(Boolean).join(" "),
      });
    }
    res.json({ received: true });
  })
);

export default router;
