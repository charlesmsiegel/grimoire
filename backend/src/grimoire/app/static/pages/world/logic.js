// What the World page works out without the app (Sprint 115): ids, the guard that keeps a late answer from
// replacing a newer one, and the words its places, rumors and reviews are said in.

// A new operation id, unique enough to name one change: kept with its review so a retry reuses it.
export function newOperationId() {
  return "op-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

// One request at a time is current: `start()` names a new one and `current(turn)` says whether it is still the
// newest, so an answer that comes back after a newer request was made is dropped instead of drawn. `now()` names the
// newest without starting one: work begun from it is current while no request was started since.
export function latest() {
  let turn = 0;
  return { start: () => { turn += 1; return turn; }, current: (asked) => asked === turn, now: () => turn };
}

// The conditions a route is asked under, typed as words separated by commas or spaces ("ticket, night").
export function parseConditions(text) {
  return [...new Set(String(text || "").split(/[\s,]+/).map((word) => word.trim()).filter(Boolean))];
}

// A confidence typed as 0 to 1 ("0.8") or a percentage ("80%"), as a number from 0 to 1, or null.
export function parseConfidence(text) {
  const value = String(text === undefined || text === null ? "" : text).trim();
  const match = /^(\d{1,3}(?:\.\d+)?|\.\d+)(%?)$/.exec(value);
  if (!match) return null;
  const number = Number(match[1]) / (match[2] ? 100 : 1);
  return number >= 0 && number <= 1 ? number : null;
}

// A place as a person picks it: its name, and its exact ref too whenever another place shares the name (or it
// has none), because a name never stands for a place.
export function placeLabels(places) {
  const counts = new Map();
  for (const place of places) counts.set(place.name || "", (counts.get(place.name || "") || 0) + 1);
  const labels = new Map();
  for (const place of places) {
    const name = place.name || "";
    const label = !name ? place.place : counts.get(name) > 1 ? name + " (" + place.place + ")" : name;
    labels.set(place.place, label + (place.missing ? " (missing)" : ""));
  }
  return labels;
}

// One road in words, from the labels of its places: "Saltmarch to North Gate, both ways, needs: ticket".
export function pathText(path, labels) {
  const name = (ref) => labels.get(ref) || ref;
  return name(path.from) + " to " + name(path.to) + (path.two_way ? ", both ways" : "")
    + (path.requires.length ? ", needs: " + path.requires.join(", ") : "");
}

// The sentence a review of one road change is said in, from what world.path_plan answered.
export function pathPlanText(plan, labels) {
  if (plan.already_applied) return "This change was already made: making it again changes nothing.";
  return (plan.after.blocked ? "Block " : "Open ") + "the road \"" + plan.after.path + "\" (" + pathText(plan.after, labels)
    + ") in this campaign. The topology itself is not changed.";
}

const STATUS = { delivered: "Delivered", due: "Due now", on_the_way: "On the way", not_sent: "Not sent" };

export function rumorStatus(status) { return STATUS[status] || status; }

const at = (moment) => moment ? moment.date + " " + moment.time : "an unknown time";

// Who told whom, and when it was sent and arrives, on the campaign's calendar.
export function rumorLine(rumor) {
  const who = (person) => person ? person.name || person.actor : "someone";
  return who(rumor.sender) + " to " + who(rumor.recipient) + " by " + rumor.channel + ", sent " + at(rumor.sent)
    + ", arriving " + at(rumor.due);
}

// The route conditions a rumor needed to travel, in words.
export function routeText(route) {
  const held = [["proximity", "nearby"], ["affiliation", "shared affiliation"], ["channel", "a channel"],
    ["attention", "attention"]].filter(([key]) => route[key]).map(([, words]) => words);
  return held.length ? "Carried by " + held.join(", ") : "No route condition held";
}

// The hypotheses being written for one NPC, as kept in a draft (JSON), or [] when there is none or it is broken.
export function readHypotheses(text) {
  try {
    const value = JSON.parse(text || "[]");
    return Array.isArray(value) ? value.filter((item) => item && typeof item === "object" && typeof item.id === "string") : [];
  } catch { return []; }
}

// A list of ids kept in a draft (JSON), or [] when there is none or it is broken.
export function readList(text) {
  try {
    const value = JSON.parse(text || "[]");
    return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
  } catch { return []; }
}

// The moment a hypothesis is held from: its newest cited evidence (060 refuses a citation from its future).
export function heldFrom(evidenceIds, held) {
  const moments = held.filter((item) => evidenceIds.includes(item.evidence)).map((item) => item.at);
  return moments.length ? Math.max(...moments) : null;
}

// The moment a hypothesis is held at: from its newest cited evidence, and strictly after the hypothesis it replaces
// (060's ledger refuses a revision that is not later than what it supersedes).
export function heldAt(evidenceIds, held, replaced) {
  const from = heldFrom(evidenceIds, held);
  if (!replaced || replaced.at === null || replaced.at === undefined) return from;
  return from === null ? replaced.at + 1 : Math.max(from, replaced.at + 1);
}

// The hypotheses that replace `id` (060 needs a replaced hypothesis kept before what replaces it).
export function replacedBy(hypotheses, id) {
  return hypotheses.filter((item) => item.supersedes === id).map((item) => item.id);
}

// Why a new hypothesis about `about` can't replace `id`, or "" when it can. 060's ledger keeps one chain per NPC and
// actors: the replaced hypothesis is written before, nothing replaces it yet, and it is about the same characters.
export function replacementProblem(hypotheses, id, about) {
  if (!id) return "";
  const previous = hypotheses.find((item) => item.id === id);
  if (!previous) return id + " is not written here.";
  if (replacedBy(hypotheses, id).length) return id + " is already replaced: pick one nothing replaces yet.";
  if (JSON.stringify(previous.about) !== JSON.stringify(about)) return id + " is about other characters: a replacement keeps whose belief it is.";
  return "";
}

// The next unused hypothesis id ("h1", "h2", …).
export function nextHypothesisId(hypotheses) {
  const used = new Set(hypotheses.map((item) => item.id));
  let n = hypotheses.length + 1;
  while (used.has("h" + n)) n += 1;
  return "h" + n;
}

export function percent(value) { return Math.round(Number(value) * 100) + "%"; }

// 060's inferred actors run outer to inner: [A] is what A believes, [A, B] what A thinks B believes.
export function whose(names) {
  return names.length > 1 ? "what " + names[0] + " thinks " + names[1] + " believes" : "what " + (names[0] || "someone") + " believes";
}
