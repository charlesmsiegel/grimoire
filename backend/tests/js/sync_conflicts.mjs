// A keep-both conflict with three other devices, in fakedom with the ops answered here. It is shown and decided
// in the inbox (pages/inbox), with the Sync page's own views of it (pages/sync/versions.js): every version is
// shown, only the actions the feed offers are there (which those are is the feed's to say: test_app_inbox.py),
// and after keeping one the report lists this device's version as not kept and what the other devices still
// hold, even when reading the list again fails right after. The Sync page itself decides nothing: it says how
// many things from sync wait and links to the inbox. A second test runs "Sync now": it says "Syncing…" for as
// long as the request takes, a sync that could not reach the other device shows what happened with "Try again",
// and one that worked says what was exchanged.
// Run by test_media_pages.py.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const MINE = "1".repeat(64), PHONE = "2".repeat(64), TABLET = "3".repeat(64);
const PATH = "campaigns/mara-vell/art/portrait.json";
const placement = (etag) => ({ etag, deleted: false, preview: { placement: { mode: "set", image: null } } });
const CONFLICT = {
  path: PATH, kind: "placement", slot: "portrait", subject: "card:mara-vell/character/mara-vell",
  mine: placement(MINE),
  theirs: [{ ...placement(PHONE), peers: ["phone"] }, { ...placement(TABLET), peers: ["tablet"] }],
  peers: [{ peer: "phone", versions: [PHONE], deleted: false }, { peer: "tablet", versions: [TABLET], deleted: false },
    { peer: "watch", versions: [], deleted: true }],
  their_deletion: true,
};
const LEFT = { ...CONFLICT, mine: placement(PHONE), theirs: [{ ...placement(TABLET), peers: ["tablet"] }],
  peers: [CONFLICT.peers[1], CONFLICT.peers[2]] };

// The feed's item for a conflict, as `inbox.list` answers it: the conflict as `media.conflicts` lists it, and
// one action for each version that may be kept now.
const keep = (peer_, tag, mine, theirs) => ({ id: "resolve:" + peer_ + ":" + tag, label: "Keep " + peer_ + "'s", op: "media.resolve",
  args: { peer: peer_, path: PATH, keep: tag, mine, theirs }, done: "Kept " + peer_ + "'s version", kind: "", quick: false,
  confirm: { message: "Keep " + peer_ + "'s version of " + PATH + "? The rest of this conflict with " + peer_
    + " is set aside here and on " + peer_ + ", and listed after.", label: "Keep it", danger: false } });
const item = (conflict, actions, notes) => ({ id: "conflict:" + PATH, kind: "conflict", label: "Conflict", order: 0,
  title: "Mara Vell's portrait picture changed on more than one device",
  where: { text: "Harbor · with " + conflict.peers.map((side) => side.peer).join(", "), chat: null, campaign: "mara-vell", devices: [] },
  detail: "Both versions are kept until you choose.", notes, actions, conflict,
  route: { route: "art", path: [], query: { subject: conflict.subject }, label: "Open where it happened" } });
const feed = (items) => ({ items, count: items.length, complete: true, problems: [], scope: "library",
  inspected: { chats: 1, limit: 24, more_chats: false, more_items: false } });
const FIRST = item(CONFLICT, [keep("phone", PHONE, MINE, [PHONE, TABLET]), keep("tablet", TABLET, MINE, [PHONE, TABLET])],
  ["Several other versions stand, so this device's can be kept only on phone. Keeping one of theirs here lists this device's after, with the rest.",
    "watch deleted it. Resolve the other devices' versions first; then its deletion can be kept or set aside."]);
const SECOND = item(LEFT, [keep("tablet", TABLET, PHONE, [TABLET])],
  ["watch deleted it. Resolve the other devices' versions first; then its deletion can be kept or set aside."]);

