import { describe, expect, it } from "vitest";
import type { ResponseSettingsRecord, ResponseSwipe } from "../../api/types";
import { madeByLines, settingsFor, swipeTitle } from "./swipeTitle";

const SETTINGS: ResponseSettingsRecord = { style_id: "terse", phase: "primary", words: 180, paragraphs: 3 };
const RESUME: ResponseSettingsRecord = { style_id: "lush", phase: "resume", words: 90, paragraphs: 2 };

const FULL = {
  task: "character_turn", connection_id: "c1", connection: "Saltmarch Router",
  model: "realm/mara-70b", provider: "realm", composed: "primary" as const,
  guidance: "Make Winifred warier", note: "steer toward the harbour",
};

function swipe(over: Partial<ResponseSwipe> = {}): ResponseSwipe {
  return {
    active: 0, variants: [{ id: "v1", status: "complete", made_by: FULL }],
    settings: SETTINGS, resume_settings: RESUME,
    can_reroll: true, editable: true, round_open: false, edited: false, ...over,
  };
}

describe("madeByLines", () => {
  it("lists every present key in order", () => {
    expect(madeByLines(FULL, SETTINGS)).toEqual([
      "Guided: Make Winifred warier",
      "Note: steer toward the harbour",
      "Model: realm/mara-70b",
      "Via: Saltmarch Router",
      "Style: terse",
      "Length: ~180 words, 3 paragraphs",
    ]);
  });

  it("skips a model the provider never named", () => {
    const { model: _model, ...rest } = FULL;
    expect(madeByLines(rest, SETTINGS).some((l) => l.startsWith("Model:"))).toBe(false);
  });

  it("skips Style when the settings name no style", () => {
    const lines = madeByLines(FULL, { ...SETTINGS, style_id: "" });
    expect(lines.some((l) => l.startsWith("Style:"))).toBe(false);
    expect(lines).toContain("Length: ~180 words, 3 paragraphs");
  });

  it("skips settings that were never recorded", () => {
    expect(madeByLines(FULL, null)).toEqual([
      "Guided: Make Winifred warier", "Note: steer toward the harbour",
      "Model: realm/mara-70b", "Via: Saltmarch Router",
    ]);
    expect(madeByLines(FULL, undefined)).toHaveLength(4);
  });

  it("says 1 paragraph and 1 word in the singular", () => {
    expect(madeByLines(FULL, { ...SETTINGS, words: 1, paragraphs: 1 }))
      .toContain("Length: ~1 word, 1 paragraph");
    expect(madeByLines(FULL, { ...SETTINGS, words: 1, paragraphs: 2 }))
      .toContain("Length: ~1 word, 2 paragraphs");
  });

  it("gives nothing for no made_by", () => {
    expect(madeByLines(undefined, SETTINGS)).toEqual([]);
  });
});

describe("swipeTitle", () => {
  it("joins the active variant's lines", () => {
    expect(swipeTitle(swipe())?.split("\n")).toHaveLength(6);
  });

  it("reads resume_settings for a resume-composed variant", () => {
    const made = { ...FULL, composed: "resume" as const };
    const t = swipeTitle(swipe({ variants: [{ id: "v1", status: "complete", made_by: made }] }));
    expect(t).toContain("Style: lush");
    expect(t).toContain("Length: ~90 words, 2 paragraphs");
    expect(t).not.toContain("terse");
  });

  it("reads settings for a primary-composed variant", () => {
    const t = swipeTitle(swipe());
    expect(t).toContain("Style: terse");
    expect(t).not.toContain("lush");
  });

  it("is undefined when the active variant has no made_by", () => {
    expect(swipeTitle(swipe({ variants: [{ id: "v1", status: "complete" }] }))).toBeUndefined();
  });

  it("is undefined when nothing is active", () => {
    expect(swipeTitle(swipe({ active: null }))).toBeUndefined();
  });

  it("is undefined when made_by carries no keys and settings are absent", () => {
    expect(swipeTitle(swipe({
      variants: [{ id: "v1", status: "complete", made_by: {} }], settings: null,
    }))).toBeUndefined();
  });
});

describe("settingsFor", () => {
  it("picks resume_settings for a resume-composed variant and settings for any other", () => {
    const record = { settings: SETTINGS, resume_settings: RESUME };
    expect(settingsFor({ ...FULL, composed: "resume" }, record)).toBe(RESUME);
    expect(settingsFor(FULL, record)).toBe(SETTINGS);
    expect(settingsFor(undefined, record)).toBe(SETTINGS);
  });

  it("prefers the variant's own settings copy over the record's", () => {
    // A later roll fence overwrites the record's single resume_settings, so a
    // resume-composed variant carries what its own prompt rendered.
    const own: ResponseSettingsRecord = { ...RESUME, words: 40 };
    const record = { settings: SETTINGS, resume_settings: RESUME };
    expect(settingsFor({ ...FULL, composed: "resume", settings: own }, record)).toBe(own);
    expect(swipeTitle(swipe({
      variants: [{ id: "v1", status: "complete", made_by: { ...FULL, composed: "resume", settings: own } }],
    }))).toContain("Length: ~40 words, 2 paragraphs");
  });
});
