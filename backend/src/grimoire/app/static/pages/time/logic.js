// What the Time page works out without the app (Sprint 116): ids, spans of campaign time, and the words a review
// and a record are said in. No DOM and no calls, so it is tested by itself (tests/js/time.test.mjs).

// A new operation id, unique enough to name one change: kept with its review so a retried commit reuses it.
export function newOperationId() {
  return "op-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// Text as an id segment: lower case, words joined by "-", at most `max` characters; "" when nothing is left.
export function slug(text, max = 60) {
  return String(text || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, max).replace(/-+$/g, "");
}

// A new record's id: its words as a slug and a short unique tail, so two records of the same words never collide.
export function recordId(text, fallback) {
  return (slug(text, 40) || fallback) + "-" + Math.random().toString(36).slice(2, 8);
}

// Days and hours as one exact number of minutes, or null unless each is a whole number and they add up to at
// least one minute. Campaign time is never estimated: a blank part is zero, nothing is rounded.
export function spanMinutes(days, hours) {
  const parts = [days, hours].map((value) => String(value === undefined || value === null ? "" : value).trim() || "0");
  if (!parts.every((value) => /^\d{1,6}$/.test(value))) return null;
  const total = Number(parts[0]) * 1440 + Number(parts[1]) * 60;
  return total > 0 ? total : null;
}

// "09:30" as minutes into the day (0 to 1439), or null when it is not a 24-hour time.
export function parseTime(text) {
  const match = /^\s*(\d{1,2}):(\d{2})\s*$/.exec(String(text || ""));
  if (!match) return null;
  const hours = Number(match[1]), minutes = Number(match[2]);
  return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
}

const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");

// An exact span of minutes in words: "20 days", "1 day 3 hours", "45 minutes"; "no time" for 0.
export function spanText(minutes) {
  const total = Math.max(0, Math.floor(Number(minutes) || 0));
  if (!total) return "no time";
  const days = Math.floor(total / 1440), hours = Math.floor((total % 1440) / 60), rest = total % 60;
  return [days && plural(days, "day"), hours && plural(hours, "hour"), rest && plural(rest, "minute")].filter(Boolean).join(" ");
}

// A campaign-clock moment as shown: "1024-03-01 10:00"; "" when there is none.
export function at(moment) {
  return moment ? (moment.date || "day " + moment.fixed_day) + " " + moment.time : "";
}

// Who an actor is, as a person reads it: its name, else its reference.
export function who(actor) {
  return actor ? actor.name || actor.actor : "";
}

export function names(list) {
  return (list || []).map(who).join(", ");
}

const quoted = (text) => "“" + text + "”";

function flagLine(flags) {
  if (!flags || !flags.length) return null;
  return "It crosses " + flags.map((flag) => flag.label || flag.deadline).join(", ")
    + ": that is a flag only. Nothing happens by itself; decide in play what follows.";
}

// -- 074 downtime -----------------------------------------------------------------------------------------------

const ACTIVITY = { active: "Active", complete: "Complete", abandoned: "Abandoned" };
export function activityStatus(status) { return ACTIVITY[status] || status; }

// The lines a review of one downtime change is said in, from the request and what time.downtime_plan answered.
// `activity` is the activity as the overview listed it (null for a begin), `actor` the cast member it is for.
export function downtimeLines(args, plan, { activity = null, actor = null } = {}) {
  if (plan.already_applied) return ["This change was already made: making it again changes nothing."];
  const what = activity ? quoted(activity.intent) : quoted(args.intent || plan.activity);
  if (args.operation === "begin") {
    return ["Begin " + quoted(args.intent) + " (" + args.kind + ") for " + (who(actor) || args.actor) + ": "
      + spanText(args.required) + " of effort required. No time passes" + (plan.after ? "; the clock stays at " + at(plan.after) + "." : ".")];
  }
  if (args.operation === "abandon") {
    return ["Abandon " + what + ": " + quoted(args.note) + ". No time passes and no effort is credited."];
  }
  const lines = ["Spend " + spanText(plan.elapsed) + " on " + what + ": the clock moves from " + at(plan.before) + " to "
    + at(plan.after) + ", and " + spanText(plan.credited) + " of effort is credited"
    + (plan.effort < plan.elapsed ? " (less effort than the time that passes)." : ".")];
  lines.push(plan.completes ? "It completes the activity." : "It does not complete the activity yet.");
  const flags = flagLine(plan.flags);
  if (flags) lines.push(flags);
  return lines;
}

// -- 076 offscreen plans ----------------------------------------------------------------------------------------

export function raceText(deadline) {
  if (!deadline) return "";
  if (!deadline.found) return "It races the deadline " + deadline.deadline + ", which the calendar no longer holds.";
  const due = (deadline.label || deadline.deadline) + " (due " + at(deadline.due) + ")";
  return "It races " + due + (deadline.race === "met" ? " and would finish in time." : " and would not finish in time.");
}

export function offscreenLines(args, plan) {
  if (plan.already_applied) return ["This change was already made: making it again changes nothing."];
  if (plan.kind === "skip") {
    const lines = ["Apply the calendar's advance from " + at(plan.before) + " to " + at(plan.after) + " to the adopted plans, with a budget of "
      + plural(plan.budget, "development") + "."];
    lines.push(plan.developments.length
      ? "Developments: " + plan.developments.map((item) => item.plan + ": " + (item.label || item.step) + " (due " + at(item.due) + ")").join("; ") + "."
      : "No step falls due in that span.");
    if (plan.deferred.length) lines.push("Deferred past the budget: " + plan.deferred.join(", ") + ".");
    for (const item of plan.deadlines) {
      lines.push(item.plan + " " + (item.outcome === "met" ? "met " : "missed ") + (item.label || item.deadline) + ".");
    }
    lines.push("A development is the director's alone: it delivers no knowledge, event or rumor, and no character learns of it.");
    return lines;
  }
  const lines = [who(plan.group) + " adopts the plan " + quoted(plan.goal) + ", as " + who(plan.decision.leader)
    + " decides: " + quoted(plan.decision.choice) + ". It starts at " + at(plan.starts) + " with priority " + plan.priority + "."];
  lines.push("Steps: " + plan.steps.map((step, n) => (n + 1) + ". " + step.label + ", due " + at(step.due)).join("; ") + ".");
  if (plan.decision.dissent.length) lines.push("Dissenting: " + plan.decision.dissent.map((item) => who(item) + " (" + item.choice + ")").join(", ") + ".");
  if (plan.deadline) lines.push(raceText(plan.deadline));
  return lines;
}

// -- 077 commitments --------------------------------------------------------------------------------------------

const STATUS = { offered: "Offered", accepted: "Accepted", refused: "Refused", partially_fulfilled: "Partly fulfilled",
  fulfilled: "Fulfilled", abandoned: "Abandoned", lapsed: "Lapsed", voided: "Voided", unsupported: "Unsupported" };
export function statusText(status) { return STATUS[status] || status; }

// What became of one recorded step, when it does not count: "" for an applied one.
const OUTCOME = { unsupported: "Skipped: it cites fiction that was undone.", stranded: "Skipped: it followed only from a skipped step.",
  voided: "Voided: withdrawn as recorded in error." };
export function outcomeText(outcome) { return outcome === "applied" ? "" : OUTCOME[outcome] || "Skipped: " + outcome + "."; }

// A commitment's deadline as the calendar holds it now, with its state: { text, state } where state is "none",
// "missing" (the calendar holds no such deadline: never an all-clear), "open", "passed", "overdue" or "late".
export function deadlineState(item) {
  const deadline = item.deadline;
  if (!deadline) return { text: "No deadline.", state: "none" };
  if (!deadline.found) return { text: "Due by " + deadline.deadline + ", which the calendar does not hold.", state: "missing" };
  const due = (deadline.label || deadline.deadline) + ", " + at(deadline.due);
  if (item.late) return { text: "Due by " + due + ": it was settled after the deadline passed.", state: "late" };
  if (item.overdue) return { text: "Due by " + due + ": the deadline passed while it is still open.", state: "overdue" };
  if (deadline.passed) return { text: "Due by " + due + ": the deadline has passed.", state: "passed" };
  return { text: "Due by " + due + ".", state: "open" };
}

export function commitmentLines(args, plan, { commitment = null } = {}) {
  if (plan.already_applied) return ["This change was already made: making it again changes nothing."];
  const cites = plan.cites && plan.cites.length ? "Citing: " + plan.cites.map((item) => item.summary || item.event).join(" / ") + "." : null;
  if (plan.kind === "offer") {
    const lines = ["Offer the " + plan.type + " " + quoted(plan.summary) + ", owed by " + names(plan.owed_by) + " to " + names(plan.owed_to) + "."];
    if (plan.deadline) {
      lines.push((plan.deadline.schedules ? "It schedules a new deadline, " : "It is due by ")
        + (plan.deadline.label || plan.deadline.deadline) + (plan.deadline.due ? ", " + at(plan.deadline.due) : "") + ".");
    }
    return [...lines, cites].filter(Boolean);
  }
  const summary = commitment ? quoted(commitment.summary) : plan.commitment;
  if (plan.kind === "void") {
    return ["Void the " + (plan.withdraws === "commitment" ? "offer of " : "step " + plan.target + " of ") + summary
      + " as recorded in error: " + quoted(plan.reason) + ". It is withdrawn, never deleted."];
  }
  const decided = plan.decided_by && plan.decided_by.length ? ", as " + names(plan.decided_by) + " decided" : ", as the director adjudicates";
  const lines = ["Record " + summary + " as " + statusText(plan.status).toLowerCase() + decided + " (it is " + statusText(plan.from).toLowerCase() + " now)."];
  if (plan.note) lines.push("Note: " + quoted(plan.note) + ".");
  return [...lines, cites].filter(Boolean);
}

// Which of a commitment's next steps this page can record now: the director's outcomes always; a decision only
// when the obligors are the director's NPCs or the owner's own characters (`deciders.available`).
export function recordable(item, outcomes) {
  const outcome = new Set(outcomes);
  return item.next.filter((status) => outcome.has(status) || item.deciders.available);
}

// -- Wave 9 coverage --------------------------------------------------------------------------------------------

// time.coverage's rows sorted into where they are: `here` (this page), `elsewhere` (another registered page) and
// `waiting` (no screen yet, or its page is not in this app), each with the reason to say.
export function coverageRows(features, registered) {
  const here = [], elsewhere = [], waiting = [];
  for (const item of features) {
    if (item.status === "here") here.push(item);
    else if (item.status === "screen" && registered(item.route)) elsewhere.push(item);
    else waiting.push({ ...item, reason: item.reason || "its page is not in this app" });
  }
  return { here, elsewhere, waiting };
}