test("a conflict with three devices: every version shown, nothing lost after keeping one, even if a refresh fails", async () => {
  install("http://127.0.0.1:9");
  const ui = await import("/static/shell/ui.js");
  const { href } = await import("/static/shell/router.js");
  const inbox = await import("/static/pages/inbox/page.js");
  const sync = await import("/static/pages/sync/page.js");
  const asked = [];
  let open = [FIRST];
  let failNext = false;  // the first refresh after the resolve fails
  const answers = {
    "media.devices": () => ({ device: "desk", device_problem: null, transport: { note: "Loopback only, not encrypted." },
      peers: ["phone", "tablet", "watch"].map((peer) => ({ peer, address: "127.0.0.1:1", paired: true, last: null })),
      unavailable: [] }),
    "inbox.list": () => {
      if (failNext) { failNext = false; throw Object.assign(new Error("unreachable"), { reason: "unavailable" }); }
      return feed(open);
    },
    "media.resolve": (args) => {
      open = [SECOND];
      failNext = true;
      return { peer: args.peer, path: args.path, status: "resolved", reason: null, code: null,
        not_kept: [{ ...placement(MINE), holder: "this device" }],
        still_open: { theirs: LEFT.theirs, peers: LEFT.peers } };
    },
  };
  const counts = [];
  const ctx = {
    ui, href, query: new URLSearchParams(), signal: new AbortController().signal,
    api: async (op, args) => { asked.push([op, args]); return answers[op](args); },
    act: async (work) => { try { await work(); } catch (error) { ctx.failed = error; } }, has: (op) => op in answers,
    registered: (route) => route === "inbox",
    picture: async () => null, styles() {}, setTitle() {}, setActions() {}, notify() {}, layout(name) { ctx.width = name; }, setCrumb() {},
    setCount(route, count) { counts.push([route, count && count.value]); }, refreshLibrary: async () => null,
  };
  const root = document.createElement("main");
  document.body.append(root);
  await inbox.mount(root, ctx);
  assert.equal(ctx.width, "reading");
  assert.deepEqual(counts.at(-1), ["inbox", 1], "the page keeps the rail's count");

  const card = root.querySelector("section.inbox-open");
  assert.ok(card.classList.contains("g-card") && card.classList.contains("g-accent"), "the open item is the card in accent");
  assert.ok(byText(card, "div", PATH).length, "the file is named");
  for (const holder of ["This device", "phone", "tablet", "watch"]) assert.ok(byText(card, "div", holder).length, holder);
  assert.ok(byText(card, "div", "Deleted").length, "the watch's deletion is shown beside the versions");
  const buttons = card.querySelectorAll("button").map((node) => node.textContent);
  assert.deepEqual(buttons, ["Keep phone's", "Keep tablet's"], "only what the feed offers: not this device's, not the deletion");
  assert.ok(card.textContent.includes("can be kept only on phone"));
  assert.ok(card.textContent.includes("Resolve the other devices' versions first"));

  byText(card, "button", "Keep phone's")[0].click();
  const dialog = await until(() => document.body.querySelector("dialog"), 5000, "the confirmation");
  assert.ok(dialog.textContent.includes("Keep phone's version of " + PATH));
  byText(dialog, "button", "Keep it")[0].click();
  const report = await until(() => byText(root, "section", "Resolved " + PATH)[0], 5000, "the report");
  const [, args] = asked.find(([op]) => op === "media.resolve");
  assert.deepEqual(args, { peer: "phone", path: PATH, keep: PHONE, mine: MINE, theirs: [PHONE, TABLET] });
  assert.ok(report.textContent.includes("Not kept:") && byText(report, "div", "This device").length);
  assert.ok(report.textContent.includes("Still in conflict with other devices:"));
  assert.ok(byText(report, "div", "tablet").length && byText(report, "div", "watch").length);
  await until(() => ctx.failed, 5000, "the refresh to fail");
  assert.equal(ctx.failed.reason, "unavailable", "the refresh failed, and the report is shown all the same");
  assert.ok(root.querySelector("span.inbox-struck"), "and what was decided is struck through");
  assert.ok(!root.querySelectorAll("button").some((node) => node.disabled), "nothing is left switched off");

  // once the page loads again: the conflict with the other devices still stands, with what the feed offers now
  const again = document.createElement("main");
  await inbox.mount(again, ctx);
  const still = again.querySelector("section.inbox-open");
  assert.deepEqual(still.querySelectorAll("button").map((node) => node.textContent), ["Keep tablet's"]);
  for (const holder of ["This device", "tablet", "watch"]) assert.ok(byText(still, "div", holder).length, holder);
  assert.ok(still.textContent.includes("Resolve the other devices' versions first"));

  // a resolve that could not reach the other device decides nothing, and says so in the item
  answers["media.resolve"] = (given) => ({ peer: given.peer, path: given.path, status: "incomplete", reason: "unreachable", code: "unreachable",
    kept: null, not_kept: [] });
  byText(still, "button", "Keep tablet's")[0].click();
  byText(await until(() => document.body.querySelector("dialog"), 5000, "the confirmation"), "button", "Keep it")[0].click();
  const refused = await until(() => again.querySelectorAll("div.g-notice").find((node) => node.textContent.includes("Not resolved: incomplete (unreachable). Nothing changed.")), 5000, "what happened");
  assert.ok(refused.classList.contains("g-bad"));
  assert.equal(again.querySelectorAll("span.inbox-struck").length, 0, "nothing is struck through");
  assert.ok(again.querySelector("section.inbox-open"), "the item still waits");

  // the Sync page decides none of it: one card says how many wait and where they are decided
  const page = document.createElement("main");
  await sync.mount(page, ctx);
  const waiting = page.querySelector('section[aria-label="Waiting from sync"]');
  assert.ok(waiting.textContent.includes("1 thing from sync is waiting"));
  assert.ok(waiting.textContent.includes("Conflicts and forks are decided in one place"));
  assert.equal(byText(waiting, "a", "Decide in Needs you")[0].getAttribute("href"), "#/inbox");
  assert.equal(page.querySelectorAll("article").length, 0);
  assert.ok(!page.querySelectorAll("button").some((node) => node.textContent.startsWith("Keep")), "no version is kept from here");
  // a feed that cannot be read is said in that card, and the devices are still shown
  failNext = true;
  await sync.mount(page, ctx);
  assert.ok(page.querySelector('section[aria-label="Waiting from sync"]').textContent.includes("What sync left waiting could not be read"));
  assert.ok(page.querySelector('section.sync-device[aria-label="phone"]'));
});

