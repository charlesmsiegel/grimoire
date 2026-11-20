// The story explorer's pure logic (081): no DOM and no calls, so it is tested by itself (tests/js/story.test.mjs).
// What a thread's entries, outcome and contradictions say in words, how each is graded on screen, where each
// entry leads back to, and the page's address.

export const AUDIENCES = [{ value: "table", label: "Table view" }, { value: "director", label: "Director view" }];

export const NOTES = {
  table: "What your own characters heard, passed on and concluded. How it ended is the director's.",
  director: "Every record of the story, with canon's ruling and where the story contradicts itself.",
};

// Each entry's grade, so an interpretation is never read as truth: [label, pill kind].
export const GRADES = {
  confirmed: ["Canon", "good"],
  belief: ["Belief", "accent"],
  proposal: ["Proposed", ""],
  revoked: ["Revoked", "bad"],
};

export function grade(entry) { return GRADES[entry.grade] || [entry.grade, ""]; }

// Canon's standing in words (012: a ruling, else reported once anyone heard it, else unasserted).
export const STANDINGS = {
  established: "Established",
  refuted: "Refuted",
  unknown: "Ruled unknown",
  reported: "Only reported",
  unasserted: "Not yet asserted",
};

export function standingText(standing) {
  if (standing === null || standing === undefined) return null;
  return STANDINGS[standing] || standing;
}

export function standingKind(standing) {
  return standing === "established" ? "good" : standing === "refuted" ? "bad" : "";
}

const STANCES = { believed: "believes it", suspected: "suspects it", rejected: "rejects it" };
const PAST = { believed: "believed it", suspected: "suspected it", rejected: "rejected it" };

function who(actor, fallback = "Someone") {
  return actor ? actor.name || actor.ref : fallback;
}

// One entry in words: what happened, who heard or concluded what. Its own text (the proposition or an event's
// summary) is shown beside this line, never folded into it.
export function entryLine(entry) {
  switch (entry.kind) {
    case "stated": return "First recorded";
    case "event": return entry.grade === "revoked" ? "Happened, then revoked" : "Happened";
    case "report": {
      const from = entry.attributed_to ? who(entry.attributed_to) : null;
      return who(entry.holder) + " heard it" + (from ? " from " + from : "") + (entry.confirmed === false ? " (unconfirmed)" : "");
    }
    case "sent": return who(entry.holder) + " passed it on";
    case "belief": {
      const said = entry.current === false
        ? (PAST[entry.stance] || entry.stance) + ", then changed their mind"
        : STANCES[entry.stance] || entry.stance;
      return who(entry.holder) + " " + said + (entry.inherited ? " (before you played them)" : "");
    }
    case "ruling": return "Canon ruled it " + (STANDINGS[entry.standing] || entry.standing).toLowerCase();
    default: return entry.kind;
  }
}

// Where an entry leads: the turn of its chat it was recorded at, when that turn is on the chat's line. A turn a
// fork choice set aside, or one the answer could not number, is said, never linked as if it were played.
export function sourceOf(entry) {
  const source = entry.source;
  if (!source) return { link: null, text: "Recorded outside any chat" };
  if (source.set_aside) return { link: null, text: "From a turn set aside" };
  if (!source.number) return { link: null, text: "From a chat turn that cannot be opened here" };
  return { link: { route: "chat", segments: [source.chat], query: { turn: source.number } }, text: "Turn " + source.number };
}

// A contradiction in words, naming the entries it is between by their own lines.
export function contradictionText(item, entries) {
  const byRecord = new Map(entries.map((entry) => [entry.record, entry]));
  const named = item.records.map((record) => byRecord.get(record)).filter(Boolean);
  switch (item.kind) {
    case "reversed_ruling": return "Canon reversed an earlier ruling.";
    case "belief_against_canon": {
      const belief = named.find((entry) => entry.kind === "belief");
      return (belief ? who(belief.holder) + " still " + (STANCES[belief.stance] || belief.stance) : "A character still disagrees")
        + ", against canon's ruling.";
    }
    case "revoked_ground": return "The ruling rests on an event that was revoked.";
    default: return item.kind;
  }
}

// The scenes of a thread, or only those that involve one actor (the first milestone: one character, one thread).
export function scenesWith(scenes, actor) {
  if (!actor) return scenes;
  return scenes.filter((scene) => scene.actors.some((member) => member.ref === actor));
}

// The page's address: `#/story/<chat>?audience=&q=&actor=&thread=`, with nothing for what is unset.
export function address(state, change = {}) {
  const next = { ...state, ...change };
  return {
    segments: [next.chat],
    query: {
      audience: next.audience === "director" ? "director" : undefined,
      q: next.q || undefined,
      actor: next.actor || undefined,
      thread: next.thread || undefined,
    },
  };
}

// The address read back: the same fields, with an audience other than the director's read as the table's.
export function readAddress(path, query) {
  const get = (key) => (query.get(key) || "").trim() || null;
  return { chat: path[0] || null, audience: get("audience") === "director" ? "director" : "table", q: get("q"),
    actor: get("actor"), thread: get("thread") };
}

export function count(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

// What a thread's row says under its text: where it stands (for the director), how far it runs and who is in it.
export function summaryLine(item) {
  const parts = [];
  const standing = standingText(item.standing);
  if (standing) parts.push(standing);
  parts.push(count(item.entries, "record"), count(item.scenes, "scene"));
  if (item.contradictions) parts.push(count(item.contradictions, "contradiction"));
  const names = item.actors.map((actor) => who(actor));
  if (names.length) parts.push(names.join(", "));
  return parts.join(" · ");
}

// A problem the answer reports, in words: shown, never hidden, so a partial thread is never taken for whole.
export const PROBLEMS = {
  knowledge_unreadable: "The story's ledger could not be read.",
  knowledge_damaged: "Part of the story's ledger is damaged: what is shown may be incomplete.",
  effects_unreadable: "Which accepted effects were revoked could not be read.",
  turns_unread: "Some turns could not be numbered, so they cannot be opened from here.",
  no_character: "You play no character in this campaign yet, so the table knows nothing of its story.",
};

export function problemText(problem) {
  return (PROBLEMS[problem.code] || problem.code) + (problem.detail ? " (" + problem.detail + ")" : "");
}

// One scene of a thread in words: which chat (this one, or another of the campaign's), and the turns of it the
// thread passes through, by number where they can be opened.
export function sceneText(scene, entries, chat) {
  const numbers = [];
  for (const entry of entries) {
    if (!scene.records.includes(entry.record) || !entry.source || !entry.source.number || entry.source.set_aside) continue;
    if (!numbers.includes(entry.source.number)) numbers.push(entry.source.number);
  }
  numbers.sort((a, b) => a - b);
  const where = scene.chat === null ? (scene.scene ? "Scene " + scene.scene : "Outside any chat")
    : scene.chat === chat ? "This chat" : "Another chat of this campaign";
  const turns = numbers.length === 0 ? "" : numbers.length === 1 ? ", turn " + numbers[0] : ", turns " + numbers.join(", ");
  return where + turns;
}
