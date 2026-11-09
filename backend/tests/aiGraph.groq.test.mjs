/**
 * Offline test of the conversation aiGraph.service.js sends to Groq. Run with:
 *   node tests/aiGraph.groq.test.mjs
 *
 * Groq speaks OpenAI's tool-call format and refuses a request that breaks it, so the
 * stand-in below applies the same rules and answers 400 the way Groq does.
 */
import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";

const { privateKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
  publicKeyEncoding: { type: "spki", format: "pem" },
});

process.env.FIREBASE_PROJECT_ID = "aigraph-test-project";
process.env.FIREBASE_CLIENT_EMAIL = "aigraph@aigraph-test-project.iam.gserviceaccount.com";
process.env.FIREBASE_PRIVATE_KEY = privateKey.replace(/\n/g, "\n");
process.env.FIREBASE_WEB_API_KEY = "aigraph-test-web-key";
process.env.GEMINI_API_KEY = "";
process.env.GROQ_API_KEY = "groq-test-key";

const { createSession } = await import("../src/services/aiGraph.service.js");
const knowledgeBase = await import("../src/services/knowledgeBase.js");

const USER = { uid: "user-1", organisationId: null, role: "member" };
const FINAL = JSON.stringify({ explanation: "Plain words.", mitigation: "Change the flag." });

let passed = 0;
const failures = [];

async function check(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (error) {
    failures.push({ name, error });
    console.log(`  FAIL ${name}\n       ${error.message}`);
  }
}

/** What Groq refuses with a 400, or null for a request it accepts. */
function groqRejection(messages) {
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    if (message.role !== "assistant" || !message.tool_calls) continue;
    const expected = [];
    for (const [c, call] of message.tool_calls.entries()) {
      if (!call.id) return `'messages.${i}.tool_calls.${c}.id' : property 'id' is missing`;
      if (call.type !== "function") return `'messages.${i}.tool_calls.${c}.type' is missing`;
      if (typeof call.function?.arguments !== "string") {
        return `'messages.${i}.tool_calls.${c}.function.arguments' must be a string`;
      }
      expected.push(call.id);
    }
    const answers = messages.slice(i + 1, i + 1 + expected.length);
    const ids = answers.filter((m) => m.role === "tool").map((m) => m.tool_call_id);
    if (JSON.stringify(ids) !== JSON.stringify(expected)) {
      return `tool_call_ids did not have response messages: ${expected} vs ${ids}`;
    }
  }
  for (const [i, message] of messages.entries()) {
    if (message.role === "tool" && !message.tool_call_id) {
      return `'messages.${i}.tool_call_id' is missing`;
    }
  }
  return null;
}

/** Replace fetch with a Groq that plays back `replies`, recording each request. */
function standInGroq(replies) {
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    const body = JSON.parse(options.body);
    requests.push(body);
    const rejection = groqRejection(body.messages);
    if (rejection) {
      return new Response(JSON.stringify({ error: { message: rejection } }), { status: 400 });
    }
    const message = replies.shift() ?? { role: "assistant", content: FINAL };
    return new Response(JSON.stringify({ choices: [{ message }] }), { status: 200 });
  };
  return requests;
}

function toolCall(id, name, args) {
  return { id, type: "function", function: { name, arguments: JSON.stringify(args) } };
}

const session = () => createSession({ user: USER, scanIds: ["scan-1"] });

await check("a tool call and its result are sent back in the format Groq requires", async () => {
  const requests = standInGroq([
    { role: "assistant", content: null, tool_calls: [toolCall("abc", "get_guidance", { category: "debuggable-release" })] },
  ]);
  const out = await session().explain("Explain this finding.");
  assert.equal(out.result.explanation, "Plain words.");
  assert.equal(requests.length, 2);
  assert.equal(requests[1].messages.filter((m) => m.role === "tool").length, 1);
});

await check("the arguments Groq returns as a JSON string reach the tool as values", async () => {
  const requests = standInGroq([
    { role: "assistant", content: null, tool_calls: [toolCall("abc", "get_guidance", { category: "debuggable-release" })] },
  ]);
  await session().explain("Explain this finding.");
  const tool = requests[1].messages.find((m) => m.role === "tool");
  const section = knowledgeBase.promptSection("debuggable-release");
  assert.ok(section, "the knowledge base should have guidance for this category");
  assert.ok(
    JSON.parse(JSON.parse(tool.content).content).section.startsWith(section.slice(0, 40)),
    tool.content.slice(0, 200)
  );
});

await check("several tool calls in one turn go in one assistant message", async () => {
  const requests = standInGroq([
    {
      role: "assistant",
      content: null,
      tool_calls: [
        toolCall("a", "get_guidance", { category: "debuggable-release" }),
        toolCall("b", "get_finding_evidence", { scanId: "not-in-session", findingIndex: 0 }),
      ],
    },
  ]);
  const out = await session().explain("Explain this finding.");
  assert.equal(out.result.mitigation, "Change the flag.");
  const assistant = requests[1].messages.filter((m) => m.tool_calls);
  assert.equal(assistant.length, 1);
  assert.equal(assistant[0].tool_calls.length, 2);
});

await check("tool calls left unanswered when the budget runs out are not sent back", async () => {
  const again = () => ({
    role: "assistant",
    content: null,
    tool_calls: [toolCall("x", "get_guidance", { category: "debuggable-release" })],
  });
  const requests = standInGroq(Array.from({ length: 9 }, again));
  const out = await session().explain("Explain this finding.");
  assert.equal(out.result.explanation, "Plain words.");
  assert.ok(requests.length >= 10);
});

console.log(`\n${passed} passed, ${failures.length} failed.`);
process.exit(failures.length ? 1 : 0);
