// What the Prep page works out without the app (Sprint 115): request ids, a ruleset form's values, the words an
// estimate is said in, and the guard that keeps a late answer from overwriting a newer choice.

// A new request id, unique enough to name one preparation: kept with its estimate so a retry reuses it.
export function newRequestId() {
  return "prep-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// A counter of what the person last chose: `next()` names a request, `current(turn)` says whether it is still the
// newest one. An answer whose turn is not current is for a choice the person has since left.
export function latest() {
  let turn = 0;
  return { next: () => ++turn, current: (value) => value === turn, bump: () => { turn += 1; } };
}

// One field's typed text as the value 062 takes, or { error }. Shapes (prep.rulesets): choice, yesno, whole, text,
// dice. The check itself is 062's: this only turns text into a number or a list.
export function fieldValue(field, text) {
  const raw = String(text === undefined || text === null ? "" : text).trim();
  if (field.type === "text") {
    if (!raw) return field.optional ? { value: null } : { error: "Fill this in." };
    return { value: raw };
  }
  if (field.type === "choice") return raw ? { value: raw } : { error: "Choose one." };
  if (field.type === "yesno") return raw === "yes" ? { value: true } : raw === "no" ? { value: false } : { error: "Choose yes or no." };
  if (field.type === "whole") return /^-?\d{1,9}$/.test(raw) ? { value: Number(raw) } : { error: "Give a whole number." };
  if (field.type === "dice") {
    const parts = raw.split(/[\s,]+/).filter(Boolean);
    // no dice of a kind (a WoD pool with Hunger 0, or all Hunger) is an empty list; a field that names an exact
    // count (PbtA's two d6) needs its dice
    if (!parts.length) return field.exactly ? { error: "Give the dice as rolled, like 3, 4." } : { value: [] };
    if (!parts.every((part) => /^\d{1,3}$/.test(part))) return { error: "Give the dice as rolled, like 3, 4." };
    return { value: parts.map(Number) };
  }
  return { error: "This field cannot be read." };
}

// Every field of a form: { values, errors } where errors maps a field's name to what is wrong with it.
export function formValues(fields, read) {
  const values = {}, errors = {};
  for (const field of fields) {
    const found = fieldValue(field, read(field.name));
    if ("error" in found) errors[field.name] = found.error;
    else values[field.name] = found.value;
  }
  return { values, errors };
}

// A value of 062's documented example as the text its field shows.
export function fieldText(field, value) {
  if (value === null || value === undefined) return "";
  if (field.type === "yesno") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");

// The sentences an estimate (prep.prepare_plan's call) is said in.
export function estimateLines(call) {
  const lines = ["One planning call" + (call.route ? " to " + call.route : "") + "."];
  lines.push("About " + plural(call.prompt_tokens, "prompt token") + " (the app's own estimate)"
    + (call.reply_tokens_at_most ? ", and at most " + plural(call.reply_tokens_at_most, "reply token") : "")
    + (call.attempts_at_most ? ", in at most " + plural(call.attempts_at_most, "attempt") : "") + ".");
  if (call.cost_state === "estimated") {
    lines.push("It costs at most " + call.cost_at_most + " at the prices saved with the model, with the prompt counted as "
      + plural(call.prompt_tokens_at_most, "token") + " (each of its bytes as a token, and a few for each message), an estimate, not a bill"
      + (call.unpriced_attempts_at_most ? ", plus " + plural(call.unpriced_attempts_at_most, "retry") + " to a fallback model that has no price" : "") + ".");
  } else {
    lines.push("No price is set for this model, so its cost cannot be estimated.");
  }
  if (call.local === true) lines.push("It goes to a model on this computer.");
  else if (call.local === false) lines.push("It leaves this computer for the configured provider.");
  return lines;
}

// What one sent call used, in a line (prep.preparation's usage), or "" when nothing was sent.
export function usageLine(usage) {
  if (!usage || !usage.sent) return "";
  const tokens = usage.tokens || {};
  const parts = [plural(usage.sent, "call") + " sent"];
  if (typeof tokens.input_tokens === "number") parts.push(plural(tokens.input_tokens, "prompt token"));
  if (typeof tokens.output_tokens === "number") parts.push(plural(tokens.output_tokens, "reply token"));
  if (usage.cost) parts.push((usage.cost_lower_bound ? "at least " : "") + usage.cost);
  return parts.join(" · ");
}

export const KIND_NAMES = { secret: "Secrets", goal: "Goals", calendar: "Calendar", choice: "Choices" };

// Evidence items grouped by kind, in 064's order: [[kind, items]], only kinds with something in them.
export function byKind(items) {
  return Object.keys(KIND_NAMES).map((kind) => [kind, items.filter((item) => item.kind === kind)]).filter(([, found]) => found.length);
}
