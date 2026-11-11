// Start a chat's own logic (pages/start/page.js): what a card's history sets up again, from the `last` that
// play.options answers. Synthetic names and refs.
import assert from "node:assert/strict";
import { test } from "node:test";

import { remembered } from "../../src/grimoire/app/static/pages/start/page.js";

const ANYA = { ref: "card:anya/character/anya", name: "Anya Rook" };
const TAMSIN = { ref: "card:tamsin/character/tamsin", name: "Tamsin Hale" };
const OWN = { ref: "card:mara/lorebook/mara-lore", name: "Mara Vell" };
const STORM = { ref: "card:storm/lorebook/storm-lore", name: "Storm over Port Vell" };
const PERSONAS = [ANYA, TAMSIN];
const LOREBOOKS = [OWN, STORM];
const last = (persona, lorebooks = []) => ({ chat: "campaign:c#chat/c", persona, lorebooks, when: "2026-03-04T10:00:00Z" });

test("no history sets nothing up", () => {
  assert.deepEqual(remembered(null, PERSONAS, LOREBOOKS, null), { persona: "", lorebook: "" });
  assert.deepEqual(remembered(undefined, PERSONAS, LOREBOOKS, OWN.ref + "@r1"), { persona: "", lorebook: "" });
});

test("the persona played last is set up, by its ref", () => {
  assert.deepEqual(remembered(last(ANYA), PERSONAS, LOREBOOKS, null), { persona: ANYA.ref, lorebook: "" });
  // a persona this page cannot offer is not set up, and neither is anything else from that chat
  const gone = { ref: "card:nobody/character/nobody", name: "Anya Rook" };
  assert.deepEqual(remembered(last(gone, [STORM]), PERSONAS, LOREBOOKS, null), { persona: "", lorebook: "" });
});

test("a remembered lorebook is chosen unless it is the card's own, which is the default", () => {
  assert.deepEqual(remembered(last(ANYA, [STORM]), PERSONAS, LOREBOOKS, OWN.ref + "@r1"), { persona: ANYA.ref, lorebook: STORM.ref });
  assert.deepEqual(remembered(last(ANYA, [OWN]), PERSONAS, LOREBOOKS, OWN.ref + "@r1"), { persona: ANYA.ref, lorebook: "" });
  // one the library no longer lists is passed over for the next
  const lost = { ref: "card:lost/lorebook/lost", name: "Lost" };
  assert.deepEqual(remembered(last(ANYA, [lost, STORM]), PERSONAS, LOREBOOKS, null), { persona: ANYA.ref, lorebook: STORM.ref });
  assert.deepEqual(remembered(last(ANYA, [lost]), PERSONAS, LOREBOOKS, null), { persona: ANYA.ref, lorebook: "" });
});
