// What the shared turn (058, Sprint 115) works out without the app: who is at the table, whose characters are
// whose, the drafts' order and the words a review is said in. Pure functions over the scene and the table.review
// answer, so they run under `node --test`.

const MODE_NAMES = { auto: "Auto", speech: "Speech", action: "Action" };

// A player as the screen names them: "you" for the library owner, else the name after "human:".
export function playerLabel(player, owner) {
  if (player === owner) return "you";
  return String(player || "").replace(/^human:/, "") || String(player);
}

export function modeLabel(mode) { return MODE_NAMES[mode] || mode; }

// The characters `player` controls in this scene now, in cast order: the ones they draft for.
export function partsOf(scene, player) {
  return scene.cast.filter((member) => member.role === "player" && member.controller === player);
}

// Whether the scene has a table of more than one character a person plays, or more than one person to play them,
// or drafts already held: the only times a shared turn is worth showing.
export function sharedTable(scene, players) {
  const pcs = scene.cast.filter((member) => member.role === "player").length;
  return pcs > 1 || (players && players.length > 1) || Boolean(scene.drafts && scene.drafts.length);
}

// The draft key of one player's unsent part for one character in one chat: a character handed to another
// player never shows the last player's unsent words.
export function partKey(chat, actor, player) { return "table:" + chat + ":" + actor + ":" + player; }

// The drafts as one comparable value (the order, each character, writer, words and mode).
export function draftsKey(drafts) {
  return JSON.stringify((drafts || []).map((item) => [item.actor, item.by, item.text, item.mode]));
}

// Why the held parts cannot be posted as a turn now, or null: a turn is posted only after the last one is closed.
export function postBlocker(scene) {
  const waiting = (scene.state && scene.state.waiting) || [];
  if (waiting.includes("paused")) return "Play is paused. Resume it to post the turn.";
  if (scene.state && scene.state.open_turn) return "Keep a take or pass the turn before the next turn is posted. The parts stay held.";
  return null;
}

// The id of the chat's last turn: the head a review is taken after (null before the first turn).
export function headOf(scene) {
  const turns = (scene && scene.turns) || [];
  return turns.length ? turns[turns.length - 1].turn : null;
}

// Whether a review on show may still be posted from: its drafts are the ones held, no turn was posted since it was
// taken, and nothing (an open take, a pause) stops a turn being posted now. A stale review is dropped, never posted.
export function reviewHolds(review, scene) {
  return Boolean(review && scene) && draftsKey(review.drafts) === draftsKey(scene.drafts)
    && headOf(scene) === review.head && postBlocker(scene) === null;
}

// The reviewed drafts as table.submit carries them.
export function reviewed(review) {
  return review.drafts.map((item) => ({ actor: item.actor, by: item.by, text: item.text, mode: item.mode }));
}

// The lines a review of the shared turn is said in: one per part, in posting order.
export function reviewLines(review) {
  return review.drafts.map((item, index) => (index + 1) + ". " + (item.name || item.actor) + ", by "
    + playerLabel(item.by, review.owner) + " (" + modeLabel(item.mode) + ")"
    + (item.ready ? "" : ": no longer theirs to play, so the turn would be refused"));
}

// Who a character may be handed to: the director, or anyone this device was started for.
export function seatOptions(players, owner) {
  return [{ value: "director", label: "The director (an NPC)" },
    ...players.map((player) => ({ value: player, label: player === owner ? "You" : playerLabel(player, owner) }))];
}
