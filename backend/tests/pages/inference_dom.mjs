// A run of the Models and costs page (112) in the stand-in DOM, with the shell's real design primitives and a
// fake ctx.api: no browser, no network. Fails (exit 1) when the page shows a never-sent call or an unpriced cost
// wrongly, lets a stale answer draw, saves at another revision than it read, or lets a preset touch the model.
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install } from "../js/fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
install("http://127.0.0.1:9");
const ui = await import(pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell", "ui.js")).href);
const page = await import(process.argv[2]);

const totals = { attempts: 2, sums: { input_tokens: 10, output_tokens: 3, cache_read_tokens: 0, reasoning_tokens: 0 },
  unreported: { input_tokens: 1, output_tokens: 0, cache_read_tokens: 2, reasoning_tokens: 2 }, unmetered_calls: 2, not_issued_calls: 1, cost: null, cost_state: "unpriced" };
const models = { settings: { provider_id: "local", base_url: "http://127.0.0.1:1/v1", model: "m", fallbacks: ["n"], max_output_tokens: 99, attempts: 2, director: 1, key_env: null, key_set: null, max_tokens_field: "max_tokens" },
  etag: "sha256:x", file: { configured: true, exists: true }, generation: { available: true, problem: null, routes: ["local@x/m"], local: true },
  stages: [{ stage: "npc_generation", kind: "baseline", enabled: true, cap: null, available: true, note: "n" }, { stage: "decision", kind: "optional", enabled: false, cap: null, available: false, note: "no" }],
  limits: { attempts_per_call: 2, max_output_tokens: 99, director_calls_per_turn: 1 },
  unavailable: [{ feature: "prices", reason: "none" }, { feature: "presets", reason: "none exist" }], bounds: { fallbacks: 4, attempts: 3, max_output_tokens: 32768 } };
const calls = [];
let hold = null;
let saved = models;  // what save_models answers
let refuse = null;  // the next save_models is refused with this
const api = async (op, args) => { calls.push([op, args]);
  if (op === "usage" && args.chat === "slow") return new Promise((resolve) => { hold = () => resolve({ turns: [], total: { ...totals, unmetered_calls: 0, not_issued_calls: 0 }, by_stage: [], complete: true, problems: [], marker: "SLOW" }); });
  if (op === "models") return models;
  if (op === "save_models") { if (refuse) { const error = refuse; refuse = null; throw error; } return saved; }
  if (op === "check_models") return args.settings.model === "bad"
    ? { ok: false, problems: [{ field: "model", message: "name a model the endpoint serves" }], routes: [], local: null, key_set: null, sent: false }
    : { ok: true, problems: [], routes: ["a", "b"], local: true, key_set: null, sent: false };
  if (op === "chats") return { chats: [{ chat: "c1", label: "Chat one" }, { chat: "slow", label: "Slow" }] };
  if (op === "usage") return { turns: [{ turn: "t", number: 1, set_aside: false, calls: [
      { take: 1, label: null, state: "kept", phase: "main", stage: "npc_generation", kind: "baseline", actor: "a", name: "Mara", status: "accepted", reason: null,
        attempts: [{ attempt: 1, outcome: "completed", measurement: "provider_reported", route: "r", tokens: { input_tokens: null, output_tokens: 3, cache_read_tokens: null, reasoning_tokens: null, cache_write_tokens: null }, cost: null, cost_state: "unpriced" }] },
      { take: 1, label: null, state: "kept", phase: "main", stage: "npc_generation", kind: "baseline", actor: "b", name: null, status: "not_issued", reason: null, attempts: [] },
      { take: 1, state: "kept", declined: ["x"] }] }], total: totals, by_stage: [], complete: true, problems: [] };
  if (op === "diagnostics") return { available: true, kept: 64, traces: [{ operation: "runner.generate", status: "ok", seconds: 0.5, spans: [], calls: [{ stage: "npc_generation", actor: "npc-1", attempts: 1, provider_seconds: null, latency: "synthetic" }] }] };
  throw new Error(op); };

const seen = { title: null, styles: [], layout: null, refreshed: 0, sent: [] };
// the shell's ctx: the page calls its own ops as providers.<op> and the text API's chats as is
const context = (send, extra = {}) => ({ ui, api: send, setTitle() {}, styles() {}, layout() {}, setCrumb() {},
  refreshLibrary: async () => {}, ...extra });
const ctx = context((op, args) => { seen.sent.push(op);
  if (op !== "chats" && !op.startsWith("providers.")) throw new Error("unprefixed " + op);
  return api(op.replace(/^providers\./, ""), args); },
  { setTitle: (text) => { seen.title = text; }, styles: (file) => { seen.styles.push(file); },
    layout: (name) => { seen.layout = name; }, refreshLibrary: async () => { seen.refreshed += 1; } });
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
const input = (root, name) => root.querySelector(`input[name="${name}"]`);
const region = (root, label) => root.querySelector(`section[aria-label="${label}"]`);
const button = (root, label) => root.querySelectorAll("button").find((node) => node.textContent === label);
const type = (node, text) => { node.value = text; node.dispatchEvent({ type: "input" }); };
const pressed = (root) => region(root, "Model").querySelectorAll("button.g-segment").find((node) => node.getAttribute("aria-pressed") === "true").textContent;

const root = document.createElement("main");
await page.mount(root, ctx);
await tick();
assert.ok(!root.textContent.includes("[object"), "only nodes and text are drawn");

// the status card: the model in use, and that a local endpoint is not billed
const status = region(root, "Model status");
assert.ok(status.textContent.includes("Generating with m") && status.textContent.includes("on this computer"), status.textContent);
assert.ok(status.querySelector("span.g-dot.g-good"), "a model that answers shows the good dot");
assert.ok(!status.textContent.includes("hosted"));

// the calls a turn may make: each stage with its state and its reason
const stages = region(root, "Calls a turn may make").querySelectorAll("div.providers-stage").map((node) => node.children.map((child) => child.textContent));
assert.deepEqual(stages, [["NPC replies", "Always", "n"], ["NPC decisions", "Not available", "no"]]);

// what the app lacks is still listed, without the presets this page now offers
const lacking = region(root, "Not available yet").textContent;
assert.ok(lacking.includes("prices") && !lacking.includes("presets"), lacking);

// usage: only the newest request draws
const picker = root.querySelector("select");
assert.equal(picker.getAttribute("aria-label"), "Chat");
const pick = (value) => { picker.value = value; picker.dispatchEvent({ type: "change" }); };
pick("slow"); await tick();
pick("c1"); await tick();
hold(); await tick();  // the first chat's answer comes last: it must not draw

// a preset fills the provider, the endpoint and the key's variable, never the model or a key
assert.equal(pressed(root), "Custom", "settings that match no preset are Custom");
button(root, "OpenRouter").click();
assert.deepEqual(["provider", "endpoint", "key", "model"].map((name) => input(root, name).value),
  ["openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "m"]);
assert.equal(pressed(root), "OpenRouter");
type(input(root, "endpoint"), "https://example.invalid/v1");
assert.equal(pressed(root), "Custom", "an edited endpoint is no longer the preset");
button(root, "Local (Ollama)").click();
assert.deepEqual(["provider", "endpoint", "key"].map((name) => input(root, name).value), ["ollama", "http://127.0.0.1:11434/v1", ""]);
button(root, "Custom").click();
assert.equal(input(root, "provider").value, "ollama", "Custom keeps what the form holds");
assert.ok(!root.querySelectorAll("input").some((node) => node.getAttribute("type") === "password"), "the page never asks for a key");

// a draft the app refuses shows every message it gave, and nothing is saved
type(input(root, "model"), "bad");
button(root, "Check").click(); await tick();
assert.ok(region(root, "Model").textContent.includes("These settings cannot be used:") && region(root, "Model").textContent.includes("name a model the endpoint serves"));
assert.ok(!calls.some((call) => call[0] === "save_models"));

// an edit survives Check, and Save sends the edited draft at the revision read
type(input(root, "model"), "edited-model");
button(root, "Check").click(); await tick();
assert.equal(input(root, "model").value, "edited-model");
assert.ok(region(root, "Model").textContent.includes("Nothing was sent"));
const form = root.querySelector("form");
// a save refused because the file moved on says so, saves nothing and shows the settings again
refuse = Object.assign(new Error("moved"), { reason: "changed" });
form.dispatchEvent({ type: "submit", preventDefault() {} }); await tick();
assert.ok(root.querySelector("div.providers-live").textContent.includes("Nothing was saved"));
assert.equal(input(root, "model").value, "m", "the settings as they are now are shown again");
// any other refusal shows the app's own message
refuse = Object.assign(new Error("OTHER_API_KEY is sent only to its own hosts"), { reason: "invalid_settings" });
root.querySelector("form").dispatchEvent({ type: "submit", preventDefault() {} }); await tick();
assert.equal(root.querySelector("div.providers-live").textContent, "OTHER_API_KEY is sent only to its own hosts");
assert.ok(root.querySelector("div.providers-live").classList.contains("g-bad"));
type(input(root, "model"), "edited-model");
type(input(root, "key"), "OPENROUTER_API_KEY");
root.querySelector("form").dispatchEvent({ type: "submit", preventDefault() {} }); await tick();
const ops = calls.map((c) => c[0]);
for (const op of ["models", "chats", "diagnostics", "usage", "save_models"]) assert.ok(ops.includes(op), op);
const sent = calls.filter((c) => c[0] === "save_models").pop()[1];
assert.deepEqual(sent.expected, "sha256:x");
assert.deepEqual(sent.settings, { provider_id: "local", base_url: "http://127.0.0.1:1/v1", model: "edited-model", fallbacks: ["n"],
  max_output_tokens: 99, attempts: 2, director: 1, key_env: "OPENROUTER_API_KEY", max_tokens_field: "max_tokens" },
  "the key goes only as the name of its variable, and nothing else is added");
assert.ok(!ops.some((op) => !["models", "chats", "diagnostics", "usage", "save_models", "check_models"].includes(op)));
assert.ok(root.querySelector("div.providers-live").textContent.startsWith("Saved."));

// usage: tiles that never show a lower bound bare, and a per-turn list
const usage = region(root, "Usage by chat");
const shown = usage.textContent;
assert.ok(!shown.includes("SLOW") && shown.includes("Mara"), "a stale chat's answer drew");
const tiles = Object.fromEntries(usage.querySelectorAll("div.providers-tile").map((node) => [node.children[0].textContent, node.children.slice(1).map((child) => child.textContent)]));
assert.deepEqual(tiles, {
  "Prompt tokens": ["at least 10", "1 unreported, 1 call unrecorded"], "Reply tokens": ["at least 3", "1 call unrecorded"],
  "Cached": ["Unknown", "Not reported by the provider"], "Reasoning tokens": ["Unknown", "Not reported by the provider"],
  "Attempts": ["2"], "Cost": ["Unknown", "No prices are set"],
  "Calls with no usage booked": ["2", "1 never sent, 1 with what they used unknown"] });
const turn = usage.querySelector("details.providers-turn");
assert.equal(turn.querySelector("summary").textContent, "Turn 12 calls · NPC replies: Mara, an earlier cast member");
for (const text of ["unknown: no prices are set", "Never sent, so it used nothing.", "prompt unreported", "skipped 1 optional call"]) assert.ok(shown.includes(text), text);
assert.ok(!shown.includes("[object") && !/\bcost 0\b/.test(shown));
assert.ok(region(root, "Recent calls on this device").textContent.includes("runner.generate"));
assert.equal(seen.title, "Models and costs");
assert.equal(seen.layout, "page");
assert.deepEqual(seen.styles, ["page.css"]);
assert.equal(seen.refreshed, 1, "the shell's library facts are refreshed after a save");
assert.ok(seen.sent.includes("providers.save_models") && seen.sent.includes("chats"));

// no model: the status says so with the app's reason, and a hosted one says it may be billed, naming the key's variable
for (const [generation, settings, texts, dot] of [
  [{ available: false, problem: { code: "key_missing", message: "the key variable is not set" }, routes: [], local: false }, models.settings,
    ["No model is set up", "the key variable is not set", "nothing answers until a model is saved"], "span.g-dot.g-bad"],
  [{ available: true, problem: null, routes: ["hosted@h/m", "hosted@h/n"], local: false }, { ...models.settings, key_env: "EXAMPLE_API_KEY", key_set: true },
    ["Generating with m", "hosted endpoint: every attempt may be billed", "read from EXAMPLE_API_KEY", "then on a retry to hosted@h/n"], "span.g-dot.g-good"]]) {
  const other = document.createElement("main");
  await page.mount(other, context((op, args) => op === "providers.models" ? { ...models, generation, settings } : api(op.replace(/^providers\./, ""), args)));
  await tick();
  const card = region(other, "Model status");
  for (const text of texts) assert.ok(card.textContent.includes(text), text + " in " + card.textContent);
  assert.ok(card.querySelector(dot), dot);
}

// a long chat list: a full page offers Show more, and a chat named in the address is chosen though unlisted
const asks = [];
const many = async (op, args) => {
  if (op === "chats") { asks.push(args.limit);
    return { chats: Array.from({ length: args.limit }, (_v, i) => ({ chat: "chat-" + i, label: "Chat " + i })) }; }
  return api(op.replace(/^providers\./, ""), args); };
const second = document.createElement("main");
const usageAsked = [];
await page.mount(second, context((op, args) => { if (op === "providers.usage") usageAsked.push(args.chat); return many(op, args); },
  { query: new URLSearchParams("chat=old-chat") }));
await tick();
assert.deepEqual(usageAsked, ["old-chat"], "the chat named in the address is shown");
assert.ok(second.querySelectorAll("option").some((node) => node.getAttribute("value") === "old-chat" && node.selected), "the named chat is in the picker");
button(second, "Show more chats").click();
await tick();
assert.deepEqual(asks, [100, 400]);

// a long chat: its turns come a window at a time, and an empty window is not called an empty chat
const windows = [];
const third = document.createElement("main");
await page.mount(third, context((op, args) => { if (op !== "providers.usage") return many(op, args);
    windows.push(args.skip);
    return Promise.resolve({ turns: [], skip: args.skip, listed: args.skip === 0 ? 200 : 0, of: 450,
      total: { ...totals, unmetered_calls: 0, not_issued_calls: 0 }, by_stage: [], complete: true, problems: [] }); },
  { query: new URLSearchParams("chat=long") }));
await tick();
const text = region(third, "Usage by chat").textContent;
assert.ok(text.includes("Turns 251 to 450 of 450") && text.includes("No model calls in these turns.") && !text.includes("in this chat yet"), text);
button(third, "Earlier turns").click();
await tick();
assert.deepEqual(windows, [0, 200]);
assert.ok(button(third, "Later turns"), "a later window can be gone back to");

// the settings cannot be read: the page says so and offers no form to save over them
const fourth = document.createElement("main");
await page.mount(fourth, context((op, args) => { if (op === "providers.models") throw new Error("unreadable"); return api(op.replace(/^providers\./, ""), args); }));
await tick();
assert.ok(byText(fourth, "div", "The settings cannot be read: unreadable").length);
assert.equal(fourth.querySelector("form"), null);
console.log("ok");
