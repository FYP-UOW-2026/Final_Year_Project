/**
 * Rate limits shared by more than one route file.
 *
 * Limits used by a single file are defined there (see authLimiter in auth.routes.js).
 * This one has to be a single instance, so that every route that emails a code draws
 * on the same allowance rather than each getting its own.
 */
import rateLimit from "express-rate-limit";

import { env } from "../config/env.js";

/**
 * Code emails per network per hour, across sign-up, password reset and re-verifying.
 *
 * Each address can also only be sent one code a minute and five a day (see
 * studentVerification.service.js). This covers the other direction: one sender working
 * through many addresses. It counts by network, never by address, so its refusal says
 * nothing about whether any address has an account.
 */
export const codeEmailLimiter = rateLimit({
  windowMs: 60 * 60 * 1000,
  limit: env.rateLimits.codeEmailsPerHour,
  standardHeaders: true,
  legacyHeaders: false,
  message: {
    error: {
      status: 429,
      message: "Too many codes have been requested from this network. Try again in an hour.",
    },
  },
});
