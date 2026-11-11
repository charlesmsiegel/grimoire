// The chat's story so far: the "Last time" recap under the scene bar, the panel's Story tab (a timeline of
// bookmarks and where this device last left the chat) and the Bookmark action on a kept line. Everything shown
// is the answer of one op (play.story and the three bookmark ops, which answer the story again); nothing here
// asks a model, and the recap is the last lines verbatim, never a written summary.
//
// Where the last sitting ended is this device's own memory, not the library's (a turn records no time): the
// newest closed turn when the chat was last open in an earlier browser session. It is kept in localStorage per
// chat, and one sessionStorage flag marks the session, so a reload or coming back from another page in the same
// tab is the same sitting. With no memory (a first visit, cleared site data) the recap is the latest lines and
// the timeline has no "Last time you played".
//
// The page hooks in with makeStory(); the functions above it are pure and proved in chat_story.test.mjs.

const PREFIX = "grimoire.chat.left:";

// The id of the newest turn that is closed (kept or passed), or null: where a sitting that ends now would end.
export function closedThrough(scene) {
  for (let at = scene.turns.length - 1; at >= 0; at -= 1) if (!scene.turns[at].open) return scene.turns[at].turn;
  return null;
}

// What of the scene the story depends on: asked again when this changes (a kept, struck or passed line).
export function storyKey(scene) {
  return scene.turns.map((turn) => turn.turn + ":" + (turn.kept ? turn.kept.lines.map((line) => line.line).join(",") : turn.open ? "open" : "passed")).join("|");
}

function read(storage, key) {
  try { const found = JSON.parse(storage.getItem(key) || "null"); return found && typeof found === "object" ? found : {}; }
  catch (error) { return {}; }
}
function write(storage, key, value) {
  try { storage.setItem(key, JSON.stringify(value)); } catch (error) { /* a browser that keeps nothing: no last time */ }
}
const turnId = (value) => (typeof value === "string" && value ? value : null);

// This device's memory of where it left one chat. `local` outlives the session and `session` does not (either
// may be null). `since` is where the sitting before this one ended (null when unknown); `note(turn)` records
// where this one stands. The first call in a session starts a new sitting: the last sitting's end becomes `since`.
export function sitting(local, session, chat) {
  const key = PREFIX + chat;
  if (!local) return { since: null, note() {} };
  let memory = read(local, key);
  let fresh = true;
  try { fresh = !session || session.getItem(key) !== "1"; } catch (error) { fresh = true; }
  if (fresh) {
    memory = { previous: turnId(memory.current) || turnId(memory.previous), current: turnId(memory.current) };
    write(local, key, memory);
    try { if (session) session.setItem(key, "1"); } catch (error) { /* every visit is then a new sitting */ }
  }
  const since = turnId(memory.previous);
  return {
    since,
    note(turn) {
      if (!turn || memory.current === turn) return;
      memory = { previous: since, current: turn };
      write(local, key, memory);
    },
  };
}

// What the recap is, said plainly: a quotation, not a summary.
export function recapHeading(recap) {
  return recap.basis === "sitting" ? "The last lines from last time" : "The latest lines so far";
}

