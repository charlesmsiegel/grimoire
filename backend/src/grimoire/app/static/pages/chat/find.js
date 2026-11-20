// Opening a chat at one turn: `#/chat/<ref>?turn=<number>`, as the command palette's search over what was said
// links a line. The page marks each transcript row with its turn's number (`data-turn-number`); this scrolls the turn
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
  const found = Array.from(rows.querySelectorAll("div")).filter((node) => node.getAttribute("data-turn-number") === String(number));
  if (!found.length) return false;
  for (const node of found) node.classList.add(MARK);
  if (typeof found[0].scrollIntoView === "function") found[0].scrollIntoView({ block: "center" });
  timer(() => { for (const node of found) node.classList.remove(MARK); }, MARK_MS);
  return true;
}

// A page owns the highlight, not the replaceable transcript nodes. A late story/picture redraw
// inherits the remaining lifetime; it never starts another timer or scrolls the reader again.
export function trackTurn(number, { timer = setTimeout, cancel = clearTimeout } = {}) {
  let started = false, active = false, disposed = false, pending = null, marked = [];
  const clear = () => { for (const node of marked) node.classList.remove(MARK); marked = []; };
  function refresh(rows) {
    clear();
    if (!active || disposed) return false;
    marked = Array.from(rows.querySelectorAll("div")).filter((node) => node.getAttribute("data-turn-number") === String(number));
    for (const node of marked) node.classList.add(MARK);
    return marked.length > 0;
  }
  function start(rows) {
    if (started || disposed || number === null) return false;
    started = true;
    active = true;
    if (!refresh(rows)) { active = false; return false; }
    if (typeof marked[0].scrollIntoView === "function") marked[0].scrollIntoView({ block: "center" });
    pending = timer(() => { pending = null; active = false; clear(); }, MARK_MS);
    return true;
  }
  function dispose() {
    disposed = true;
    active = false;
    if (pending !== null) cancel(pending);
    pending = null;
    clear();
  }
  return { start, refresh, dispose };
}
