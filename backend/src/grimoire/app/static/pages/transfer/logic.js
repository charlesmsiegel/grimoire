// What the transfer page (084, Sprint 116) works out without the app: each actor's choices, the reviewed request
// and the words a scan or a plan is said in. Pure, so it runs under `node --test`.

// A new operation id, unique enough to name one copy: kept with its review, so a retried commit reuses it.
export function newOperationId() {
  return "op-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

const ROLES = { character: "character", persona: "your persona", actor: "creature" };

export function roleText(role) {
  return ROLES[role] || role;
}

// The choices for one origin actor: only the target's instances of the same definition may be reused (never a
// namesake), a new actor where 084 can create one, or leaving it out. Nothing is chosen for the director.
export function decisionOptions(member) {
  const options = [{ value: "", label: "Decide…" }];
  for (const item of member.candidates) {
    options.push({ value: item.actor, label: "Reuse " + (item.name || item.actor) + " (already in the target)" });
  }
  if (member.can_create) options.push({ value: "new", label: "Create a new " + (member.name || "actor") + " in the target" });
  options.push({ value: "leave", label: "Leave out (posts keep the name, with no actor)" });
  return options;
}

// The decisions kept from an earlier draft, dropping any that no longer fit what the scan offers.
export function readDecisions(saved, cast) {
  let parsed = {};
  try { parsed = saved ? JSON.parse(saved) : {}; } catch { parsed = {}; }
  const found = {};
  for (const member of cast) {
    const value = parsed && typeof parsed[member.actor] === "string" ? parsed[member.actor] : "";
    found[member.actor] = decisionOptions(member).some((item) => item.value === value) ? value : "";
  }
  return found;
}

// The plan request, or the first thing still to decide.
export function requestOf({ chat, target, decisions, cast, sources, label, operationId }) {
  if (!target) return { error: "Choose the campaign to copy into." };
  const undecided = cast.find((member) => !decisions[member.actor]);
  if (undecided) return { error: "Decide what happens to " + (undecided.name || undecided.actor) + "." };
  const request = { chat, target, audience: "director", operation_id: operationId,
    cast: Object.fromEntries(cast.map((member) => [member.actor, decisions[member.actor]])),
    sources: [...sources] };
  const text = String(label || "").trim();
  if (text) request.label = text;
  return { request };
}

// "t-c-2.000003" (a turn's post), "t-c-2.000002.1.1" (a kept line) or "page 1 p000002" (an imported post).
export function whereText(where) {
  const text = String(where || "");
  const page = /^page (\d+) p0*(\d+)$/.exec(text);
  if (page) return "imported page " + page[1] + ", post " + page[2];
  const line = /\.0*(\d+)\.(\d+)\.(\d+)$/.exec(text);
  if (line) return "turn " + line[1] + ", a kept line";
  const post = /\.0*(\d+)(?:\.p(\d+))?$/.exec(text);
  if (post) return "turn " + post[1] + (post[2] ? ", post " + post[2] : "");
  return text;
}

export function omittedText(item) {
  const where = whereText(item.where);
  if (item.reason === "private") return where + ": private (a whisper or a withheld action), never copied";
  if (item.reason === "unheard") {
    const names = item.missed.map((who) => who.name || who.actor).join(", ");
    return where + ": not heard by " + (names || "someone carried over") + ", so not copied";
  }
  return where + ": " + item.reason;
}

const LEFT = { effect: "an accepted effect", ruling: "a ruling and its decided outcome" };

export function leftText(item) {
  return whereText(item.where) + ": " + (LEFT[item.kind] || item.kind) + (item.detail ? " (" + item.detail + ")" : "")
    + " stays in the origin campaign";
}

const NOTES = {
  world_differs: "The target campaign plays another world, or none.",
  revision_differs: "A reused actor plays another revision in the target than the origin played.",
  new_beside_existing: "A new actor is created beside instances of the same definition already in the target.",
  snapshot: "The origin's snapshot reports a problem.",
};

export function noteText(note) {
  const known = NOTES[note.code];
  if (known) return known + (note.detail && note.code !== "world_differs" ? " " + note.detail : "");
  return note.code + (note.detail ? ": " + note.detail : "");
}

const REASONS = {
  same_campaign: "a copy goes to another campaign; within one campaign, fork the chat",
  different_individual: "that actor only shares the name: it is another individual",
  actor_mismatch: "that target actor is of another definition",
  foreign_actor: "that actor is not of the target campaign",
  merged_actors: "two origin actors cannot become one",
  unreviewed_actor: "every actor needs a decision",
  cannot_create: "this actor cannot be created in the target; leave it out",
  second_persona: "a chat has one persona: leave one out",
  same_definition_twice: "two actors would be one binding of one definition",
  history_incomplete: "the origin's history cannot all be read now",
  unknown_source: "the origin does not bind that source now",
  changed: "something changed since the review: plan the copy again",
  director_only: "copying a chat is the director's",
};

// The words a refusal is said in, with its reason kept.
export function reasonText(error) {
  const reason = error && error.reason;
  return (REASONS[reason] ? REASONS[reason] + " " : "") + "(" + (reason || "refused") + ")";
}
