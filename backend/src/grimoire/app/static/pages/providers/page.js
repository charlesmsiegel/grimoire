// Models and costs (112): the model the table plays with, the calls a turn may make, what each chat's calls
// used and this device's recent call timings (docs/INFERENCE_UI.md).
// Everything shown comes from the app's own ops; library and provider text is only ever set as text.
// "Check" is checked by the app without a call, and "Save" only writes the app's settings file at the revision
// this page read. Exactly one control sends anything to a provider: "Send a test call", which says so, sends one
// small request to the saved model when pressed, and is never pressed by the page itself. A provider's key is
// only ever named by its environment variable: the page never asks for the key, shows it or keeps it.
// A cost is an estimate from the prices the person saved with the model, never a bill, and is said to be one.

/** Chats listed at first, and the most the text API lists (044's MAX_LIMIT). */
const CHATS_PAGE = 100, CHATS_MAX = 1000;
/** Turns listed at a time in one chat's usage (the totals always cover every turn). */
const TURNS_PAGE = 200;

const TOKEN_NAMES = [["input_tokens", "prompt"], ["output_tokens", "reply"], ["cache_read_tokens", "cached"],
  ["reasoning_tokens", "reasoning"]];
const TILE_NAMES = { input_tokens: "Prompt tokens", output_tokens: "Reply tokens", cache_read_tokens: "Cached",
  reasoning_tokens: "Reasoning tokens" };
const STAGE_NAMES = {
  npc_generation: "NPC replies", director_generation: "Director replies", decision: "NPC decisions",
  labelling: "Action labelling", planning: "NPC planning", epistemic_inference: "Perception inference",
  recall: "Model recall", graph_lookup: "Model graph lookup", critique: "Critique",
};

// What a preset fills in. A preset names where calls go and which variable holds the key; the model is the
// person's own choice and is never filled in. "custom" is whatever the form holds that is neither.
const PRESETS = {
  openrouter: { provider_id: "openrouter", base_url: "https://openrouter.ai/api/v1", key_env: "OPENROUTER_API_KEY" },
  local: { provider_id: "ollama", base_url: "http://127.0.0.1:11434/v1", key_env: "" },
};
// The Model card's price fields and the settings they are saved as.
const PRICE_FIELDS = [["pricePrompt", "price_prompt"], ["priceReply", "price_reply"], ["priceCached", "price_cached"]];
const PRESET_LABELS = [{ value: "openrouter", label: "OpenRouter" }, { value: "local", label: "Local (Ollama)" },
  { value: "custom", label: "Custom" }];

// Features the page itself now offers, so the app's list of what is missing no longer applies to them.
const OFFERED = new Set(["presets"]);

const plural = (count, one, many) => count + " " + (count === 1 ? one : many || one + "s");

// A sum is a lower bound while any of its counts went unreported, or a call was issued with no attempt booked:
// say so, never show a bare number (not even 0).
function count(totals, name) {
  const sum = totals.sums[name], missing = totals.unreported[name];
  const unknown = (totals.unmetered_calls || 0) - (totals.not_issued_calls || 0);
  if (missing === 0 && unknown <= 0) return { value: String(sum) };
  if (totals.attempts === 0 || missing === totals.attempts) return { value: "Unknown", note: "Not reported by the provider" };
  const why = [missing ? `${missing} unreported` : null, unknown > 0 ? `${plural(unknown, "call")} unrecorded` : null];
  return { value: `at least ${sum}`, note: why.filter(Boolean).join(", ") };
}

// 004 counts every planned call with no attempt booked; the ones never issued used nothing.
function unmetered(totals) {
  const unsent = totals.not_issued_calls, rest = totals.unmetered_calls - unsent;
  const parts = [];
  if (unsent) parts.push(`${unsent} never sent`);
  if (rest) parts.push(`${rest} with what they used unknown`);
  return parts.join(", ");
}

