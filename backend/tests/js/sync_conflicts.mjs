// 114's Sync page over a keep-both conflict with three other devices, in fakedom with the page's ops answered
// here: every version is shown, keeping this device's is not offered while several others stand (the text API
// refuses it), a deletion waits behind the others' versions, and after keeping one the report lists this
// device's version as not kept and what the other devices still hold. Run by test_media_pages.py.
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

test("a conflict with three devices: every version shown, nothing lost after keeping one", async () => {
  install("http://127.0.0.1:9");
  const ui = await import("/static/shell/ui.js");
  const { mount } = await import("/static/pages/sync/page.js");
  const asked = [];
  let open = [CONFLICT];
  const answers = {
    "media.devices": () => ({ device: "desk", device_problem: null, transport: { note: "Loopback only, not encrypted." },
      peers: ["phone", "tablet", "watch"].map((peer) => ({ peer, address: "127.0.0.1:1", paired: true, last: null })),
      unavailable: [] }),
    "media.conflicts": () => ({ conflicts: open, unknown: [] }),
    "media.forks": () => ({ forks: [], more: false, problems: [] }),
    "media.resolve": (args) => {
      open = [LEFT];
      return { peer: args.peer, path: args.path, status: "resolved", reason: null, code: null,
        not_kept: [{ ...placement(MINE), holder: "this device" }],
        still_open: { theirs: LEFT.theirs, peers: LEFT.peers } };
    },
  };
  const ctx = {
    ui, query: new URLSearchParams(), signal: new AbortController().signal,
    api: async (op, args) => { asked.push([op, args]); return answers[op](args); },
    act: async (work) => work(), has: (op) => op in answers, registered: () => false,
    picture: async () => null, styles() {}, setTitle() {}, setActions() {}, notify() {},
    refreshLibrary: async () => null,
  };
  const root = document.createElement("main");
  document.body.append(root);
  await mount(root, ctx);

  const card = byText(root, "section", PATH).pop();
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
  // with one other version left, the deletion still waits and this device's can be kept again
  const still = byText(root, "section", PATH).filter((node) => !node.textContent.startsWith("Resolved")).pop();
  const now = still.querySelectorAll("button").map((node) => node.textContent);
  assert.ok(now.includes("Keep this device's") && now.includes("Keep tablet's"));
});
