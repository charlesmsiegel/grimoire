// The inbox's shared model (pages/inbox/feed.js) and the chat's waiting tray (pages/chat/tray.js) with canned
// answers, in fakedom: an item is decided by its own op with its own arguments, after its own question; a no
// decides nothing; a resolve that did not go through leaves the item waiting; the tray shows one row for the
// chat's first item, its quick actions only, and leaves out what the chat already says itself. Run by
// test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(here, "..", "..", "src", "grimoire", "app", "static");
install("http://127.0.0.1:1");
const ui = await import(path.join(STATIC, "shell", "ui.js"));
const { href } = await import(path.join(STATIC, "shell", "router.js"));
const feed = await import(path.join(STATIC, "pages", "inbox", "feed.js"));
const { makeTray } = await import(path.join(STATIC, "pages", "chat", "tray.js"));

const CHAT = "campaign:c1#chat/t1";
const action = (overrides = {}) => ({ id: "confirm", label: "Confirm the labelling", op: "campaign.confirm",
  args: { chat: CHAT, audience: "table" }, confirm: null, done: "Labelling confirmed", kind: "", quick: true, ...overrides });
const item = (kind, id, actions, label = "Action") => ({ id, kind, label, title: "Anya slips past the lantern", order: 0,
  where: { text: "Harbor · turn 3", chat: CHAT, campaign: "c1", devices: [] }, detail: "", notes: [], actions,
  route: { route: "campaign", path: ["c1"], query: { chat: CHAT }, label: "Open where it happened" } });
const answer = (items, more = {}) => ({ items, count: items.length, complete: true, problems: [], scope: "library",
  inspected: { chats: 2, limit: 24, more_chats: false, more_items: false, ...more.inspected }, ...more });

function context(api, { ops = ["inbox.list"], routes = ["inbox", "campaign"] } = {}) {
  const seen = { calls: [], counts: [], notices: [] };
  const ctx = {
    ui, href, has: (op) => ops.includes(op), registered: (route) => routes.includes(route), styles() {},
    api: async (op, args) => { seen.calls.push([op, args]); return api(op, args); },
    setCount(route, count) { seen.counts.push([route, count]); }, notify: (text) => seen.notices.push(text),
  };
  return { ctx, seen };
}

test("the feed is served only with its op and its page, and reading it keeps the rail's count", async () => {
  assert.equal(feed.served(context(() => null).ctx), true);
  assert.equal(feed.served(context(() => null, { ops: [] }).ctx), false);
  assert.equal(feed.served(context(() => null, { routes: [] }).ctx), false);
  const { ctx, seen } = context(() => answer([item("action", "a", []), item("approval", "b", [])]));
  await feed.loadFeed(ctx);
  assert.deepEqual(seen.calls, [["inbox.list", {}]]);
  assert.deepEqual(seen.counts, [["inbox", { value: 2, label: "2 things need you", bad: true }]]);
  // one chat's own items say nothing of the rest: the count is left alone
  await feed.loadFeed(ctx, { chat: CHAT });
  assert.deepEqual(seen.calls[1], ["inbox.list", { chat: CHAT }]);
  assert.equal(seen.counts.length, 1);
  const none = context(() => answer([]));
  await feed.loadFeed(none.ctx);
  assert.deepEqual(none.seen.counts, [["inbox", null]], "nothing waiting clears it");
  assert.equal(feed.countLabel(1), "1 thing needs you");
  assert.equal(feed.itemHref(ctx, "label:" + CHAT + ":t"), "#/inbox?" + new URLSearchParams({ item: "label:" + CHAT + ":t" }));
  assert.equal(feed.itemHref(ctx), "#/inbox");
  assert.equal(feed.itemId.effect(CHAT, "t1.000001", 2), "effect:" + CHAT + ":t1.000001:2");
  assert.equal(feed.whereHref(ctx, item("action", "a", [])), "#/campaign/c1?" + new URLSearchParams({ chat: CHAT }));
  assert.equal(feed.whereHref(context(() => null, { routes: ["inbox"] }).ctx, item("action", "a", [])), null, "a screen this app lacks is not linked");
  assert.deepEqual(feed.fromSync(answer([item("fork", "f", []), item("action", "a", []), item("conflict", "c", [])])).map((entry) => entry.id), ["f", "c"]);
});