// One attempt's cost in a sentence: an estimate, a floor, or unknown and why. Never a bare 0 for the unknown.
function cost(item) {
  if (item.cost_state === "unpriced") {
    return item.unpriced_attempts ? "unknown: no price is set for this model" : "unknown: no prices are set";
  }
  return (item.cost_lower_bound ? "at least " : "about ") + item.cost;
}

// The Cost tile. Three states: no prices yet ("Set prices to see it"), an estimate from the person's prices, and
// a lower bound, which is always said to be one. Calls that all went to a model with no price are unknown.
function costTile(total, prices) {
  if (total.cost_state === "unpriced") {
    if (!prices || !prices.set) return { value: "Set prices to see it", note: "Enter both prices in the Model card" };
    return { value: "Unknown", note: "No price is set for the model these calls used" };
  }
  const why = [];
  if (total.unpriced_attempts) why.push(plural(total.unpriced_attempts, "attempt") + " on a model with no price left out");
  if (total.cost_lower_bound && !why.length) why.push("some usage was not reported");
  if (total.cost_state === "estimated") why.push("token counts not the provider's own");
  return { value: (total.cost_lower_bound ? "at least " : "") + total.cost,
    note: ["Estimate from your prices", ...why].join("; ") };
}

const TEST_REASONS = {
  auth: "the provider refused the key", account_limit: "the provider says the account is out of credit or over its limit",
  rate_limit: "the provider is rate limiting", bad_request: "the provider refused the request",
  server_error: "the provider had an error", network: "the endpoint could not be reached",
  tls: "the endpoint's certificate could not be trusted", timeout: "the provider took too long",
  peer_not_loopback: "the endpoint is not on this computer", bad_response: "the answer could not be read",
  truncated: "the answer was cut off", provider_error: "the provider reported an error",
  missing_key: "the key variable is not set where the app runs", bad_key: "the key variable does not hold a usable key",
};

// The result of a test call as one line: the model that answered, how long it took and the tokens it used, or the
// failure with the provider's own reason. Provider text is only ever set as text.
function testLine(result) {
  const tokens = (name) => result.tokens.unreported[name] ? "unreported" : String(result.tokens.sums[name]);
  const seconds = result.seconds.toFixed(2) + " s";
  if (result.ok) {
    const parts = [`Answered by ${result.model || result.route} in ${seconds}`,
      `${tokens("input_tokens")} prompt and ${tokens("output_tokens")} reply tokens`];
    if (result.attempts.length > 1) parts.push(plural(result.attempts.length, "attempt"));
    if (result.cost_state !== "unpriced") parts.push(`${result.cost_lower_bound ? "at least" : "about"} ${result.cost} at your prices`);
    if (result.status === "incomplete") parts.push("cut short by the test's small reply limit");
    return { good: true, text: parts.join(" · ") + "." };
  }
  if (result.status === "timeout") {
    return { good: false, text: `No answer within ${result.limit_seconds} seconds, so the test call was given up on.`
      + (result.sent ? " A hosted model may still bill what it began." : "") };
  }
  const why = TEST_REASONS[result.reason] || (result.reason ? String(result.reason).replaceAll("_", " ") : "it did not finish");
  const said = [result.http_status ? "HTTP " + result.http_status : null, result.code, result.detail].filter(Boolean).join(": ");
  return { good: false, text: `The test call failed after ${seconds}: ${why}.` + (said ? " The provider said: " + said : "")
    + (result.sent ? "" : " Nothing was sent.") };
}

/** The ops this page calls: ``chats`` is the text API's, the rest Sprint 112's adapter's (``providers.<op>``). */
const OWN = new Set(["models", "check_models", "save_models", "usage", "diagnostics", "test_call"]);

/** The shell's page entry (docs/APP_SHELL.md): ``ctx.api(op, args)`` posts one op and rejects with
 * ``error.reason``; once the page is left it rejects with ``left`` and nothing more is drawn. */
