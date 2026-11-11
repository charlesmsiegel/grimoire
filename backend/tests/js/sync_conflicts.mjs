// 114's Sync page over a keep-both conflict with three other devices, in fakedom with the page's ops answered
// here: every version is shown, keeping this device's is not offered while several others stand (the text API
// refuses it), a deletion waits behind the others' versions, and after keeping one the report lists this
// device's version as not kept and what the other devices still hold, even when refreshing the page fails right
// after. A second test runs "Sync now": it says "Syncing…" for as long as the request takes, a sync that could not
// reach the other device shows what happened with "Try again", and one that worked says what was exchanged.
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

test("a conflict with three devices: every version shown, nothing lost after keeping one, even if a refresh fails", async () => {
  install("http://127.0.0.1:9");
  const ui = await import("/static/shell/ui.js");
  const { mount } = await import("/static/pages/sync/page.js");
  const asked = [];
  let open = [CONFLICT];
  let failNext = false;  // the first refresh after the resolve fails
  const answers = {
    "media.devices": () => ({ device: "desk", device_problem: null, transport: { note: "Loopback only, not encrypted." },
      peers: ["phone", "tablet", "watch"].map((peer) => ({ peer, address: "127.0.0.1:1", paired: true, last: null })),
      unavailable: [] }),
    "media.conflicts": () => ({ conflicts: open, unknown: [] }),
    "media.forks": () => {
      if (failNext) { failNext = false; throw Object.assign(new Error("unreachable"), { reason: "unavailable" }); }
      return { forks: [], more: false, problems: [] };
    },
    "media.resolve": (args) => {
      open = [LEFT];
      failNext = true;
      return { peer: args.peer, path: args.path, status: "resolved", reason: null, code: null,
        not_kept: [{ ...placement(MINE), holder: "this device" }],
        still_open: { theirs: LEFT.theirs, peers: LEFT.peers } };
    },
  };
  const ctx = {
    ui, query: new URLSearchParams(), signal: new AbortController().signal,
    api: async (op, args) => { asked.push([op, args]); return answers[op](args); },
    act: async (work) => { try { await work(); } catch (error) { ctx.failed = error; } }, has: (op) => op in answers, registered: () => false,
    picture: async () => null, styles() {}, setTitle() {}, setActions() {}, notify() {}, layout() {}, setCrumb() {},
    refreshLibrary: async () => null,
  };
  const root = document.createElement("main");
  document.body.append(root);
  await mount(root, ctx);

  const card = byText(root, "article", PATH).pop();
  assert.ok(card.classList.contains("g-card"), "a conflict is a card");
  for (const holder of ["This device", "phone", "tablet", "watch"]) assert.ok(byText(card, "div", holder).length, holder);
  assert.ok(byText(card, "div", "Deleted").length, "the watch's deletion is shown beside the versions");
  const buttons = card.querySelectorAll("button").map((node) => node.textContent);
  assert.ok(!buttons.some((label) => label.startsWith("Keep this device")), "keeping this device's is not offered");
  assert.ok(buttons.includes("Keep phone's") && buttons.includes("Keep tablet's"));
  assert.ok(!buttons.some((label) => label.startsWith("Accept")), "the deletion waits behind the others' versions");
  assert.ok(card.textContent.includes("can be kept only on phone"));

  byText(card, "button", "Keep phone's")[0].click();
  const dialog = await until(() => document.body.querySelector("dialog"), 5000, "the confirmation");
  byText(dialog, "button", "Keep it")[0].click();
  const report = await until(() => byText(root, "section", "Resolved " + PATH)[0], 5000, "the report");
  const [, args] = asked.find(([op]) => op === "media.resolve");
  assert.deepEqual(args, { peer: "phone", path: PATH, keep: PHONE, mine: MINE, theirs: [PHONE, TABLET] });
  assert.ok(report.textContent.includes("Not kept:") && byText(report, "div", "This device").length);
  assert.ok(report.textContent.includes("Still in conflict with other devices:"));
  assert.ok(byText(report, "div", "tablet").length && byText(report, "div", "watch").length);
  assert.equal(ctx.failed.reason, "unavailable", "the refresh failed, and the report is shown all the same");

  // once the page loads again: with one other version left, this device's can be kept again, and the deleting
  // device's side still offers nothing (the text API refuses it as stale until the edit is resolved)
  await mount(root, ctx);
  const still = byText(root, "article", PATH).pop();
  const rows = still.querySelectorAll("div.sync-side");
  const labels = (peer) => rows.find((node) => node.querySelector("strong").textContent === "With " + peer)
    .querySelectorAll("button").map((node) => node.textContent);
  assert.deepEqual(labels("tablet"), ["Keep this device's", "Keep tablet's"]);
  assert.deepEqual(labels("watch"), []);
  assert.ok(still.textContent.includes("Resolve the other devices' versions first"));
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
    "media.conflicts": () => ({ conflicts: [], unknown: [] }),
    "media.forks": () => ({ forks: [], more: false, problems: [] }),
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
