// What the chronicle page works out without the app (Sprint 115): ids, times and the words a review is said in.

// A new operation id, unique enough to name one change: kept with its review so a retry reuses it.
export function newOperationId() {
  return "op-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// "09:30" as minutes into the day (0 to 1439), or null when it is not a 24-hour time.
export function parseTime(text) {
  const match = /^\s*(\d{1,2}):(\d{2})\s*$/.exec(String(text || ""));
  if (!match) return null;
  const hours = Number(match[1]), minutes = Number(match[2]);
  return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
}

// Days, hours and minutes as one exact number of minutes, or null unless each is a whole number and they add up
// to at least one minute.
export function advanceMinutes(days, hours, minutes) {
  const parts = [days, hours, minutes].map((value) => String(value === undefined ? "" : value).trim() || "0");
  if (!parts.every((value) => /^\d{1,6}$/.test(value))) return null;
  const [d, h, m] = parts.map(Number);
  const total = d * 1440 + h * 60 + m;
  return total > 0 ? total : null;
}

// A detail's name as an id (053's topic): lower case, words joined by "-"; "" when nothing is left.
export function topicId(text) {
  return String(text || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100);
}

const at = (moment) => moment.date + " " + moment.time;

// Minutes into the day as "HH:MM".
export function clockTime(minute) {
  const value = Number(minute) || 0;
  return String(Math.floor(value / 60)).padStart(2, "0") + ":" + String(value % 60).padStart(2, "0");
}

// The sentence a review of one clock change is said in, from what chronicle.clock_plan answered.
export function planText(args, plan) {
  if (plan.already_applied) return "This change was already made: making it again changes nothing.";
  if (args.operation === "initialize") return "Start the clock at " + at(plan.after) + ".";
  if (args.operation === "schedule") return "Schedule \"" + args.label + "\" (" + args.kind + ") for " + args.date + " " + clockTime(args.minute) + ". The clock stays at " + at(plan.after) + ".";
  return "Advance time from " + at(plan.before) + " to " + at(plan.after) + ".";
}

const STATUS = { current: "Current", contested: "Contested", superseded: "Replaced", stale: "Rests on corrected evidence",
  older_basis: "Older than what it would replace", unsupported: "Its evidence is no longer held" };

export function statusText(status) { return STATUS[status] || status; }
