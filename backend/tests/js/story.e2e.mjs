// Sprint 115's Story page end to end, in fakedom against a real app server, in two phases (GRIMOIRE_PHASE).
//   review (the desktop's app, with the phone served to it): from the campaign hub to its story; the table sees
//     that it is the director's; the director syncs from Devices and sync, sees the scene both devices changed
//     apart kept in both versions, keeping one side refused with the reason, the accepted payment and holdings
//     held, and the restoration named as not offered; previews a retcon of the scene from its card (writing
//     nothing); keeps an unsent alternative across leaving the page; and records, reviewed first, that the later
//     line of dialogue rests on the payment, after which a preview flags it as known.
//   restored (the phone's app, with the desktop served to it, after 054's owner operation restored the desktop's
//     scene as a retained fork): the fork shows both originals and waits on sync; one sync from Devices and sync
//     brings the payment across, and the fork is reconciled here with nothing left divergent.
// Run by test_app_story.py, which passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_CAMPAIGN,
// GRIMOIRE_PEER, GRIMOIRE_DEVICE and GRIMOIRE_PHASE.
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

const base = process.env.GRIMOIRE_APP;
const chat = process.env.GRIMOIRE_CHAT;
const campaign = process.env.GRIMOIRE_CAMPAIGN;
const peer = process.env.GRIMOIRE_PEER;
const phase = process.env.GRIMOIRE_PHASE;

const GIVEN = "Wren gives Tam the iron key.";
const DESTROYED = "The key was destroyed.";
const DIALOGUE = "Tam says: I will pay Ines with the coins.";

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const labelled = (root, tag, title) => root.querySelectorAll(tag).find((node) => node.getAttribute("aria-label") === title);
const sceneOf = (root) => root.querySelectorAll("article").find((node) => (node.getAttribute("aria-label") || "").startsWith("Divergent: Scene 1"));
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));