test("an item is decided by its own op and arguments, after its own question; a no decides nothing", async () => {
  const { ctx, seen } = context((op) => (op === "media.resolve" ? { status: "incomplete", reason: "unreachable" } : { done: {}, hub: {} }));
  const plain = await feed.perform(ctx, item("action", "a", []), action());
  assert.deepEqual(seen.calls, [["campaign.confirm", { chat: CHAT, audience: "table" }]]);
  assert.deepEqual([plain.decided, plain.text], [true, "Labelling confirmed"]);

  const asks = action({ op: "campaign.endorse_effect", args: { chat: CHAT, fingerprint: "e".repeat(64) }, done: "Your key is given",
    confirm: { message: "Give your key to effect 1, exactly as shown: Anya pays Mara a coin.", label: "Endorse", danger: false } });
  let pending = feed.perform(ctx, item("approval", "b", []), asks);
  let dialog = await until(() => document.body.querySelector("dialog"), 2000, "the question");
  assert.ok(dialog.textContent.includes("exactly as shown: Anya pays Mara a coin."));
  byText(dialog, "button", "Cancel")[0].click();
  assert.equal(await pending, null);
  assert.equal(seen.calls.length, 1, "nothing was called");
  pending = feed.perform(ctx, item("approval", "b", []), asks);
  dialog = await until(() => document.body.querySelector("dialog"), 2000, "the question again");
  byText(dialog, "button", "Endorse").at(-1).click();
  assert.equal((await pending).decided, true);
  assert.deepEqual(seen.calls[1], ["campaign.endorse_effect", { chat: CHAT, fingerprint: "e".repeat(64) }]);

  // a resolve that could not reach the other device answers, with nothing changed: the item still waits
  const resolve = action({ op: "media.resolve", args: { peer: "phone" }, done: "Kept phone's version" });
  const stuck = await feed.perform(ctx, item("conflict", "c", []), resolve);
  assert.deepEqual([stuck.decided, stuck.text], [false, "Not decided: incomplete (unreachable). Nothing changed."]);
  // a refusal rejects, as any call does
  const refusing = context(() => { throw Object.assign(new Error("moved on"), { reason: "changed" }); });
  await assert.rejects(feed.perform(refusing.ctx, item("action", "a", []), action()), { reason: "changed" });
});

test("a list that stopped early says so", () => {
  assert.deepEqual(feed.feedNotes(answer([])), []);
  const notes = feed.feedNotes(answer([], { inspected: { chats: 24, more_chats: true }, problems: [
    { source: "sync", campaign: "c1", code: "conflicts_unknown" }, { source: "sync", campaign: "c2", code: "conflicts_unknown" },
    { source: "sync", code: "device_identity" }, { source: "campaign", chat: CHAT, code: "knowledge_unreadable" },
    { source: "chat", chat: CHAT, code: "no_chat" }] }));
  const text = notes.map((note) => note.text).join("\\n");
  assert.ok(text.includes("sync records for c1, c2 cannot be read in full"), text);
  assert.ok(text.includes("could not be read (device_identity)") && text.includes("(knowledge_unreadable): " + CHAT) && text.includes("(no_chat): " + CHAT));
  assert.ok(text.includes("Only the 24 chats played last are checked."));
  assert.deepEqual(notes.map((note) => note.bad), [true, true, true, true, false]);
});

