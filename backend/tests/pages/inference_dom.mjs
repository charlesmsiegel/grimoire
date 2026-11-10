// A run of the Models and costs page (112) over a minimal fake DOM and a fake ctx.api: no browser, no network.
// Fails (exit 1) when the page appends anything but nodes, or shows a never-sent call or an unpriced cost wrongly.
import assert from "node:assert/strict";
class Node_ {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.listeners = {}; this.className = "";
    this.classList = { toggle: () => {} }; this._text = null; this.checked = false; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === "value") this.value = v; if (k === "checked") this.checked = true; }
  addEventListener(k, f) { this.listeners[k] = f; }
  append(...c) { for (const x of c) if (typeof x !== "object" || Array.isArray(x)) throw new Error("bad append " + x); this.children.push(...c); }
  replaceChildren(...c) { for (const x of c) if (x === null || x === undefined || typeof x === "boolean") throw new Error("bad child " + x); this.children = c; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => c.textContent ?? c.data ?? "").join(""); }
  querySelector(sel) { const id = sel.slice(1); const walk = (n) => { if (n.attrs && n.attrs.id === id) return n; for (const c of n.children || []) { const f = walk(c); if (f) return f; } return null; }; return walk(this); }
}
globalThis.document = { createElement: (t) => new Node_(t), createTextNode: (d) => ({ data: d, textContent: d }) };
const page = await import(process.argv[2]);
const totals = { attempts: 2, sums: { input_tokens: 10, output_tokens: 3, cache_read_tokens: 0, reasoning_tokens: 0 },
  unreported: { input_tokens: 1, output_tokens: 0, cache_read_tokens: 2, reasoning_tokens: 2 }, unmetered_calls: 2, not_issued_calls: 1, cost: null, cost_state: "unpriced" };
const models = { settings: { provider_id: "local", base_url: "http://127.0.0.1:1/v1", model: "m", fallbacks: ["n"], max_output_tokens: 99, attempts: 2, director: 1, key_env: null, key_set: null, max_tokens_field: "max_tokens" },
  etag: "sha256:x", file: { configured: true, exists: true }, generation: { available: true, problem: null, routes: ["local@x/m"], local: true },
  stages: [{ stage: "npc_generation", kind: "baseline", enabled: true, cap: null, available: true, note: "n" }, { stage: "decision", kind: "optional", enabled: false, cap: null, available: false, note: "no" }],
  limits: { attempts_per_call: 2, max_output_tokens: 99, director_calls_per_turn: 1 }, unavailable: [{ feature: "prices", reason: "none" }], bounds: { fallbacks: 4, attempts: 3, max_output_tokens: 32768 } };
const calls = [];
const api = async (op, args) => { calls.push([op, args]);
  if (op === "models" || op === "save_models") return models;
  if (op === "check_models") return { ok: true, problems: [], routes: ["a", "b"], local: true, key_set: null, sent: false };
  if (op === "chats") return { chats: [{ chat: "c1", label: "Chat one" }] };
  if (op === "usage") return { turns: [{ turn: "t", number: 1, set_aside: false, calls: [
      { take: 1, label: null, state: "kept", phase: "main", stage: "npc_generation", kind: "baseline", actor: "a", name: "Mara", status: "accepted", reason: null,
        attempts: [{ attempt: 1, outcome: "completed", measurement: "provider_reported", route: "r", tokens: { input_tokens: null, output_tokens: 3, cache_read_tokens: null, reasoning_tokens: null, cache_write_tokens: null }, cost: null, cost_state: "unpriced" }] },
      { take: 1, label: null, state: "kept", phase: "main", stage: "npc_generation", kind: "baseline", actor: "b", name: null, status: "not_issued", reason: null, attempts: [] },
      { take: 1, state: "kept", declined: ["x"] }] }], total: totals, by_stage: [], complete: true, problems: [] };
  if (op === "diagnostics") return { available: true, kept: 64, traces: [{ operation: "runner.generate", status: "ok", seconds: 0.5, spans: [], calls: [{ stage: "npc_generation", actor: "npc-1", attempts: 1, provider_seconds: null, latency: "synthetic" }] }] };
  throw new Error(op); };
const root = new Node_("main");
await page.render(root, { api });
const form = root.querySelector("#inf-provider");
const find = (n, tag) => n.tag === tag ? n : (n.children || []).map((c) => c.tag ? find(c, tag) : null).find(Boolean);
await new Promise((r) => setTimeout(r, 10));
root.querySelector("#inf-chat").listeners.change({ target: { value: "c1" } });
await new Promise((r) => setTimeout(r, 10));
const formNode = find(root, "form");
formNode.listeners.submit({ preventDefault() {} });
await new Promise((r) => setTimeout(r, 10));
const ops = calls.map((c) => c[0]);
for (const op of ["models", "chats", "diagnostics", "usage", "save_models"]) assert.ok(ops.includes(op), op);
assert.deepEqual(calls.find((c) => c[0] === "save_models")[1].expected, "sha256:x");
assert.ok(!ops.some((op) => !["models", "chats", "diagnostics", "usage", "save_models", "check_models"].includes(op)));
const shown = root.querySelector("#inf-usage-body").textContent;
for (const text of ["at least 10 (1 unreported)", "unknown: no prices are set", "1 never sent, 1 with what they used unknown",
  "Never sent, so it used nothing.", "prompt unreported"]) assert.ok(shown.includes(text), text);
assert.ok(!shown.includes("[object") && !/\bcost 0\b/.test(shown));
console.log("ok");
