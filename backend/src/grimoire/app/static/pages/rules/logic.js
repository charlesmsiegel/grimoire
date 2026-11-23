// What the Rules page works out without the app (Sprint 116): ids, a sheet's typed values, dice as typed, and the
// words a module's problems, a review and a roll are said in.

// A module id as 086 takes it: 1-64 lower case letters, digits and _, starting with a letter.
export function moduleIdError(text) {
  const value = String(text || "").trim();
  if (!value) return "Give the module's id.";
  return /^[a-z][a-z0-9_]{0,63}$/.test(value) ? "" : "An id is lower case letters, digits and _, starting with a letter (like duel_rules).";
}

// A new request id, unique enough to name one roll: kept with its review so asking again reuses it.
export function newRequestId() {
  return "roll-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// The first 12 hex digits of a revision digest, for a link or a heading (the full digest is shown beside it).
export function shortRevision(revision) {
  const text = String(revision || "");
  return text.startsWith("sha256:") ? text.slice(7, 19) : text;
}

const NUMERIC = new Set(["number", "dots", "resource", "track"]);

// One sheet field's typed text as the value 086 takes, or { absent } (left empty: it takes its default) or { error }.
// The check itself is 086's fill_sheet: this only turns text into a whole number, text or a list of lines.
export function sheetValue(field, text) {
  const raw = String(text === undefined || text === null ? "" : text);
  if (NUMERIC.has(field.type)) {
    const trimmed = raw.trim();
    if (!trimmed) return { absent: true };
    return /^-?\d{1,9}$/.test(trimmed) ? { value: Number(trimmed) } : { error: "Give a whole number." };
  }
  if (field.type === "list") {
    const items = raw.split("\n").map((line) => line.trim()).filter(Boolean);
    return items.length ? { value: items } : { absent: true };
  }
  return raw === "" ? { absent: true } : { value: raw };
}

// Every field of a sheet: { values, errors }; a field left empty is left out, so it takes its default.
export function sheetValues(fields, read) {
  const values = {}, errors = {};
  for (const field of fields) {
    const found = sheetValue(field, read(field.key));
    if (found.error) errors[field.key] = found.error;
    else if (!found.absent) values[field.key] = found.value;
  }
  return { values, errors };
}

// A field's range or limit, as the hint under it says it.
export function fieldHint(field) {
  if (NUMERIC.has(field.type)) {
    const range = "A whole number from " + field.min + " to " + field.max;
    return range + (field.default === null || field.default === undefined ? "" : "; empty takes " + field.default);
  }
  if (field.type === "list") return "One entry a line, at most " + field.max_items;
  return "Text of at most " + field.max_length + " characters" + (field.default ? "; empty takes \"" + field.default + "\"" : "");
}

// Dice as typed ("4", "3, 5 6") as a list of whole numbers, or null when it is not one.
export function parseFaces(text) {
  const parts = String(text || "").split(/[\s,]+/).filter(Boolean);
  if (!parts.length || !parts.every((part) => /^\d{1,4}$/.test(part))) return null;
  return parts.map(Number);
}

// Dice by their sides, as a person says them: [20] is "1d20", [10, 10, 10] "3d10", [6, 6] "2d6".
export function diceText(sides) {
  const counts = new Map();
  for (const side of sides || []) counts.set(side, (counts.get(side) || 0) + 1);
  return [...counts].map(([side, n]) => n + "d" + side).join(" + ") || "no dice";
}

const signed = (n) => (n < 0 ? "−" + Math.abs(n) : "+" + n);
const capital = (text) => String(text || "").charAt(0).toUpperCase() + String(text || "").slice(1);

// What an action asks, in a line: "Dexterity (stealth), DC 15, +3".
export function actionText(family, action) {
  if (family === "d20") return capital(action.ability) + " (" + action.skill + "), DC " + action.dc + ", " + signed(action.modifier);
  if (family === "wod") return capital(action.attribute) + " + " + action.skill + ", pool " + action.pool + " with " + action.hunger
    + " Hunger, difficulty " + action.difficulty;
  if (family === "pbta") return "Defy Danger with " + action.stat + " " + signed(action.modifier) + ": " + action.trigger;
  return Object.entries(action || {}).map(([key, value]) => key + " " + value).join(", ");
}

// What a roll came to, in a line: "4 → 7, failure".
export function resultText(roll) {
  const result = roll.result || {};
  if (roll.family === "d20") return roll.faces.join(", ") + " → " + result.total + ", " + (result.success ? "success" : "failure");
  if (roll.family === "wod") return roll.faces.join(", ") + " → " + result.successes + " successes, " + result.outcome;
  if (roll.family === "pbta") return roll.faces.join(" + ") + " → " + result.total + ", " + result.outcome;
  return roll.faces.join(", ") + " → " + roll.outcome;
}

// The sentence a roll's review is said in, from what rules.roll_plan answered.
export function planText(plan) {
  return "Roll " + diceText(plan.dice) + " for " + (plan.name || plan.actor) + " under attempt " + plan.attempt + ".";
}

// A module's problems grouped by the part they are about (the first segment of the path: sheets, checks...), in
// the order 086 reported them.
export function problemGroups(problems) {
  const groups = new Map();
  for (const problem of problems || []) {
    const head = problem.path === "$" ? "The whole file" : problem.path.split(".")[0];
    if (!groups.has(head)) groups.set(head, []);
    groups.get(head).push(problem);
  }
  return [...groups];
}

// The ids this tab opened, newest first, from the text kept in a draft; at most 8, each a valid id.
export function recentIds(text, add) {
  let list = [];
  try { list = JSON.parse(text || "[]"); } catch { list = []; }
  if (!Array.isArray(list)) list = [];
  list = list.filter((item) => typeof item === "string" && !moduleIdError(item));
  if (add) list = [add, ...list.filter((item) => item !== add)];
  return list.slice(0, 8);
}