// The timeline's entries in the order of the story: each bookmark and, where this device knows it, "Last time
// you played". A bookmark whose turn is gone comes last and says so. The chat records no scene changes, so
// there are no scene entries.
export function timeline(story) {
  const entries = story.bookmarks.map((item) => ({
    kind: "bookmark", key: "bookmark:" + item.id, id: item.id, etag: item.etag, turn: item.turn, missing: item.missing,
    number: item.number, label: item.label ? "Bookmark: " + item.label : "Bookmark", name: item.label || "",
    at: item.missing ? "Its turn is no longer in this chat" : "Turn " + item.number,
  }));
  if (story.last_time) {
    entries.push({ kind: "last", key: "last", turn: story.last_time.turn, missing: false, number: story.last_time.number,
      label: "Last time you played", at: "Turn " + story.last_time.number });
  }
  const rank = (entry) => (entry.kind === "last" ? 1 : 0);
  return entries.sort((a, b) => (a.missing - b.missing) || ((a.number || 0) - (b.number || 0)) || (rank(a) - rank(b))
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

// The newest turn with something kept: what "Bookmark this moment" marks. Null when nothing is kept yet.
export function newestKept(scene) {
  for (let at = scene.turns.length - 1; at >= 0; at -= 1) if (scene.turns[at].kept) return scene.turns[at];
  return null;
}

export const hasBookmark = (story, turn) => Boolean(story) && story.bookmarks.some((item) => item.turn === turn);

// Bring one turn's rows to the top of the transcript, mark them and move focus there. False when the turn has
// no row on screen.
export function scrollToTurn(rows, turn) {
  const found = Array.from(rows.querySelectorAll("div[data-turn]")).filter((node) => node.getAttribute("data-turn") === turn);
  for (const node of Array.from(rows.querySelectorAll("div.chat-jumped"))) node.classList.remove("chat-jumped");
  if (!found.length) return false;
  for (const node of found) node.classList.add("chat-jumped");
  const first = found[0];
  if (typeof first.scrollIntoView === "function") first.scrollIntoView({ block: "start" });
  first.setAttribute("tabindex", "-1");
  first.focus();
  return true;
}

function storages() {
  let local = null;
  let session = null;
  try { local = window.localStorage || null; } catch (error) { local = null; }
  try { session = window.sessionStorage || null; } catch (error) { session = null; }
  return { local, session };
}

// `run(work)` is the chat page's way to run one action, `redraw()` draws the chat again when the story
// arrives, `rows` holds the transcript's rows and `afterJump()` runs once a timeline entry was jumped to.
export function makeStory(ctx, chat, { run, redraw, rows, tint, afterJump }) {
  const { ui } = ctx;
  const { el } = ui;
  const served = ctx.has("play.story");
  const recap = el("div", { class: "chat-recap " + (tint || ""), hidden: true });
  const button = el("button", { type: "button", class: "chat-scene-button chat-recap-toggle", "aria-expanded": "false", hidden: true,
    "data-focus-key": "chat-last-time", onclick: () => setOpen(!state.open) }, "Last time");
  if (!served) {
    return { served, recap, button, update() {}, anchor() {}, lineTool: () => null, tab: () => [] };
  }
  ctx.styles("story.css");
  const { local, session } = storages();
  const memory = sitting(local, session, chat);
  const state = { story: null, failed: false, key: null, open: false, current: null, renaming: null, removing: null, jumped: null };
  const args = (more) => ({ chat, ...(memory.since ? { since: memory.since } : {}), ...more });
  const adds = ctx.has("play.bookmark_add");

  function setOpen(open) {
    state.open = open;
    drawRecap();
    if (!open) button.focus();
  }

  function drawRecap() {
    const found = state.story && state.story.recap;
    button.hidden = !found;
    button.setAttribute("aria-expanded", String(Boolean(found) && state.open));
    recap.hidden = !(found && state.open);
    if (recap.hidden) { recap.replaceChildren(); return; }
    const lines = found.lines.map((line) => el("p", { class: "chat-recap-line" },
      el("span", { class: "chat-recap-speaker" }, line.speaker + (line.you ? " · you" : "") + ": "), line.text));
    recap.replaceChildren(
      el("div", { class: "chat-recap-text", role: "group", "aria-label": recapHeading(found) },
        el("div", { class: "chat-recap-head" }, recapHeading(found)), ...lines),
      el("button", { type: "button", class: "chat-scene-button", onclick: () => setOpen(false) }, "Hide"));
  }

  async function load() {
    try {
      state.story = await ctx.api("play.story", args());
      state.failed = false;
    } catch (error) {
      if (error && error.reason === "left") return;
      state.story = null;
      state.failed = true;
    }
    if (ctx.signal.aborted) return;
    drawRecap();
    redraw();
  }

  // One bookmark op: its answer is the story. A refusal (the bookmark changed on another device, say) is
  // shown by the page, and the story is read again so what is on screen is what is stored.
  async function change(op, more) {
    try { state.story = await ctx.api(op, args(more)); }
    catch (error) { if (!error || error.reason !== "left") load(); throw error; }
    drawRecap();
    return state.story;
  }

  function add(turn, { name = false } = {}) {
    return run(async () => {
      const before = new Set(state.story ? state.story.bookmarks.map((item) => item.id) : []);
      const story = await change("play.bookmark_add", { turn });
      const made = story.bookmarks.find((item) => !before.has(item.id));
      if (made && name) { state.renaming = made.id; state.current = "bookmark:" + made.id; }
    });
  }

  const tool = (label, onclick, name) => {
    const node = el("button", { type: "button", class: "chat-tool", onclick, "aria-label": name }, label);
    node.setAttribute("data-local", "");  // a note about the story, not play: taken while play is held up too
    return node;
  };

  function jump(entry) {
    state.current = entry.key;
    state.jumped = entry.turn;
    redraw();  // the entry is the current one; the rows are drawn again, so they are found after it
    scrollToTurn(rows, entry.turn);
    if (afterJump) afterJump();
  }

  function renameField(entry) {
    const input = el("input", { type: "text", class: "g-input chat-spine-name", maxlength: "120", "aria-label": "Name this bookmark",
      placeholder: "A short name (optional)", "data-focus-key": "chat-bookmark-name" });
    input.value = entry.name;
    const done = () => { state.renaming = null; redraw(); };
    const save = () => run(async () => {
      const label = input.value.trim();
      state.renaming = null;
      if (label !== entry.name) await change("play.bookmark_rename", { bookmark: entry.id, label: label || null, expected: entry.etag });
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); save(); }
      else if (event.key === "Escape") { event.preventDefault(); done(); }
    });
    setTimeout(() => { if (!ctx.signal.aborted && state.renaming === entry.id && !input.disabled) input.focus(); });
    return el("div", { class: "chat-spine-edit" }, input, el("span", { class: "chat-tools" }, tool("Save", save), tool("Cancel", done)));
  }

  function entryRow(entry) {
    const current = state.current === entry.key;
    const text = el("span", { class: "chat-spine-text" }, el("span", { class: "chat-spine-label" }, entry.label),
      el("span", { class: "chat-spine-at" }, entry.at));
    const dot = el("span", { class: "chat-spine-dot", "aria-hidden": "true" });
    const main = entry.missing ? el("div", { class: "chat-spine-jump chat-spine-gone" }, dot, text)
      : el("button", { type: "button", class: "chat-spine-jump", "data-local": "", "aria-current": current ? "true" : undefined,
        title: "Go to this moment", onclick: () => jump(entry) }, dot, text);
    const row = el("li", { class: "chat-spine-item" + (current ? " chat-current" : "") }, main);
    if (entry.kind !== "bookmark") return row;
    if (state.renaming === entry.id) { row.append(renameField(entry)); return row; }
    const tools = el("span", { class: "chat-tools" });
    if (state.removing === entry.id) {
      tools.append(tool("Remove it", () => run(async () => {
        state.removing = null;
        await change("play.bookmark_remove", { bookmark: entry.id, expected: entry.etag });
      }), "Remove this bookmark"), tool("Cancel", () => { state.removing = null; redraw(); }));
      tools.querySelector("button").classList.add("chat-danger");
    } else {
      if (ctx.has("play.bookmark_rename")) tools.append(tool("Rename", () => { state.renaming = entry.id; state.removing = null; redraw(); }, "Rename " + entry.label));
      if (ctx.has("play.bookmark_remove")) tools.append(tool("Remove", () => { state.removing = entry.id; state.renaming = null; redraw(); }, "Remove " + entry.label));
    }
    row.append(tools);
    return row;
  }

  function tab(scene) {
    if (!state.story) {
      return [el("p", { class: "g-muted chat-plain" }, state.failed ? "The story of this chat could not be read." : "Reading the story…"),
        state.failed ? ui.button("Try again", () => { state.failed = false; redraw(); load(); }, { size: "small" }) : null];
    }
    const entries = timeline(state.story);
    const out = [];
    if (entries.length) {
      out.push(el("ol", { class: "chat-spine", "aria-label": "Story so far" }, ...entries.map(entryRow)));
    } else {
      out.push(el("p", { class: "g-muted chat-plain" }, "No bookmarks yet. Bookmark a moment to find it again."));
    }
    const newest = newestKept(scene);
    if (adds && newest) {
      out.push(hasBookmark(state.story, newest.turn)
        ? el("p", { class: "g-muted chat-plain chat-spine-marked" }, "This moment is bookmarked.")
        : (() => {
          const node = ui.button("Bookmark this moment", () => add(newest.turn, { name: true }), { kind: "dashed", size: "small" });
          node.setAttribute("data-local", "");
          node.classList.add("chat-spine-add");
          return node;
        })());
    }
    const unread = state.story.problems.length;
    if (unread) out.push(el("p", { class: "g-muted chat-plain", role: "status" }, unread + " bookmark file" + (unread === 1 ? "" : "s") + " could not be read and " + (unread === 1 ? "is" : "are") + " left as found."));
    return out;
  }

  return {
    served,
    recap,
    button,
    // Called each time the chat is drawn: remembers where this sitting stands and reads the story when what
    // it depends on changed.
    update(scene) {
      memory.note(closedThrough(scene));
      const key = storyKey(scene);
      if (key !== state.key) { state.key = key; load(); }
    },
    // Mark the rows drawn for one turn, so a timeline entry can find them.
    anchor(nodes, turn) {
      for (const node of nodes) {
        node.setAttribute("data-turn", turn.turn);
        if (state.jumped === turn.turn) node.classList.add("chat-jumped");  // the mark outlives a redraw
      }
    },
    // The small action on a kept line: bookmark its turn, or say it is.
    lineTool(turn) {
      if (!adds || !state.story) return null;
      if (hasBookmark(state.story, turn.turn)) return el("span", { class: "chat-tool chat-tool-done" }, "Bookmarked");
      return tool("Bookmark", () => add(turn.turn), "Bookmark this moment of the story");
    },
    tab,
  };
}
