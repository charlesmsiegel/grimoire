// Opening a chat at one turn: `#/chat/<ref>?turn=<number>`, as the command palette's search over what was said
// links a line. The page marks each transcript row with its turn's number (`data-turn`); this scrolls the turn
// into view and marks its rows for a moment. A turn the chat does not show (set aside, or not there) does nothing.

const MARK = "chat-found";
const MARK_MS = 4000;

// The turn number an address asks for, or null.
export function wantedTurn(query) {
  const text = query && query.get("turn");
  return text && /^[1-9][0-9]{0,8}$/.test(text) ? Number(text) : null;
}

// Scroll the turn's first row into view and mark its rows; true when the turn is shown.
export function showTurn(rows, number, { timer = setTimeout } = {}) {
  const found = Array.from(rows.querySelectorAll("div")).filter((node) => node.getAttribute("data-turn") === String(number));
  if (!found.length) return false;
  for (const node of found) node.classList.add(MARK);
  if (typeof found[0].scrollIntoView === "function") found[0].scrollIntoView({ block: "center" });
  timer(() => { for (const node of found) node.classList.remove(MARK); }, MARK_MS);
  return true;
}