test("Sync now: Syncing… while the request runs, a failure with Try again, then what a sync exchanged", async () => {
  install("http://127.0.0.1:9");
  const ui = await import("/static/shell/ui.js");
  const { mount } = await import("/static/pages/sync/page.js");
  const report = (status, code, extra = {}) => ({ peer: "phone", status, reason: code, code, passes: 1, conflicts: [], copied: 0, deleted: [], ...extra });
  const outcomes = [
    () => report("incomplete", "unreachable"),
    () => { throw Object.assign(new Error("phone is not paired"), { reason: "not_paired" }); },
    () => report("conflicts", null, { copied: 3, conflicts: [{ path: PATH, code: "both_changed" }] }),
    () => report("synced", null, { copied: 12, deleted: ["a"] }),
  ];
  let last = null, release = null, synced = 0, refreshed = 0;
  const answers = {
    "media.devices": () => ({ device: "desk", device_problem: null, transport: { note: "Loopback only, not encrypted." },
      peers: [{ peer: "phone", address: "127.0.0.1:1", paired: true, last }, { peer: "tablet", address: null, paired: true, last: null }],
      unavailable: [] }),
    "media.sync": async (args) => {
      assert.deepEqual(args, { peer: "phone" });
      await new Promise((resolve) => { release = resolve; });
      const outcome = outcomes[synced++]();
      last = outcome;
      return outcome;
    },
  };
  const ctx = {
    ui, query: new URLSearchParams(), signal: new AbortController().signal,
    api: async (op, args) => answers[op](args),
    act: async (work) => { try { await work(); } catch (error) { ctx.failed = error; } }, has: (op) => op in answers, registered: () => false,
    picture: async () => null, styles() {}, setTitle(text) { ctx.title = text; }, setActions() {}, notify() {},
    layout(name) { ctx.width = name; }, setCrumb() {}, refreshLibrary: async () => { refreshed += 1; },
  };
  const root = document.createElement("main");
  document.body.append(root);
  await mount(root, ctx);
  assert.deepEqual([ctx.title, ctx.width], ["Devices and sync", "page"]);

  // a card for this device and for each other device; only one this device dials can be synced from here
  const device = (name) => root.querySelector(`section.sync-device[aria-label="${name}"]`);
  assert.ok(device("This device").textContent.includes("desk") && device("This device").textContent.includes("not encrypted"));
  assert.ok(device("phone").textContent.includes("Paired") && device("phone").textContent.includes("This device dials it at 127.0.0.1:1."));
  assert.ok(device("phone").textContent.includes("Not synced since the app started."));
  assert.ok(device("tablet").textContent.includes("It dials this device.") && !device("tablet").querySelector("button"));
  const notices = () => root.querySelectorAll("div.sync-notice");
  const run = async (button, done) => {
    button.click();
    await until(() => release, 5000, "the sync request");
    assert.equal(button.textContent, "Syncing…", "the button says so for as long as the request takes");
    const go = release; release = null; go();
    return until(done, 5000, "the sync's notice");
  };

  // nothing answered: a danger notice that says nothing changed, with Try again
  let notice = await run(byText(device("phone"), "button", "Sync now")[0], () => notices()[0]);
  assert.ok(notice.classList.contains("g-bad") && notice.getAttribute("role") === "alert");
  assert.ok(notice.textContent.includes("Couldn't reach phone") && notice.textContent.includes("Nothing was changed on either device."));
  await until(() => device("phone").textContent.includes("incomplete (unreachable)"), 5000, "the card's last sync");
  assert.equal(byText(device("phone"), "button", "Sync now")[0].textContent, "Sync now", "the button is itself again");

  // Try again runs the same sync; a refusal before anything was exchanged is shown with the app's reason
  notice = await run(byText(notices()[0], "button", "Try again")[0], () => notices().find((node) => node.textContent.includes("did not start")));
  assert.ok(notice.textContent.includes("not_paired: phone is not paired") && byText(notice, "button", "Try again").length);
  assert.equal(ctx.failed, undefined, "the failure is shown on the page, not thrown past it");

  // a sync that ends with conflicts worked: it says how many are kept to decide, and offers no retry
  notice = await run(byText(notices()[0], "button", "Try again")[0], () => notices().find((node) => node.classList.contains("g-soft")));
  assert.ok(notice.textContent.includes("3 files were copied") && notice.textContent.includes("1 file in conflict: both versions are kept"));
  assert.equal(byText(notice, "button", "Try again").length, 0);
  assert.ok(device("phone").textContent.includes("synced, 1 file in conflict"));

  // and a quiet one says what was exchanged and that nothing waits
  notice = await run(byText(device("phone"), "button", "Sync now")[0], () => notices().find((node) => node.textContent.includes("12 files")));
  assert.ok(notice.textContent.includes("Synced with phone just now. 12 files were copied and 1 deleted, with nothing new to resolve."));
  assert.ok(notice.classList.contains("g-soft") && notice.querySelector("span.g-dot.g-good"));
  assert.equal(notices().length, 1, "only the last sync's notice is shown");
  assert.equal(refreshed, 3, "the shell's library facts are read again after each sync that answered");
});
