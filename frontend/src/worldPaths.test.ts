import {
  sectionHref, parseWorldTail, legacyTarget, defaultSection,
} from "./worldPaths";

const W = { kind: "world" as const, id: "realm" };
const C = { kind: "campaign" as const, id: "run" };

describe("sectionHref", () => {
  test("a section's own screen, both shapes", () => {
    expect(sectionHref(W, { kind: "section", at: "items" })).toBe("/worlds/realm/items");
    expect(sectionHref(C, { kind: "section", at: "items" })).toBe("/campaigns/run/world/items");
  });

  test("the world root is the overview, with no trailing segment", () => {
    expect(sectionHref(W, { kind: "section", at: "overview" })).toBe("/worlds/realm");
  });

  test("a record inside a section", () => {
    expect(sectionHref(W, { kind: "record", at: "lore", rid: "the-salt-pact" }))
      .toBe("/worlds/realm/lore/the-salt-pact");
    expect(sectionHref(C, { kind: "record", at: "items", rid: "the-salt-pact" }))
      .toBe("/campaigns/run/world/items/the-salt-pact");
  });

  test("a campaign's character record lives under its world copy, like every other kind", () => {
    expect(sectionHref(C, { kind: "record", at: "characters", rid: "mira" }))
      .toBe("/campaigns/run/world/characters/mira");
  });

  test("modifiers reach the destination that gives them meaning", () => {
    expect(sectionHref(W, { kind: "record", at: "characters", rid: "mira", v: "veiled" }))
      .toBe("/worlds/realm/characters/mira?v=veiled");
    expect(sectionHref(W, { kind: "section", at: "images", forCampaign: "run" }))
      .toBe("/worlds/realm/images?for=run");
    expect(sectionHref(W, { kind: "section", at: "greetings", view: "graph" }))
      .toBe("/worlds/realm/greetings?view=graph");
    expect(sectionHref(W, { kind: "section", at: "lore", newOwner: "characters:seraphine" }))
      .toBe("/worlds/realm/lore?owner=characters%3Aseraphine");
  });

  test("an absent modifier adds no query string at all", () => {
    expect(sectionHref(W, { kind: "section", at: "images" })).toBe("/worlds/realm/images");
    expect(sectionHref(W, { kind: "record", at: "characters", rid: "mira" }))
      .toBe("/worlds/realm/characters/mira");
  });

  test("ids and world names are encoded as single segments", () => {
    expect(sectionHref({ kind: "world", id: "a b" }, { kind: "record", at: "items", rid: "c/d" }))
      .toBe("/worlds/a%20b/items/c%2Fd");
  });

  test("a campaign cannot address a world-only section", () => {
    expect(() => sectionHref(C, { kind: "section", at: "tags" })).toThrow(/tags/);
    expect(() => sectionHref(C, { kind: "section", at: "overview" })).toThrow(/overview/);
    expect(() => sectionHref(C, { kind: "section", at: "push" })).toThrow(/push/);
    expect(() => sectionHref(C, { kind: "section", at: "images" })).toThrow(/images/);
  });
});

