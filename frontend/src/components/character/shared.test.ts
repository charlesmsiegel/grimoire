import { characterHref, charactersHref } from "./shared";

const W = { kind: "world" as const, id: "realm" };
const C = { kind: "campaign" as const, id: "run" };

test("a character's page, in both shapes", () => {
  expect(characterHref(W, "mira")).toBe("/worlds/realm/characters/mira");
  expect(characterHref(C, "mira")).toBe("/campaigns/run/world/characters/mira");
});

test("a version rides along when one is named", () => {
  expect(characterHref(W, "mira", "veiled")).toBe("/worlds/realm/characters/mira?v=veiled");
  expect(characterHref(W, "mira", "")).toBe("/worlds/realm/characters/mira");
});

test("the grid they came from", () => {
  expect(charactersHref(W)).toBe("/worlds/realm/characters");
  expect(charactersHref(C)).toBe("/campaigns/run/world/characters");
});
