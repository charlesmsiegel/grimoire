// Sprint 112: model settings, optional calls, usage and diagnostics (docs/INFERENCE_UI.md).
// Everything shown comes from the app's own ops; library and provider text is only ever set as textContent.
// The page sends nothing to a provider: "Check" is checked by the app without a call, and "Save" only writes
// the app's settings file at the revision this page read.
"use strict";

const TOKEN_NAMES = [["input_tokens", "prompt"], ["output_tokens", "completion"], ["cache_read_tokens", "cached"],
  ["reasoning_tokens", "reasoning"]];
const STAGE_NAMES = {
  npc_generation: "NPC replies", director_generation: "Director replies", decision: "NPC decisions",
  labelling: "Action labelling", planning: "NPC planning", epistemic_inference: "Perception inference",
  recall: "Model recall", graph_lookup: "Model graph lookup", critique: "Critique",
};

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  return node;
}

// replaceChildren with the empty slots (null, false) left out: the DOM would write them as text
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
}

function count(totals, name) {
  // a sum is a lower bound while any of its counts went unreported, or a call was issued with no attempt
  // booked: say so, never show a bare number (not even 0)
  const sum = totals.sums[name], missing = totals.unreported[name];
  const unknown = (totals.unmetered_calls || 0) - (totals.not_issued_calls || 0);
  if (missing === 0 && unknown <= 0) return String(sum);
  if (totals.attempts === 0 || missing === totals.attempts) return "unknown";
  const why = [missing ? `${missing} unreported` : null, unknown > 0 ? `${unknown} call(s) unrecorded` : null];
  return `at least ${sum} (${why.filter(Boolean).join(", ")})`;
}

function unmetered(totals) {
  // 004 counts every planned call with no attempt booked; the ones never issued used nothing
  const unsent = totals.not_issued_calls, rest = totals.unmetered_calls - unsent;
  const parts = [];
  if (unsent) parts.push(`${unsent} never sent`);
  if (rest) parts.push(`${rest} with what they used unknown`);
  return `${totals.unmetered_calls}: ${parts.join(", ")}`;
}

function cost(item) {
  return item.cost_state === "unpriced" ? "unknown: no prices are set" : String(item.cost);
}

function field(id, label, input, hint) {
  return el("div", { class: "g-field" }, el("label", { for: id }, label), input,
    hint ? el("div", { class: "g-hint", id: id + "-hint" }, hint) : null);
}

