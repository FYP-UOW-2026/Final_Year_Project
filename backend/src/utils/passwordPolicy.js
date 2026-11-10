/**
 * Password rules. Mirrors bioaudit/password_policy.py -- change both together.
 * Used for registration, change-password and reset. Login does not use it, so accounts
 * made under the old 8-character rule can still sign in.
 */
import { z } from "zod";

export const MIN_LENGTH = 12;

const COMMON = new Set([
  "password", "passw0rd", "p@ssw0rd", "p@ssword", "qwerty", "qwertyuiop", "letmein",
  "welcome", "admin", "administrator", "iloveyou", "monkey", "dragon", "football",
  "baseball", "abc123", "trustno1", "sunshine", "princess", "changeme", "bioaudit",
  "123456", "12345678", "123456789", "1234567890", "111111", "000000", "asdfgh",
  "zxcvbn", "starwars", "whatever", "freedom", "master", "login", "hello",
]);

/** @returns {string|null} what is wrong, or null when acceptable */
export function passwordProblem(password) {
  if (password.length < MIN_LENGTH) return `Use at least ${MIN_LENGTH} characters.`;
  if (!/\p{Ll}/u.test(password)) return "Add a lowercase letter.";
  if (!/\p{Lu}/u.test(password)) return "Add an uppercase letter.";
  if (!/\d/.test(password)) return "Add a digit.";
  if (!/[^\p{L}\d]/u.test(password)) return "Add a symbol (such as ! ? # $).";
  const letters = password.toLowerCase().replace(/[^\p{L}]/gu, "");
  for (const word of COMMON) {
    if (/^[a-z]{6,}$/.test(word) && letters === word) {
      return "That password is too common. Choose something less guessable.";
    }
  }
  if (COMMON.has(password.toLowerCase()) || new Set(password).size <= 3) {
    return "That password is too common. Choose something less guessable.";
  }
  return null;
}

export const strongPasswordSchema = z
  .string()
  .max(128, "That password is too long.")
  .superRefine((value, ctx) => {
    const problem = passwordProblem(value);
    if (problem) ctx.addIssue({ code: z.ZodIssueCode.custom, message: problem });
  });