function input(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function choose(root, label, optionText) {
  const select = input(root, label);
  const option = select.querySelectorAll("option").find((node) => node.textContent.includes(optionText));
  assert.ok(option, label + " offers " + optionText);
  select.value = option.getAttribute("value");
  select.dispatchEvent({ type: "change" });
}

// the impacts of the preview drawn now, as "record text|Known" lines
function impacts(main) {
  const region = labelled(main, "div", "What it would touch");
  if (!region) return null;
  return region.querySelectorAll("li").map((node) => node.textContent);
}

async function start() {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const main = await until(() => document.body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  return { window, main };
}

async function syncNow(window, main) {
  window.location.hash = "#/sync";
  const button = await until(() => byText(main, "button", "Sync now")[0], 15000, "Sync now for " + peer);
  button.click();
  await until(() => !byText(main, "button", "Syncing…").length && byText(main, "button", "Sync now").length, 60000, "the sync to finish");
  await idle();
}

const storyHash = () => "#/story/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";

test("divergent offline scenes and a reviewed retcon, on the desktop", { skip: !base || phase !== "review" }, async () => {
  const { window, main } = await start();

  // the campaign hub links to its story, for the same chat; the table's view offers nothing
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const link = byText(main, "a", "Story").find((node) => node.getAttribute("href").startsWith("#/story/"));
  assert.ok(link, "the hub links to the story");
  link.click();
  const table = await until(() => section(main, "Story continuity"), 15000, "the table's view");
  assert.equal(view(main, "Table view").getAttribute("aria-pressed"), "true");
  assert.ok(table.textContent.includes("director's"));
  assert.equal(table.querySelectorAll("button").length, 0, "the table is offered nothing");
  assert.ok(!main.textContent.includes(GIVEN), "no scene text reaches the table's view");

  // before any sync: nothing is divergent
  view(main, "Director view").click();
  const quiet = await until(() => section(main, "Divergent offline scenes") && section(main, "Review a retcon or fork")
    && !section(main, "Divergent offline scenes").textContent.includes("Reading…") && section(main, "Divergent offline scenes"), 15000, "the director's view");
  assert.ok(quiet.textContent.includes("No scene of this campaign is kept in two versions on this device."));
  assert.ok(window.location.hash.includes("audience=director"));

  // a sync from Devices and sync: the scene both devices changed apart is kept in both versions
  await syncNow(window, main);
  window.location.hash = storyHash();
  const divergent = await until(() => {
    const found = section(main, "Divergent offline scenes");
    return found && sceneOf(found) && found;
  }, 15000, "the divergent scene");
  const scene = sceneOf(divergent);
  assert.ok(scene.textContent.includes(GIVEN) && scene.textContent.includes(DESTROYED), "both versions are shown: " + scene.textContent);
  assert.ok(byText(scene, "span", "Both kept").length && byText(scene, "span", "Held for review").length);
  assert.ok(scene.textContent.includes("Keeping one side: refused.") && scene.textContent.includes("semantic_review"));
  assert.ok(scene.textContent.includes("Taking " + peer + "'s version here would break what rests on it: an accepted event here rests on this scene"));
  assert.ok(!byText(scene, "button", "Keep").length, "no control keeps one side of a refused scene");
  assert.ok(divergent.textContent.includes("Held with them") && divergent.textContent.includes("accepted event e-1-"));
  assert.ok(divergent.textContent.includes("The last sync with " + peer + " held 4 records of this campaign for review."));
  assert.ok(byText(divergent, "strong", "Restore one device's scene as a retained fork is not available yet.").length);
  assert.ok(byText(divergent, "strong", "Merge both versions of a scene is not available yet.").length);
  assert.ok(byText(divergent, "a", "Devices and sync").length, "the sync page is linked, not repeated");

  // the scene's card opens a preview of a retcon of it, which writes nothing
  byText(scene, "button", "Preview a retcon of this scene")[0].click();
  await idle();
  const first = await until(() => impacts(main), 15000, "the preview");
  assert.ok(first.some((line) => line.includes("Accepted event: tam pays ines.") && line.includes("Known")), first.join("\n"));
  assert.ok(first.some((line) => line.includes("what tam holds") && line.includes("Known")));
  assert.ok(first.some((line) => line.includes(DIALOGUE) && line.includes("Possible")), "the dialogue may rest on it: " + first.join("\n"));
  const retcon = section(main, "Review a retcon or fork");
  const picked = retcon.querySelectorAll("input").filter((node) => node.getAttribute("type") === "checkbox" && node.checked);
  assert.deepEqual(picked.map((node) => node.getAttribute("value")), ["campaigns/c/turns/t-1.000001.json"], "the scene is the one picked");
  assert.ok(labelled(main, "div", "What it would touch").textContent.includes("Nothing was changed"));
  for (const item of ["Repair what depends on a changed scene", "Write a continuation", "Promote an alternative into the story"]) {
    assert.ok(byText(retcon, "strong", item + " is not available yet.").length, item + " is named, never offered");
  }

  // an alternative typed and not previewed is still there after the person leaves the page and comes back
  const alternative = input(retcon, "Your alternative, in your own words (optional)");
  alternative.value = "The key was only hidden.";
  alternative.dispatchEvent({ type: "input" });
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign);
  await until(() => section(main, "Cast and stage"), 15000, "the hub again");
  window.location.hash = storyHash();
  const again = await until(() => {
    const found = section(main, "Review a retcon or fork");
    return found && found.querySelector("textarea") && found;
  }, 15000, "the review again");
  assert.equal(input(again, "Your alternative, in your own words (optional)").value, "The key was only hidden.");

  // record that the later dialogue rests on the payment: reviewed first, then made as reviewed
  const declare = await until(() => labelled(again, "div", "Record what rests on what"), 15000, "the declaration form");
  choose(declare, "This record", "Accepted event: tam pays ines.");
  choose(declare, "rests on it (the record that depends)", DIALOGUE);
  choose(declare, "Because of", "dialogue");
  byText(declare, "button", "Review")[0].click();
  const box = await until(() => labelled(main, "div", "Review this change"), 15000, "the review");
  assert.ok(box.textContent.includes("Record that Scene 2: " + DIALOGUE + " rests on Accepted event: tam pays ines. (dialogue)."), box.textContent);
  assert.ok(box.textContent.includes("Once written it is never rewritten."));
  byText(box, "button", "Make this change")[0].click();
  await until(() => !labelled(main, "div", "Review this change"), 15000, "the change to be made");
  await idle();

  // a retcon of the scene now flags the dialogue as known; the rehearsal is said to be saved nowhere
  const now = section(main, "Review a retcon or fork");
  const box2 = now.querySelectorAll("input").find((node) => node.getAttribute("value") === "campaigns/c/turns/t-1.000001.json");
  box2.checked = true;
  box2.dispatchEvent({ type: "change" });
  byText(now, "button", "Preview")[0].click();
  await idle();
  const second = await until(() => impacts(main), 15000, "the preview again");
  assert.ok(second.some((line) => line.includes(DIALOGUE) && line.includes("Known")), "the declared dialogue is known: " + second.join("\n"));
  assert.ok(labelled(main, "div", "What it would touch").textContent.includes("not saved, and no model was called"));
});

test("a retained fork waits on sync, then is reconciled once, on the phone", { skip: !base || phase !== "restored" }, async () => {
  const { window, main } = await start();
  window.location.hash = storyHash();
  const forkOf = () => {
    const found = section(main, "Divergent offline scenes");
    return found && found.querySelectorAll("article").find((node) => (node.getAttribute("aria-label") || "").startsWith("Retained fork: "));
  };
  const fork = await until(forkOf, 15000, "the retained fork");
  assert.ok(byText(fork, "span", "Waiting on sync").length, fork.textContent);
  assert.ok(fork.textContent.includes("Restored from " + peer));
  assert.ok(fork.textContent.includes(GIVEN) && fork.textContent.includes(DESTROYED), "both originals are kept: " + fork.textContent);
  assert.ok(fork.textContent.includes(peer + " · restored") && fork.textContent.includes("This device · original kept"));

  await syncNow(window, main);
  window.location.hash = storyHash();
  const reconciled = await until(() => {
    const found = forkOf();
    return found && byText(found, "span", "Reconciled here").length && found;
  }, 15000, "the fork reconciled here");
  assert.ok(reconciled.textContent.includes("each once"));
  assert.ok(section(main, "Divergent offline scenes").textContent.includes("No scene of this campaign is kept in two versions on this device."));
});