// The chat page's side of the tray: it runs one action at a time, reads the chat again, and draws when told.
function chatPage(api, options) {
  const { ctx, seen } = context(api, options);
  const page = { reopened: 0, redrawn: 0 };
  const tray = makeTray(ctx, CHAT, { run: async (work) => { try { await work(); } catch (error) { page.failed = error; } },
    reopen: async () => { page.reopened += 1; }, redraw: () => { page.redrawn += 1; } });
  return { tray, seen, page };
}
const scene = (turns = 3, open = null) => ({ turns: new Array(turns).fill({}), state: { open_turn: open, waiting: [] } });

test("the tray shows the chat's first item with its quick actions, and what the chat already says is left out", async () => {
  let items = [item("fork", "fork", [action({ label: "Go on with this device's" })], "Fork"),
    item("reply", "reply", [action({ label: "Pass the turn" })], "Cut-off reply"),
    item("action", "label", [action()]), item("approval", "effect", [action({ label: "Endorse as shown", quick: false })], "Your approval")];
  const { tray, seen, page } = chatPage((op) => (op === "inbox.list" ? answer(items, { scope: "chat" }) : { done: {}, hub: {} }));
  assert.equal(tray.view(scene()), null, "nothing until the chat's items are read");
  await until(() => page.redrawn === 1, 2000, "the tray's items");
  assert.deepEqual(seen.calls, [["inbox.list", { chat: CHAT }]], "this chat's items only");
  assert.deepEqual(seen.counts, [], "which say nothing of the rail's count");
  const row = tray.view(scene());
  assert.equal(seen.calls.length, 1, "the same chat is not asked about twice");
  assert.ok(row.classList.contains("chat-tray"));
  assert.equal(row.querySelector("span.g-pill").textContent, "Action");
  assert.equal(row.querySelector("span.chat-tray-title").textContent, "Anya slips past the lantern");
  assert.deepEqual(row.querySelectorAll("button").map((node) => node.textContent), ["Confirm the labelling"]);
  assert.ok(row.querySelector("button").classList.contains("g-primary") && row.querySelector("button").classList.contains("g-small"));
  assert.equal(byText(row, "a", "+1 more waiting")[0].getAttribute("href"), "#/inbox", "the fork and the reply are the chat's own to say");

  // the quick action is the item's own op; the chat is read again, and so is what waits on it
  items = items.slice(3);
  row.querySelector("button").click();
  await until(() => page.reopened === 1, 2000, "the chat read again");
  assert.deepEqual(seen.calls[1], ["campaign.confirm", { chat: CHAT, audience: "table" }]);
  const next = tray.view(scene());
  await until(() => seen.calls.length === 3, 2000, "the items read again");
  // a change is read in full before its key is given: the tray links to it and offers no button
  assert.equal(next.querySelector("span.g-pill").textContent, "Your approval");
  assert.equal(next.querySelectorAll("button").length, 0);
  assert.equal(byText(next, "a", "Decide")[0].getAttribute("href"), "#/inbox?" + new URLSearchParams({ item: "effect" }));
  assert.equal(byText(next, "a", "more waiting").length, 0);

  // the chat moving on (a new turn) asks again; the same scene drawn again does not
  tray.view(scene());
  assert.equal(seen.calls.length, 3);
  items = [];
  tray.view(scene(4, "t1.000004"));
  await until(() => seen.calls.length === 4 && page.redrawn >= 2, 2000, "the items after a new turn");
  assert.equal(tray.view(scene(4, "t1.000004")), null, "nothing waits: no row");
});

test("an app with no inbox has no tray, and a tray that cannot be read shows nothing", async () => {
  const without = chatPage(() => { throw new Error("not served"); }, { ops: [] });
  assert.equal(without.tray.view(scene()), null);
  assert.deepEqual(without.seen.calls, []);
  const broken = chatPage(() => { throw Object.assign(new Error("no"), { reason: "internal" }); });
  assert.equal(broken.tray.view(scene()), null);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(broken.tray.view(scene()), null);
  assert.equal(broken.page.failed, undefined);
});