/** Render the page into ``root``. ``ctx.api(op, args)`` posts one op and rejects with ``error.reason``. */
export async function render(root, ctx) {
  const api = ctx.api;
  const state = { models: null, check: null, chat: null, asked: 0 };
  const live = el("div", { class: "g-live", role: "status", "aria-live": "polite" });
  const sections = {
    model: el("section", { "aria-labelledby": "inf-model" }),
    stages: el("section", { "aria-labelledby": "inf-stages" }),
    usage: el("section", { "aria-labelledby": "inf-usage" }),
    diagnostics: el("section", { "aria-labelledby": "inf-diag" }),
    missing: el("section", { "aria-labelledby": "inf-missing" }),
  };
  fill(root, el("h1", {}, "Models and costs"), live, ...Object.values(sections));
  const say = (text, bad) => { live.textContent = text; live.classList.toggle("g-bad", Boolean(bad)); };

  function draft() {
    const value = (id) => root.querySelector("#" + id).value.trim();
    const number = (id) => Number.parseInt(value(id), 10);
    const fallbacks = value("inf-fallbacks").split(/[\s,]+/).filter(Boolean);
    const settings = {
      provider_id: value("inf-provider"), base_url: value("inf-url"), model: value("inf-name"),
      fallbacks, max_output_tokens: number("inf-tokens"), attempts: number("inf-attempts"),
      director: root.querySelector("#inf-director").checked ? 1 : 0,
    };
    const key = value("inf-key");
    if (key) settings.key_env = key;
    const kept = state.models && state.models.settings;
    if (kept && kept.max_tokens_field) settings.max_tokens_field = kept.max_tokens_field;
    return settings;
  }

  function problems(list) {
    if (!list || !list.length) return null;
    return el("ul", { class: "g-bad" }, list.map((item) => el("li", {}, item.message)));
  }

  function modelSection() {
    const shown = state.models, settings = shown.settings || {}, generation = shown.generation;
    const status = generation.available
      ? el("p", {}, `Generating with ${generation.routes[0]}` + (generation.local ? " (on this computer)." : " (hosted)."))
      : el("p", { class: "g-bad" }, generation.problem ? generation.problem.message : "Generation is off.");
    const readOnly = !shown.file.configured;
    const keyState = settings.key_env ? (settings.key_set ? "set where the app runs" : "not set where the app runs") : "";
    const form = el("form", { onsubmit: (event) => { event.preventDefault(); save(); } },
      field("inf-provider", "Provider name", el("input", { id: "inf-provider", value: settings.provider_id || "local", required: true, autocomplete: "off" })),
      field("inf-url", "Endpoint address", el("input", { id: "inf-url", type: "url", value: settings.base_url || "", required: true, autocomplete: "off", "aria-describedby": "inf-url-hint" }),
        "An OpenAI-compatible endpoint: https, or http on this computer only."),
      field("inf-name", "Model", el("input", { id: "inf-name", value: settings.model || "", required: true, autocomplete: "off" })),
      field("inf-fallbacks", "Fallback models", el("input", { id: "inf-fallbacks", value: (settings.fallbacks || []).join(", "), autocomplete: "off", "aria-describedby": "inf-fallbacks-hint" }),
        `Up to ${shown.bounds.fallbacks}, on the same endpoint. Each is tried only by a retry, so set more than one attempt.`),
      field("inf-key", "Key variable", el("input", { id: "inf-key", value: settings.key_env || "", autocomplete: "off", spellcheck: "false", "aria-describedby": "inf-key-hint" }),
        "The name of the environment variable holding the key, such as OPENROUTER_API_KEY. Never the key. " +
        "Leave empty for a local model with no key." + (keyState ? ` Now: ${keyState}.` : "")),
      field("inf-tokens", "Most tokens per reply", el("input", { id: "inf-tokens", type: "number", min: 1, max: shown.bounds.max_output_tokens, value: settings.max_output_tokens || 1024, required: true })),
      field("inf-attempts", "Attempts per call", el("input", { id: "inf-attempts", type: "number", min: 1, max: shown.bounds.attempts, value: settings.attempts || 1, required: true, "aria-describedby": "inf-attempts-hint" }),
        "Every attempt may be billed."),
      el("div", { class: "g-field" }, el("label", {}, el("input", { id: "inf-director", type: "checkbox", checked: settings.director > 0 }),
        " Allow a director reply when asked (an optional, billed call)")),
      el("div", { class: "g-actions" },
        el("button", { type: "button", onclick: check }, "Check"),
        el("button", { type: "submit", class: "g-primary", disabled: readOnly }, "Save")),
      readOnly ? el("p", { class: "g-hint" }, "Read only: the app was started without a settings file (serve --model FILE).") : null,
      el("div", { id: "inf-check", "aria-live": "polite" }));
    fill(sections.model, el("h2", { id: "inf-model" }, "Model"), status, form);
  }

  function checkView(result) {
    if (!result.ok) return el("div", {}, el("p", { class: "g-bad" }, "These settings cannot be used:"), problems(result.problems));
    return el("div", {},
      el("p", {}, "Usable. Nothing was sent to the provider. Calls would go to:"),
      el("ul", {}, result.routes.map((route, index) => el("li", {}, (index ? "then " : "") + route))),
      el("p", {}, result.local ? "This endpoint is on this computer." : "This endpoint is hosted: its calls may be billed."),
      problems(result.problems));
  }

  function stagesSection() {
    const rows = state.models.stages.map((stage) => el("tr", {},
      el("th", { scope: "row" }, STAGE_NAMES[stage.stage] || stage.stage),
      el("td", {}, stage.kind === "baseline" ? "Always" : stage.available ? (stage.enabled ? "On" : "Off") : "Not available"),
      el("td", {}, stage.note)));
    const limits = state.models.limits;
    fill(sections.stages, el("h2", { id: "inf-stages" }, "Model calls a turn may make"),
      el("p", { class: "g-hint" }, "Every NPC who speaks gets its own call. Every other call is optional, off unless switched on, and capped per turn."),
      el("div", { class: "g-scroll" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Call"), el("th", { scope: "col" }, "State"), el("th", { scope: "col" }, "Why"))), el("tbody", {}, rows))),
      limits ? el("p", {}, `At most ${limits.attempts_per_call} attempt(s) per call and ${limits.max_output_tokens} tokens per reply` +
        (limits.director_calls_per_turn ? "; one director reply per turn." : "; no director reply.")) : null);
  }

  function missingSection() {
    fill(sections.missing, el("h2", { id: "inf-missing" }, "Not available yet"),
      el("ul", {}, state.models.unavailable.map((item) => el("li", {}, el("strong", {}, item.feature.replaceAll("_", " ")), ": ", item.reason))));
  }

  async function chatsSection() {
    let listed;
    try { listed = await api("chats", { limit: 100 }); } catch (error) { listed = { chats: [], error }; }
    const picker = el("select", { id: "inf-chat", onchange: (event) => { state.chat = event.target.value || null; usage(); } },
      el("option", { value: "" }, "Choose a chat"),
      listed.chats.map((entry) => el("option", { value: entry.chat, selected: entry.chat === state.chat }, entry.label || entry.chat)));
    fill(sections.usage, el("h2", { id: "inf-usage" }, "Usage by chat"),
      listed.error ? el("p", { class: "g-bad" }, "The chats cannot be listed: " + listed.error.message) : null,
      field("inf-chat", "Chat", picker), el("div", { id: "inf-usage-body" }));
    if (state.chat) usage();
  }

  async function usage() {
    // only the newest request draws: an answer for a chat no longer chosen is dropped
    const body = root.querySelector("#inf-usage-body"), asked = ++state.asked, chat = state.chat;
    if (!chat) { fill(body); return; }
    fill(body, el("p", {}, "Reading this chat's usage…"));
    let result;
    try { result = await api("usage", { chat }); } catch (error) {
      if (asked === state.asked) fill(body, el("p", { class: "g-bad" }, error.message));
      return;
    }
    if (asked !== state.asked) return;
    const total = result.total;
    const summary = el("dl", { class: "g-totals" },
      TOKEN_NAMES.map(([name, label]) => [el("dt", {}, label + " tokens"), el("dd", {}, count(total, name))]),
      el("dt", {}, "Attempts"), el("dd", {}, String(total.attempts)),
      el("dt", {}, "Cost"), el("dd", {}, cost(total)),
      total.unmetered_calls ? [el("dt", {}, "Calls with no usage booked"), el("dd", { class: "g-bad" }, unmetered(total))] : null);
    const turns = result.turns.filter((turn) => turn.calls.length).reverse().map((turn) => el("details", {},
      el("summary", {}, `Turn ${turn.number}` + (turn.set_aside ? " (set aside at a fork)" : "") + `: ${turn.calls.filter((c) => c.stage).length} call(s)`),
      el("ul", {}, turn.calls.map(callView))));
    fill(body, summary,
      result.problems.length ? el("p", { class: "g-bad" }, "Some turns cannot be read; their calls are not counted.") : null,
      turns.length ? el("div", {}, turns) : el("p", {}, "No model calls in this chat yet."));
  }

  function callView(call) {
    if (!call.stage) return el("li", {}, `Take ${call.take}: skipped ${call.declined.length} optional call(s) the settings did not allow.`);
    const who = call.actor === null ? "Director" : (call.name || "an earlier cast member");
    const where = call.label ? `labelling ${call.label}` : `take ${call.take} (${call.state})`;
    return el("li", {}, el("strong", {}, `${STAGE_NAMES[call.stage] || call.stage}: ${who}`), ` ${where}, ${call.status}` + (call.reason ? ` (${call.reason})` : ""),
      call.attempts.length ? el("ol", {}, call.attempts.map((attempt) => el("li", {},
        `${attempt.outcome} via ${attempt.route || "an unknown route"}, ${attempt.measurement.replaceAll("_", " ")}: ` +
        TOKEN_NAMES.map(([name, label]) => `${label} ${attempt.tokens[name] === null ? "unreported" : attempt.tokens[name]}`).join(", ") +
        `; cost ${cost(attempt)}`)))
        : call.status === "not_issued" ? el("p", {}, "Never sent, so it used nothing.")
        : el("p", { class: "g-bad" }, "No attempt was recorded: what it used is unknown."));
  }

  async function diagnosticsSection() {
    let result;
    try { result = await api("diagnostics", {}); } catch (error) { result = { available: false, reason: error.message, traces: [] }; }
    const refresh = el("button", { type: "button", onclick: diagnosticsSection }, "Refresh");
    const items = result.traces.slice(0, 20).map((trace) => el("li", {},
      `${trace.operation}: ${trace.status}, ${(trace.seconds * 1000).toFixed(0)} ms`,
      trace.calls.length ? el("ul", {}, trace.calls.map((role) => el("li", {},
        `${STAGE_NAMES[role.stage] || role.stage} (${role.actor || "scene"}): ${role.attempts} attempt(s), ` +
        (role.provider_seconds === null ? `provider time ${role.latency}` : `provider ${(role.provider_seconds * 1000).toFixed(0)} ms (${role.latency})`)))) : null));
    fill(sections.diagnostics, el("h2", { id: "inf-diag" }, "Recent calls on this device"),
      el("p", { class: "g-hint" }, "Timings of this app's play calls since it started, kept in memory only. Speakers are numbered, never named."),
      result.available ? null : el("p", {}, "Not available: " + result.reason), refresh,
      items.length ? el("ol", {}, items) : el("p", {}, "Nothing played since the app started."));
  }

  async function load() {
    state.models = await api("models", {});
    modelSection(); stagesSection(); missingSection();
  }

  async function check() {
    try { state.check = await api("check_models", { settings: draft() }); fill(root.querySelector("#inf-check"), checkView(state.check)); say(state.check.ok ? "Checked: nothing was sent." : "These settings cannot be used.", !state.check.ok); }
    catch (error) { say(error.message, true); }
  }

  async function save() {
    try {
      state.models = await api("save_models", { settings: draft(), expected: state.models.etag });
      state.check = null; modelSection(); stagesSection();
      say(state.models.generation.available ? "Saved. The next turn plays by these settings." : "Saved, but generation is off: " + state.models.generation.problem.message,
        !state.models.generation.available);
    } catch (error) {
      say(error.reason === "changed" ? "The settings changed since this page read them. Nothing was saved; they are shown again." : error.message, true);
      if (error.reason === "changed") await load();
    }
  }

  try { await load(); } catch (error) { say("The settings cannot be read: " + error.message, true); }
  await Promise.all([chatsSection(), diagnosticsSection()]);
}
