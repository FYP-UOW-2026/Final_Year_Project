/**
 * Request validation with zod.
 *
 * Every endpoint that accepts input runs through here, so a malformed request is
 * refused with a clear list of what was wrong rather than failing deeper in the code
 * or writing a half-formed document to Firestore.
 */
import { z } from "zod";

import { ApiError } from "../utils/ApiError.js";

/**
 * A six-digit verification code as a person pastes it. Spaces and dashes are dropped
 * first, since codes copied out of an email often come with them ("123 456", or one
 * space per digit from older emails).
 */
export const verificationCodeSchema = z.preprocess(
  (value) => (typeof value === "string" ? value.replace(/[\s-]/g, "") : value),
  z.string().regex(/^\d{6}$/, "Enter the six-digit code from the email.")
);

/**
 * @param {object} schemas - any of { body, query, params } as zod schemas
 */
export const validate = (schemas) => (req, res, next) => {
  for (const key of ["body", "query", "params"]) {
    const schema = schemas[key];
    if (!schema) continue;

    const result = schema.safeParse(req[key]);
    if (!result.success) {
      const details = result.error.issues.map((issue) => ({
        field: issue.path.join(".") || key,
        message: issue.message,
      }));
      return next(ApiError.badRequest(`Invalid request ${key}.`, details));
    }
    // Use the parsed value so defaults are applied and unknown keys are dropped.
    req[key] = result.data;
  }
  next();
};