describe("parseWorldTail", () => {
  test("an empty tail is the shape's default section", () => {
    expect(parseWorldTail("", "world"))
      .toEqual({ ok: true, section: "overview", rid: null });
    expect(parseWorldTail("", "campaign"))
      .toEqual({ ok: true, section: "characters", rid: null });
  });

  test("a section, and a section with a record", () => {
    expect(parseWorldTail("items", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("items/the-salt-pact", "world"))
      .toEqual({ ok: true, section: "items", rid: "the-salt-pact" });
  });

  test("a segment is decoded", () => {
    expect(parseWorldTail("items/c%2Fd", "world"))
      .toEqual({ ok: true, section: "items", rid: "c/d" });
  });

  test("empty segments are dropped, so a trailing or doubled slash is the section root", () => {
    expect(parseWorldTail("items/", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("/items", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
    expect(parseWorldTail("items//", "world"))
      .toEqual({ ok: true, section: "items", rid: null });
  });

  test("a segment spelled any other way still names the same record", () => {
    // `it%65ms` is "items"; `%2f` is a slash inside an id. Both parse to what
    // they mean -- WorldView compares the result against `sectionHref`'s own
    // spelling and replaces the URL, so canonicalisation needs no flag here.
    expect(parseWorldTail("it%65ms/x", "world"))
      .toEqual({ ok: true, section: "items", rid: "x" });
    expect(parseWorldTail("items/a%2fb", "world"))
      .toEqual({ ok: true, section: "items", rid: "a/b" });
  });

  test("a malformed escape is rejected, not thrown", () => {
    expect(() => parseWorldTail("items/%ZZ", "world")).not.toThrow();
    expect(parseWorldTail("items/%ZZ", "world")).toEqual({ ok: false });
  });

  test("an unknown section is rejected rather than rendered", () => {
    expect(parseWorldTail("garbage", "world")).toEqual({ ok: false });
    expect(parseWorldTail("garbage/x", "world")).toEqual({ ok: false });
  });

  test("a world-only section is rejected on the campaign shape", () => {
    expect(parseWorldTail("tags", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("images", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("overview", "campaign")).toEqual({ ok: false });
    expect(parseWorldTail("push", "campaign")).toEqual({ ok: false });
  });

  test("a record is rejected on a section that has none", () => {
    expect(parseWorldTail("tags/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("push/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("images/x", "world")).toEqual({ ok: false });
    expect(parseWorldTail("overview/x", "world")).toEqual({ ok: false });
  });

  test("more than two segments is not an address", () => {
    expect(parseWorldTail("items/a/b", "world")).toEqual({ ok: false });
  });
});

describe("legacyTarget", () => {
  test("a section and a record", () => {
    expect(legacyTarget(new URLSearchParams("section=lore&id=the-salt-pact"), "world"))
      .toEqual({ kind: "record", at: "lore", rid: "the-salt-pact" });
    expect(legacyTarget(new URLSearchParams("section=lore"), "world"))
      .toEqual({ kind: "section", at: "lore" });
  });

  test("a character carries its version, and only when one was named", () => {
    expect(legacyTarget(new URLSearchParams("section=characters&id=mira&v=veiled"), "world"))
      .toEqual({ kind: "record", at: "characters", rid: "mira", v: "veiled" });
    expect(legacyTarget(new URLSearchParams("section=characters&id=mira&v="), "world"))
      .toEqual({ kind: "record", at: "characters", rid: "mira" });
  });

  test("section=characters with no id is the grid, not an empty character", () => {
    expect(legacyTarget(new URLSearchParams("section=characters"), "world"))
      .toEqual({ kind: "section", at: "characters" });
  });

  test("images keeps `for`, and nothing else does", () => {
    expect(legacyTarget(new URLSearchParams("section=images&for=run"), "world"))
      .toEqual({ kind: "section", at: "images", forCampaign: "run" });
    expect(legacyTarget(new URLSearchParams("section=items&for=run"), "world"))
      .toEqual({ kind: "section", at: "items" });
  });

  test("`owner` reaches a recordless lore root and never joins an id", () => {
    expect(legacyTarget(new URLSearchParams("section=lore&owner=characters:sera"), "world"))
      .toEqual({ kind: "section", at: "lore", newOwner: "characters:sera" });
    expect(legacyTarget(new URLSearchParams("section=lore&id=x&owner=characters:sera"), "world"))
      .toEqual({ kind: "record", at: "lore", rid: "x" });
  });

  test("no section, an unknown one, or one this shape forbids, is not a legacy link", () => {
    expect(legacyTarget(new URLSearchParams(""), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("id=x"), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("section=garbage"), "world")).toBeNull();
    expect(legacyTarget(new URLSearchParams("section=tags"), "campaign")).toBeNull();
  });

  test("a legacy target never carries `section` or `id` into its new address", () => {
    const t = legacyTarget(new URLSearchParams("section=lore&id=x"), "world")!;
    expect(sectionHref(W, t)).toBe("/worlds/realm/lore/x");
  });
});

describe("defaultSection", () => {
  test("each shape has one", () => {
    expect(defaultSection("world")).toBe("overview");
    expect(defaultSection("campaign")).toBe("characters");
  });
});
