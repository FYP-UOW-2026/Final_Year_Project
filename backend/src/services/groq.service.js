/**
 * Plain-language explanations for confirmed findings.
 *
 * Three rules govern this file. The model only ever explains a finding the desktop app's
 * own rules already confirmed, so it never decides what counts as a vulnerability. Every
 * string it receives is redacted first, so a token or key found inside an app cannot
 * reach a third party. And the prompt carries our own reviewed guidance for the finding's
 * category (see knowledgeBase.js), which the model is told to treat as the authority --
 * so the fix a developer is given is one we checked against MASVS, not one the model
 * recalled.
 *
 * The API key lives here on the server rather than in the desktop app, which means it
 * can be rotated centrally and is never shipped inside a downloadable binary.
 *
 * Runs on Groq rather than a client SDK: Groq's API is OpenAI-compatible, so a plain
 * `fetch` against its chat-completions endpoint is enough -- no extra dependency, in
 * keeping with the rest of the project's habit of not adding a library for a handful
 * of calls. (This used to call Gemini; switched after its free tier proved too easy to
 * exhaust during ordinary testing -- see the retry split below for why.)
 */
import { env } from "../config/env.js";
import { ApiError } from "../utils/ApiError.js";
import { redactFinding } from "../utils/redact.js";
import * as knowledgeBase from "./knowledgeBase.js";

const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";

const SYSTEM_PROMPT = `You explain confirmed Android security findings to app developers who are not security specialists.

Rules:
- The finding has already been confirmed by a rule-based scanner. Never question whether it is real, and never speculate about other problems.
- Where reviewed guidance is supplied for the finding's category, treat it as the authority: your fix must be the one it describes, and you must not contradict it or name APIs it does not mention.
- Write for a developer with no security background. Plain words, no jargon unless you define it in the same sentence.
- Be specific about the fix. Name the actual Android API, manifest attribute, or setting to change.
- Do not invent evidence, file names, or line numbers that are not in the input.
- Keep the explanation to three sentences or fewer, and the fix to four sentences or fewer.

Reply with JSON only, in this shape:
{"explanation": "...", "mitigation": "...", "references": ["..."]}`;

function buildPrompt(finding) {
  const safe = redactFinding(finding);
  // Grounding comes first so the model reads the reviewed guidance before the finding it
  // has to apply it to. An unknown category yields an empty section and an ungrounded
  // prompt, which is a plainer answer rather than no answer.
  const grounding = knowledgeBase.promptSection(safe.category);

  return [
    grounding || null,
    grounding ? "" : null,
    `Category: ${safe.category}`,
    `Title: ${safe.title}`,
    `Severity: ${safe.severity}`,
    `OWASP risk codes: ${(safe.owasp ?? []).join(", ") || "not mapped"}`,
    `Confidence: ${safe.confidence ?? "confirmed"}`,
    safe.component ? `Affected component: ${safe.component}` : null,
    "",
    "Evidence captured by the scanner (secrets already removed):",
    safe.evidence,
  ]
    .filter((line) => line !== null)
    .join("\n");
}

function parseResponse(text) {
  // Models sometimes wrap JSON in a code fence even when asked not to.
  const cleaned = text
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "");

  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    throw ApiError.internal("The AI service returned a response we could not read.");
  }

  if (!parsed.explanation || !parsed.mitigation) {
    throw ApiError.internal("The AI service returned an incomplete explanation.");
  }

  return {
    explanation: String(parsed.explanation),
    mitigation: String(parsed.mitigation),
    references: Array.isArray(parsed.references) ? parsed.references.map(String) : [],
  };
}

/**
 * One call to Groq's chat-completions endpoint. Throws a plain Error carrying the
 * HTTP status on `.status` so the retry loop below can tell a rate limit apart from
 * a genuine outage without parsing message text.
 */
async function callGroq(prompt, timeoutMs) {
  let response;
  try {
    response = await fetch(GROQ_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.groq.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: env.groq.model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: prompt },
        ],
        response_format: { type: "json_object" },
      }),
      // Bound the call. An overloaded model can otherwise leave the request open for
      // minutes before answering, which is worse than failing: the caller has long
      // since given up, and the retries below never get their turn.
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    // fetch itself threw: DNS failure, connection refused, or the abort signal firing.
    const err = new Error(
      error.name === "TimeoutError" || error.name === "AbortError"
        ? "timed out waiting for a response"
        : error.message
    );
    err.status = 0;
    throw err;
  }

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body?.error?.message || `HTTP ${response.status}`;
    const err = new Error(message);
    err.status = response.status;
    throw err;
  }

  const text = body?.choices?.[0]?.message?.content ?? "";
  return parseResponse(text);
}

/**
 * Generate an explanation and a fix for one finding.
 *
 * A 429 is not retried: it means the account's request quota (per-minute or
 * per-day) is already spent, and trying again a second later gets the same answer
 * every time -- retrying it just spends three round-trips failing the same way
 * instead of one. A 500/502/503, or the request timing out, genuinely can clear up
 * on its own, so those get a few attempts with a growing delay.
 */
export async function explainFinding(finding, { attempts = 3 } = {}) {
  if (!env.groq.enabled) {
    throw ApiError.internal(
      "The AI explanation layer is not configured. Set GROQ_API_KEY to enable it."
    );
  }

  const prompt = buildPrompt(finding);
  const transientStatuses = new Set([0, 500, 502, 503, 504]);

  let delay = 2000;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return { ...(await callGroq(prompt, env.groq.timeoutMs)), model: env.groq.model };
    } catch (error) {
      if (error instanceof ApiError) throw error;

      if (error.status === 429) {
        throw ApiError.internal(
          "The AI service has hit its request limit for right now. Try again in a " +
            "minute, or in a while if this keeps happening -- that usually means the " +
            "daily limit, not just the per-minute one, has been used up."
        );
      }

      const transient = transientStatuses.has(error.status);
      const lastAttempt = attempt === attempts;
      if (lastAttempt || !transient) {
        throw ApiError.internal(`The AI service is unavailable right now. ${error.message}`);
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay *= 2;
    }
  }

  throw ApiError.internal("The AI service is unavailable right now.");
}
