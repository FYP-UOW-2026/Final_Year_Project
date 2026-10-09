/**
 * Provider-independent model registry.
 *
 * One entry per usable model, keyed "<provider>:<model>". The router (next piece to be
 * built) reads this instead of any graph node instantiating a provider client directly.
 * Only Gemini exists today -- gemini.service.js/aiGraph.service.js's own client wiring
 * (env.gemini) is unchanged and still the source of truth for how that call is made.
 * This registry is metadata only; it does not yet replace loadClient() in
 * aiGraph.service.js.
 */
import { env } from "../config/env.js";

const ollamaEntries = Object.fromEntries(
  env.ollama.models.map((model, index) => [
    `ollama:${model}`,
    {
      id: `ollama:${model}`,
      provider: "ollama",
      model,
      enabled: env.ollama.enabled,
      priority: index + 1,
      capabilities: {
        text: true,
        tools: true,
        structuredOutput: true,
        vision: false,
        // Ollama has no per-model limit exposed by env config yet -- conservative
        // default until a model-specific value is wired in.
        contextLimit: 32000,
      },
    },
  ])
);

const openaiEntries = Object.fromEntries(
  env.openai.models.map((model, index) => [
    `openai:${model}`,
    {
      id: `openai:${model}`,
      provider: "openai",
      model,
      // False unless OPENAI_ENABLED=true and both OPENAI_API_KEY and OPENAI_MODELS are
      // set -- see env.openai.enabled. Never registered as usable just because a key
      // happens to be present.
      enabled: env.openai.enabled,
      priority: index + 1,
      capabilities: {
        text: true,
        tools: true,
        structuredOutput: true,
        vision: false,
        contextLimit: 128000,
      },
    },
  ])
);

export const modelRegistry = {
  [`gemini:${env.gemini.model}`]: {
    id: `gemini:${env.gemini.model}`,
    provider: "gemini",
    model: env.gemini.model,
    enabled: env.gemini.enabled,
    priority: 1,
    capabilities: {
      text: true,
      tools: true,
      structuredOutput: true,
      vision: false,
      contextLimit: env.gemini.contextLimit,
    },
  },
  [`groq:${env.groq.model}`]: {
    id: `groq:${env.groq.model}`,
    provider: "groq",
    model: env.groq.model,
    enabled: env.groq.enabled,
    priority: 2,
    capabilities: {
      text: true,
      tools: true,
      structuredOutput: true,
      vision: false,
      // Not exposed per-model by env config yet -- gpt-oss-120b's real window is much
      // larger, but a conservative default is safer than overstating it.
      contextLimit: 32000,
    },
  },
  ...ollamaEntries,
  ...openaiEntries,
};