export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  const api = (op, args) => ctx.api(OWN.has(op) ? "providers." + op : op, args);
  ctx.setTitle("Models and costs");
  ctx.layout("page");
  ctx.styles("page.css");
  // a chat can be named in the address (#/providers?chat=<ChatRef>), so one past the list's end is reachable too
  const named = ctx.query && ctx.query.get ? ctx.query.get("chat") : null;
  // ``test``: the last test call's line, kept while the settings it tested are the ones shown; ``testing`` while
  // one is on its way (the button is off and the line says so, for as long as the request really takes)
  const state = { models: null, chat: named || null, asked: 0, limit: CHATS_PAGE, skip: 0, test: null, testing: false };
  let form = null;  // the Model card's controls, read by Check and Save

  const fill = (node, ...children) => {
    node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
    return node;
  };
  const live = el("div", { class: "providers-live", role: "status", "aria-live": "polite" });
  const say = (text, bad) => {
    live.textContent = text;
    live.className = "providers-live" + (text ? " g-notice " + (bad ? "g-bad" : "g-soft") : "");
  };
  const status = el("section", { class: "g-card providers-status", "aria-label": "Model status", hidden: true });
  const model = el("section", { class: "g-card providers-card", "aria-label": "Model", hidden: true });
  const stages = el("section", { class: "g-card providers-card", "aria-label": "Calls a turn may make", hidden: true });
  const usageSection = el("section", { class: "g-stack providers-usage", "aria-label": "Usage by chat" });
  const usageBody = el("div", { class: "g-stack" });
  const diagnostics = el("section", { class: "g-card providers-card", "aria-label": "Recent calls on this device" });
  const missing = el("section", { class: "providers-missing", "aria-label": "Not available yet" });
  root.replaceChildren(el("div", { class: "g-stack g-loose" }, status, live,
    el("div", { class: "providers-columns" }, model, stages), usageSection, diagnostics, missing));

  // -- the status card ----------------------------------------------------------------------------------------
  function statusCard() {
    const shown = state.models, settings = shown.settings || {}, generation = shown.generation;
    let head, lines;
    if (generation.available) {
      head = "Generating with " + (settings.model || generation.routes[0] || "the model the app was started with");
      if (generation.local === true) lines = ["A local endpoint on this computer: no provider bills its calls."];
      else if (generation.local === false) {
        lines = ["A hosted endpoint: every attempt may be billed by its provider."
          + (settings.key_env ? " The key is read from " + settings.key_env + " where the app runs." : "")];
      } else lines = ["The app was started with this model. Nothing here configures it."];
      if (generation.routes.length) {
        lines.push("Calls go to " + generation.routes[0]
          + (generation.routes.length > 1 ? ", then on a retry to " + generation.routes.slice(1).join(", then ") : "") + ".");
      }
    } else {
      head = "No model is set up";
      lines = [generation.problem ? generation.problem.message : "Generation is off.",
        "Chats work, but nothing answers until a model is saved below."];
    }
    const test = shown.test_call;
    const offered = Boolean(test && test.available);
    // why no test can be sent matters only while a model answers: with none, the lines above already say why
    if (test && !test.available && generation.available && test.reason) lines.push("No test call: " + test.reason + ".");
    const result = state.testing ? el("div", { class: "g-muted providers-test-line" }, "Sending one test call…")
      : state.test ? el("div", { class: "providers-test-line " + (state.test.good ? "providers-test-good" : "providers-problem") }, state.test.text)
      : null;
    const noteId = "providers-test-note";
    fill(status, el("span", { class: "g-dot providers-dot " + (generation.available ? "g-good" : "g-bad"), "aria-hidden": "true" }),
      el("div", { class: "g-grow providers-status-text" }, el("div", { class: "g-heading" }, head),
        lines.map((line, index) => el("div", { class: !generation.available && index === 0 ? "providers-problem" : "g-muted" }, line)),
        el("div", { class: "providers-test-result", role: "status", "aria-live": "polite" }, result)),
      offered ? el("div", { class: "providers-test" },
        el("button", { type: "button", class: "g-button", disabled: state.testing, "aria-describedby": noteId,
          "data-focus-key": "providers:test", onclick: testCall }, state.testing ? "Calling…" : "Send a test call"),
        el("div", { id: noteId, class: "g-muted providers-test-note" }, generation.local === true
          ? "Sends one small request to the saved model on this computer."
          : "Sends one small request to the saved model. A hosted model may bill it.")) : null);
  }

  // One call, only when the button is pressed. It tests the settings as saved (the revision this page read), so
  // an unsaved edit in the form is not what answers.
  async function testCall() {
    if (state.testing) return;
    state.testing = true; state.test = null;
    statusCard();
    let line;
    try { line = testLine(await api("test_call", { expected: state.models.etag })); } catch (error) {
      if (error.reason === "left") return;
      line = { good: false, text: error.reason === "changed"
        ? "The settings changed since this page read them, so nothing was sent. They are shown again."
        : "No test call was sent: " + error.message };
      if (error.reason === "changed") { try { await load(); } catch (_error) { /* the old settings stay shown */ } }
    }
    state.testing = false; state.test = line;
    statusCard();
    const again = status.querySelector("button");
    if (again) again.focus();
    diagnosticsCard();  // the call is among this device's recent calls now
  }

  // -- the Model card -----------------------------------------------------------------------------------------
  function draft() {
    const text = (field) => field.value.trim();
    const number = (field) => Number.parseInt(text(field), 10);
    const settings = {
      provider_id: text(form.provider), base_url: text(form.url), model: text(form.name),
      fallbacks: text(form.fallbacks).split(/[\s,]+/).filter(Boolean),
      max_output_tokens: number(form.tokens), attempts: number(form.attempts),
      director: form.director.checked ? 1 : 0,
    };
    const key = text(form.key);
    if (key) settings.key_env = key;
    // a price goes as the text typed (the app reads it as a decimal, never a float); an empty field is left out
    for (const [field, name] of PRICE_FIELDS) { const price = text(form[field]); if (price) settings[name] = price; }
    const kept = state.models && state.models.settings;
    if (kept && kept.max_tokens_field) settings.max_tokens_field = kept.max_tokens_field;
    return settings;
  }

  // Which preset the form holds now: the one whose provider, endpoint and key variable it matches, else Custom.
  function presetOf() {
    const now = { provider_id: form.provider.value.trim(), base_url: form.url.value.trim(), key_env: form.key.value.trim() };
    const found = Object.entries(PRESETS).find(([, preset]) => Object.keys(preset).every((key) => preset[key] === now[key]));
    return found ? found[0] : "custom";
  }

  function problems(list) {
    if (!list || !list.length) return null;
    return el("ul", { class: "providers-problems" }, list.map((item) => el("li", {}, item.message)));
  }

  function modelCard() {
    const shown = state.models, settings = shown.settings || {};
    const readOnly = !shown.file.configured;
    const keyState = settings.key_env ? (settings.key_set ? "set where the app runs" : "not set where the app runs") : "";
    const control = (label, options, attrs = {}) => {
      const made = ui.field(label, options);
      for (const [key, value] of Object.entries(attrs)) made.input.setAttribute(key, String(value));
      if (options.hint) made.node.append(made.node.children[1]);  // the hint reads under its input
      return made;
    };
    form = {
      provider: control("Provider name", { value: settings.provider_id || "local", required: true, name: "provider" }),
      name: control("Model", { value: settings.model || "", required: true, name: "model" }),
      url: control("Endpoint address", { value: settings.base_url || "", required: true, name: "endpoint", type: "url",
        hint: "OpenAI-compatible: https, or http on this computer only." }),
      key: control("Key variable", { value: settings.key_env || "", name: "key",
        hint: "The name of the environment variable, such as OPENROUTER_API_KEY, never the key itself. "
          + "Leave empty for a local model with no key." + (keyState ? ` Now: ${keyState}.` : "") }, { spellcheck: "false" }),
      fallbacks: control("Fallback models", { value: (settings.fallbacks || []).join(", "), name: "fallbacks",
        hint: `Up to ${shown.bounds.fallbacks}, on the same endpoint. Each is tried only by a retry, so set more than one attempt.` }),
      tokens: control("Most tokens per reply", { value: String(settings.max_output_tokens || 1024), required: true, name: "tokens", type: "number" },
        { min: 1, max: shown.bounds.max_output_tokens }),
      attempts: control("Attempts per call", { value: String(settings.attempts || 1), required: true, name: "attempts", type: "number",
        hint: "Every attempt may be billed." }, { min: 1, max: shown.bounds.attempts }),
      pricePrompt: control("Price per million prompt tokens", { value: settings.price_prompt || "", name: "price_prompt", placeholder: "e.g. 3" },
        { inputmode: "decimal", spellcheck: "false" }),
      priceReply: control("Price per million reply tokens", { value: settings.price_reply || "", name: "price_reply", placeholder: "e.g. 15" },
        { inputmode: "decimal", spellcheck: "false" }),
      priceCached: control("Price per million cached prompt tokens", { value: settings.price_cached || "", name: "price_cached",
        hint: "Optional. Left empty, cached prompt tokens are charged at the prompt price. Prices are for the model named above, "
          + "in the currency your provider bills in; costs shown here are estimates from them, never a bill." },
        { inputmode: "decimal", spellcheck: "false" }),
      director: el("input", { type: "checkbox", "data-focus-key": "providers:director" }),
      check: el("div", { class: "providers-check", "aria-live": "polite" }),
    };
    form.director.checked = settings.director > 0;
    form.key.input.classList.add("g-mono");
    for (const name of ["provider", "name", "url", "key", "fallbacks", "tokens", "attempts", "pricePrompt", "priceReply", "priceCached"]) {
      form[name].input.setAttribute("data-focus-key", "providers:" + name);
    }
    form.presets = ui.segments("Preset", PRESET_LABELS, { onchange: (value) => {
      const preset = PRESETS[value];
      if (!preset) return;  // Custom keeps what the form holds
      form.provider.value = preset.provider_id;
      form.url.value = preset.base_url;
      form.key.value = preset.key_env;
      fill(form.check);
      if (!form.name.value.trim()) form.name.input.focus();
    } });
    form.presets.set(presetOf());
    // typing in a field a preset filled moves the choice to whichever preset the form now matches
    for (const name of ["provider", "url", "key"]) form[name].input.addEventListener("input", () => form.presets.set(presetOf()));
    const wide = (field) => { field.node.classList.add("providers-wide"); return field.node; };
    const node = el("form", { class: "g-stack providers-form", onsubmit: (event) => { event.preventDefault(); save(); } },
      el("h2", { class: "g-heading" }, "Model"),
      el("div", {}, form.presets.node),
      el("div", { class: "providers-fields" }, form.provider.node, form.name.node, wide(form.url), wide(form.key),
        wide(form.fallbacks), form.tokens.node, form.attempts.node,
        el("div", { class: "providers-prices providers-wide" }, form.pricePrompt.node, form.priceReply.node),
        wide(form.priceCached)),
      el("label", { class: "providers-toggle" }, form.director,
        el("span", {}, "Allow a director reply when asked (an optional, billed call)")),
      readOnly ? el("p", { class: "g-muted" }, "Read only: the app was started without a settings file (serve --model FILE).") : null,
      form.check,
      el("div", { class: "providers-save" },
        el("button", { type: "button", class: "g-button", onclick: check }, "Check"),
        el("button", { type: "submit", class: "g-button g-primary", disabled: readOnly }, "Save")));
    fill(model, node);
  }

  function checkView(result) {
    if (!result.ok) {
      return el("div", { class: "g-notice g-bad providers-result" }, el("div", {}, el("strong", {}, "These settings cannot be used:"), problems(result.problems)));
    }
    return el("div", { class: "g-notice g-soft providers-result" }, el("div", {},
      el("strong", {}, "Usable. Nothing was sent to the provider."), el("div", {}, "Calls would go to:"),
      el("ul", {}, result.routes.map((route, index) => el("li", {}, (index ? "then " : "") + route))),
      el("div", {}, result.local ? "This endpoint is on this computer." : "This endpoint is hosted: its calls may be billed."),
      problems(result.problems)));
  }

  // -- the calls a turn may make ------------------------------------------------------------------------------
  function stagesCard() {
    const rows = state.models.stages.map((stage) => {
      const on = stage.kind === "baseline" || (stage.available && stage.enabled);
      const label = stage.kind === "baseline" ? "Always" : stage.available ? (stage.enabled ? "On" : "Off") : "Not available";
      return el("div", { class: "providers-stage" },
        el("span", { class: "providers-stage-name" }, STAGE_NAMES[stage.stage] || stage.stage),
        el("span", { class: "providers-stage-state" + (on ? " providers-on" : "") }, label),
        el("span", { class: "g-muted providers-stage-why" }, stage.note));
    });
    const limits = state.models.limits;
    fill(stages, el("h2", { class: "g-heading" }, "Calls a turn may make"),
      el("p", { class: "g-muted" }, "Every NPC who speaks gets its own call. Every other call is optional, off unless switched on, and capped per turn."),
      el("div", { class: "providers-stages" }, rows),
      limits ? el("p", { class: "g-muted" }, `At most ${plural(limits.attempts_per_call, "attempt")} per call and ${limits.max_output_tokens} tokens per reply`
        + (limits.director_calls_per_turn ? "; one director reply per turn." : "; no director reply.")) : null);
  }

  function missingSection() {
    const items = state.models.unavailable.filter((item) => !OFFERED.has(item.feature));
    if (!items.length) { fill(missing); return; }
    fill(missing, el("details", {}, el("summary", {}, "Not available yet"),
      el("ul", { class: "providers-missing-list" }, items.map((item) => el("li", {},
        el("strong", {}, item.feature.replaceAll("_", " ")), ": ", item.reason)))));
  }

  // -- usage by chat ------------------------------------------------------------------------------------------
  async function chatsSection() {
    let listed;
    try { listed = await api("chats", { limit: state.limit }); } catch (error) {
      if (error.reason === "left") return;
      listed = { chats: [], error };
    }
    const known = new Set(listed.chats.map((entry) => entry.chat));
    const picker = el("select", { class: "g-input providers-chat", "aria-label": "Chat", "data-focus-key": "providers:chat",
      onchange: (event) => { state.chat = event.target.value || null; state.skip = 0; usage(); } },
      el("option", { value: "" }, "Choose a chat"),
      // the chosen chat stays choosable even when it is past the list's end (its exact ref is shown)
      state.chat && !known.has(state.chat) ? el("option", { value: state.chat, selected: true }, state.chat) : null,
      listed.chats.map((entry) => el("option", { value: entry.chat, selected: entry.chat === state.chat }, entry.label || entry.chat)));
    // a full list may have more behind it: ask for a longer one, up to the API's largest
    const more = !listed.error && listed.chats.length >= state.limit && state.limit < CHATS_MAX
      ? ui.button("Show more chats", () => { state.limit = Math.min(state.limit * 4, CHATS_MAX); chatsSection(); }, { size: "small" })
      : null;
    fill(usageSection,
      el("div", { class: "g-row g-between" }, el("h2", { class: "g-heading" }, "Usage by chat"),
        el("div", { class: "g-row" }, more, picker)),
      listed.error ? ui.notice("The chats cannot be listed: " + listed.error.message, { kind: "bad" }) : null,
      usageBody);
    if (state.chat) usage(); else fill(usageBody, ui.empty("Choose a chat to see what its model calls used."));
  }

  function tile(label, value, note, bad) {
    return el("div", { class: "providers-tile" + (bad ? " providers-bad" : "") }, el("div", { class: "g-muted providers-tile-label" }, label),
      el("div", { class: "providers-tile-value" }, value), note ? el("div", { class: "g-muted providers-tile-note" }, note) : null);
  }

  async function usage() {
    // only the newest request draws: an answer for a chat no longer chosen is dropped
    const asked = ++state.asked, chat = state.chat;
    if (!chat) { fill(usageBody, ui.empty("Choose a chat to see what its model calls used.")); return; }
    fill(usageBody, el("p", { class: "g-muted" }, "Reading this chat's usage…"));
    let result;
    try { result = await api("usage", { chat, limit: TURNS_PAGE, skip: state.skip }); } catch (error) {
      if (asked === state.asked && error.reason !== "left") fill(usageBody, ui.notice(error.message, { kind: "bad" }));
      return;
    }
    if (asked !== state.asked) return;
    const total = result.total;
    const prices = result.prices || (state.models && state.models.prices);
    const spent = costTile(total, prices);
    const tiles = el("div", { class: "providers-tiles" },
      TOKEN_NAMES.map(([name]) => { const shown = count(total, name); return tile(TILE_NAMES[name], shown.value, shown.note); }),
      tile("Attempts", String(total.attempts)),
      tile("Cost", spent.value, spent.note),
      total.unmetered_calls ? tile("Calls with no usage booked", String(total.unmetered_calls), unmetered(total), true) : null);
    const turns = result.turns.filter((turn) => turn.calls.length).reverse().map(turnView);
    // the totals cover every turn; the turns themselves come a window at a time, newest first
    const whole = result.listed === result.of;
    const first = result.of - result.skip - result.listed + 1, last = result.of - result.skip;
    const move = (skip) => () => { state.skip = Math.max(skip, 0); usage(); };
    const paging = whole ? null : el("div", { class: "g-row g-between" },
      el("span", { class: "g-muted" }, result.listed ? `Turns ${first} to ${last} of ${result.of}, newest first. The totals above cover all of them.` : `No turns here: this chat has ${result.of}.`),
      el("div", { class: "g-row" },
        result.skip + result.listed < result.of ? ui.button("Earlier turns", move(result.skip + result.listed), { size: "small" }) : null,
        result.skip > 0 ? ui.button("Later turns", move(result.skip - TURNS_PAGE), { size: "small" }) : null));
    fill(usageBody, tiles,
      prices && prices.set ? el("p", { class: "g-muted providers-estimate" },
        `Costs are estimates: the tokens each recorded call used, at the prices set now for ${prices.route}. `
        + "Changing a price changes them for past calls too. What your provider bills is what counts.") : null,
      result.problems.length ? ui.notice("Some turns cannot be read; their calls are not counted.", { kind: "bad" }) : null,
      paging,
      turns.length ? ui.card({ kind: "flush" }, el("div", { class: "providers-turns" }, turns))
        : ui.empty(whole ? "No model calls in this chat yet." : "No model calls in these turns."));
  }

  // One turn: its number and a line saying what it called; opening it lists every call and attempt.
  function turnView(turn) {
    const made = turn.calls.filter((call) => call.stage);
    const groups = new Map();
    for (const call of made) {
      const name = STAGE_NAMES[call.stage] || call.stage;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(speaker(call));
    }
    const detail = [plural(made.length, "call"), ...[...groups].map(([name, who]) => name + ": " + [...new Set(who)].join(", "))].join(" · ");
    return el("details", { class: "providers-turn" },
      el("summary", {}, el("span", { class: "providers-turn-title" }, "Turn " + turn.number),
        el("span", { class: "g-muted g-grow" }, detail), turn.set_aside ? ui.pill("Set aside at a fork") : null),
      el("ul", { class: "providers-calls" }, turn.calls.map(callView)));
  }

  const speaker = (call) => call.actor === null ? "Director" : (call.name || "an earlier cast member");

  function callView(call) {
    if (!call.stage) return el("li", {}, `Take ${call.take}: skipped ${plural(call.declined.length, "optional call")} the settings did not allow.`);
    const where = call.label ? `labelling ${call.label}` : `take ${call.take} (${call.state})`;
    return el("li", {}, el("strong", {}, `${STAGE_NAMES[call.stage] || call.stage}: ${speaker(call)}`), ` ${where}, ${call.status}` + (call.reason ? ` (${call.reason})` : ""),
      call.attempts.length ? el("ol", {}, call.attempts.map((attempt) => el("li", {},
        `${attempt.outcome} via ${attempt.route || "an unknown route"}, ${attempt.measurement.replaceAll("_", " ")}: `
        + TOKEN_NAMES.map(([name, label]) => `${label} ${attempt.tokens[name] === null ? "unreported" : attempt.tokens[name]}`).join(", ")
        + `; cost ${cost(attempt)}`)))
        : call.status === "not_issued" ? el("div", { class: "g-muted" }, "Never sent, so it used nothing.")
        : el("div", { class: "providers-problem" }, "No attempt was recorded: what it used is unknown."));
  }

  // -- this device's recent calls -----------------------------------------------------------------------------
  async function diagnosticsCard() {
    let result;
    try { result = await api("diagnostics", {}); } catch (error) {
      if (error.reason === "left") return;
      result = { available: false, reason: error.message, traces: [] };
    }
    const ms = (seconds) => (seconds * 1000).toFixed(0) + " ms";
    const items = result.traces.slice(0, 20).map((trace) => el("div", { class: "providers-trace" },
      el("div", { class: "g-row g-between" }, el("span", { class: "providers-stage-name" }, trace.operation === "test_call" ? "Test call" : trace.operation),
        el("span", { class: "g-muted" }, trace.status + ", " + ms(trace.seconds))),
      trace.calls.map((role) => el("div", { class: "g-muted" },
        `${STAGE_NAMES[role.stage] || role.stage} (${role.actor || "scene"}): ${plural(role.attempts, "attempt")}, `
        + (role.provider_seconds === null ? `provider time ${role.latency}` : `provider ${ms(role.provider_seconds)} (${role.latency})`)
        + (role.cost_state && role.cost_state !== "unpriced" ? `, cost ${cost(role)}` : "")))));
    fill(diagnostics,
      el("div", { class: "g-row g-between" }, el("h2", { class: "g-heading" }, "Recent calls on this device"),
        ui.button("Refresh", diagnosticsCard, { size: "small" })),
      el("p", { class: "g-muted" }, "Timings of this app's play calls and test calls since it started, kept in memory only. Speakers are numbered, never named."),
      result.available ? null : el("p", {}, "Not available: " + result.reason),
      items.length ? el("div", { class: "providers-stages" }, items) : ui.empty("Nothing played since the app started."));
  }

  // -- reading, checking and saving ---------------------------------------------------------------------------
  async function load() {
    state.models = await api("models", {});
    state.test = null;  // a test's line is about the settings it tested
    statusCard(); modelCard(); stagesCard(); missingSection();
    for (const node of [status, model, stages]) node.hidden = false;
  }

  async function check() {
    try {
      const result = await api("check_models", { settings: draft() });
      fill(form.check, checkView(result));
      say(result.ok ? "Checked: nothing was sent." : "These settings cannot be used.", !result.ok);
    } catch (error) { if (error.reason !== "left") say(error.message, true); }
  }

  async function save() {
    try {
      state.models = await api("save_models", { settings: draft(), expected: state.models.etag });
      state.test = null;
      statusCard(); modelCard(); stagesCard(); missingSection();
      if (state.chat) usage();  // the costs follow the prices just saved
      if (ctx.refreshLibrary) ctx.refreshLibrary().catch(() => {});
      say(state.models.generation.available ? "Saved. The next turn plays by these settings." : "Saved, but generation is off: " + state.models.generation.problem.message,
        !state.models.generation.available);
    } catch (error) {
      if (error.reason === "left") return;
      say(error.reason === "changed" ? "The settings changed since this page read them. Nothing was saved; they are shown again." : error.message, true);
      if (error.reason === "changed") await load();
    }
  }

  try { await load(); } catch (error) {
    if (error.reason === "left") return;
    say("The settings cannot be read: " + error.message, true);
  }
  await Promise.all([chatsSection(), diagnosticsCard()]);
}
