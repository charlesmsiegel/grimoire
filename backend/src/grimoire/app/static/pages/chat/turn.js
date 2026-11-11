// What the chat page shows of the open turn, worked out from the scene alone (no DOM): which takes can be
// stepped through and which one is on show, whether a reply is being written or did not arrive, and what the
// composer says and allows. The page draws these; chat_turn.test.mjs proves them.

// What stops play until someone acts: no new post, answer or pass is taken meanwhile.
export const STOPS = ["paused", "forked", "conflicted", "turns_unreadable"];

// The turn still open (waiting on a keep or a pass), or null.
export function openTurn(scene) {
  const id = scene.state.open_turn;
  if (id === null || id === undefined) return null;
  return scene.turns.find((turn) => turn.turn === id) || null;
}

// Whether play is held up: no seat, or one of the STOPS.
export function isStopped(scene) {
  return !scene.state.seated || scene.state.waiting.some((item) => STOPS.includes(item));
}

// A pause holds new play, not corrections: a skip, a redirect or a strike is still taken while paused.
export function isCorrectable(scene) {
  return Boolean(scene.state.seated) && scene.state.waiting.every((item) => item === "paused" || !STOPS.includes(item));
}

// The reply to the open turn as the page shows it.
//   turn     the open turn, or null
//   takes    the takes with a line to read, oldest first: what the stepper steps through
//   writing  a reply is being written now (`writing`: this page asked and waits; or the scene has a take running)
//   failed   null, or why the last ask gave nothing: { kind: "error", text } (the ask itself was refused or
//            lost), { kind: "interrupted" } (cut off before it finished) or { kind: "calls", calls } (every
//            call of the newest take ended without a line)
//   unanswered  a turn is open with nothing to keep, nothing being written and nothing known to have failed
export function replyState(scene, { writing = false, failure = null } = {}) {
  const turn = openTurn(scene);
  const offered = turn ? turn.offered : [];
  const interrupted = scene.state.waiting.includes("interrupted");
  const settled = offered.filter((take) => take.state !== "running");
  const takes = settled.filter((take) => take.lines.length);
  const running = offered.some((take) => take.state === "running") && !interrupted;
  const busy = writing || running;
  const newest = settled.reduce((found, take) => (!found || take.take > found.take ? take : found), null);
  let failed = null;
  if (!busy) {
    if (failure) failed = { kind: "error", text: failure.text };
    else if (interrupted) failed = { kind: "interrupted" };
    else if (newest && !newest.lines.length && newest.calls.length) failed = { kind: "calls", calls: newest.calls };
  }
  return { turn, takes, writing: busy, failed, unanswered: Boolean(turn) && !takes.length && !busy && !failed };
}

// The take on show: the one chosen if it is still offered, else the newest.
export function shownTake(takes, chosen) {
  if (!takes.length) return null;
  return takes.find((take) => take.take === chosen) || takes[takes.length - 1];
}

// The take number `by` steps from the one on show (-1 the one before, 1 the one after), going round at the ends.
export function stepTake(takes, chosen, by) {
  const shown = shownTake(takes, chosen);
  if (!shown) return null;
  const at = takes.indexOf(shown);
  return takes[(at + by + takes.length) % takes.length].take;
}

// "Take 2 of 3": where the take on show sits among the takes to step through.
export function takeLabel(takes, shown) {
  return "Take " + (takes.indexOf(shown) + 1) + " of " + takes.length + (shown.origin === "director" ? " · director" : "");
}

// What the composer allows and says. A post is taken only with a character to play, a seat, play not held up,
// no reply being written and the last turn closed; the placeholder says which of those is in the way.
//   canPost      whether Post (and Ctrl+Enter) sends
//   canAsk       whether a reply may be asked for (the page still needs a model to ask)
//   placeholder  what the empty composer says
// `asks` is whether a reply can be asked for at all (a model is set up).
export function composerState(scene, reply, { asks = true } = {}) {
  const waiting = scene.state.waiting;
  const persona = scene.persona;
  let why = null;
  if (!persona) why = "This chat has no character for you to play, so it takes no posts";
  else if (!scene.state.seated) why = "Take your seat before you post";
  else if (waiting.includes("paused")) why = "Play is paused. Resume to post";
  else if (waiting.some((item) => STOPS.includes(item))) why = "Play is held up. See the notice above";
  else if (reply.writing) why = "The table is answering…";
  else if (reply.takes.length) why = "Keep a take or pass the turn before you post again";
  else if (reply.turn) why = asks ? "Pass the turn or ask for a reply before you post again" : "Pass the turn before you post again";
  const idle = persona && persona.name ? "As " + persona.name + ": say or do something" : "Say or do something";
  return { canPost: why === null, canAsk: asks && !isStopped(scene) && !reply.writing, placeholder: why || idle };
}

// The keys that act on the take on show, when focus is not in a field: which of the card's actions a key means.
export function takeKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return null;
  switch (event.key) {
    case "ArrowLeft": return "prev";
    case "ArrowRight": return "next";
    case "Enter": return "keep";
    case "r": case "R": return "again";
    case "p": case "P": return "pass";
    default: return null;
  }
}
