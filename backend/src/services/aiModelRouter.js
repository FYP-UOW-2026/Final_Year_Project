/**
 * Centralized model routing. LangGraph nodes must call pickModel(task) instead of
 * instantiating or naming a provider client directly -- that keeps a future provider
 * swap out of aiGraph.service.js entirely.
 *
 * Circuit breaker state is process-local (in-memory Map), which is fine for a single
 * Cloud Run instance's worth of failures; it resets on redeploy/restart.
 */
import { env } from "../config/env.js";
import { ApiError } from "../utils/ApiError.js";
import { modelRegistry } from "./aiModelRegistry.js";

/**
 * Every aiGraph mode routes to the same ordered candidate list today: Gemini first (the
 * live provider), then Groq (hosted, no local runtime needed), then Ollama, then OpenAI
 * last -- OpenAI is never preferred just because it becomes available, only used once
 * all the others are exhausted. The OpenAI entries are inert (registry marks them
 * disabled) until OPENAI_ENABLED=true plus a key and models are configured; unlike
 * Ollama and Groq, no call path exists for them yet -- registering the route ahead of
 * that is deliberate so enabling it later needs no router change, only the adapter.
 */
const fallbackChain = [
  `gemini:${env.gemini.model}`,
  `groq:${env.groq.model}`,
  ...env.ollama.models.map((m) => `ollama:${m}`),
  ...env.openai.models.map((m) => `openai:${m}`),
];

const ROUTES = {
  explain: fallbackChain,
  synthesize: fallbackChain,
  compare: fallbackChain,
  report: fallbackChain,
};

const REQUIRED_CAPABILITY = {
  explain: "tools",
  synthesize: "tools",
  compare: "tools",
  report: "tools",
};

/** consecutiveFailures + openUntil per model id. */
const breakerState = new Map();

function isCircuitOpen(id) {
  const state = breakerState.get(id);
  return Boolean(state?.openUntil && state.openUntil > Date.now());
}

export function recordFailure(id) {
  const state = breakerState.get(id) ?? { consecutiveFailures: 0, openUntil: 0 };
  state.consecutiveFailures += 1;
  if (state.consecutiveFailures >= env.ai.maxProviderAttempts) {
    state.openUntil = Date.now() + env.ai.circuitBreakerMs;
  }
  breakerState.set(id, state);
}

export function recordSuccess(id) {
  breakerState.set(id, { consecutiveFailures: 0, openUntil: 0 });
}

/**
 * Every qualifying candidate for `task`, in route order: enabled, has the required
 * capability, circuit breaker not open. Used by callers that need failover -- trying
 * the next entry when the current one fails outright rather than just at pick time.
 */
export function pickModels(task) {
  const candidates = ROUTES[task];
  if (!candidates) throw ApiError.badRequest(`No route configured for AI task: ${task}`);

  const capability = REQUIRED_CAPABILITY[task];
  return candidates
    .map((id) => modelRegistry[id])
    .filter((entry) => entry && entry.enabled)
    .filter((entry) => !capability || entry.capabilities[capability])
    .filter((entry) => !isCircuitOpen(entry.id));
}

/** First qualifying candidate for `task`. Throws if none qualify. */
export function pickModel(task) {
  const [first] = pickModels(task);
  if (!first) throw ApiError.internal(`No healthy AI model available for task: ${task}`);
  return first;
}
