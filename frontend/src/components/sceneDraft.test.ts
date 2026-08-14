import { greetingDraft, savedDraft, suggestionDraft, customDraft, BLANK_TITLE } from "./sceneDraft";
import type { SceneIdea } from "../api/client";

const G = { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true };
const S = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
};

const IDEA: SceneIdea = {
  id: "the-tide-book", title: "The tide-book", premise: "A ledger nobody signed.",
  date: "2026-03-04", cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" }, pcless: false,
  source: "llm", status: "active", created: "2026-03-01T00:00:00Z", used_scene: "",
};

test("a greeting draft takes its title from the greeting and its date from nextDate", () => {
  const d = greetingDraft(G, "2026-01-01", false);
  expect(d).toEqual({ source: "greeting", gid: "reck", title: "Reckoning",
                      defaultTitle: "Reckoning", date: "2026-01-01",
                      location: "", pcless: false });
});

test("a suggestion draft prefers its own date and falls back to nextDate", () => {
  expect(suggestionDraft(S, "2026-01-01", false).date).toBe("2026-03-04");
  expect(suggestionDraft({ ...S, date: "" }, "2026-01-01", false).date).toBe("2026-01-01");
});

test("a suggestion draft carries cast, location id, and premise", () => {
  const d = suggestionDraft(S, "", false);
  expect(d).toMatchObject({ source: "generated", location: "saltmarch",
                            premise: "A debt-collector arrives.",
                            cast: [{ kind: "characters", id: "mara", name: "Mara" }] });
});

test("a custom draft keeps the typed text as the premise, never the model's title", () => {
  const d = customDraft("back at the marsh house",
    { title: "The morning after", date: "2026-03-04",
      location: { id: "saltmarch", name: "Saltmarch" }, cast: [] }, "2026-01-01", false);
  expect(d).toMatchObject({ source: "custom", title: "The morning after",
                            defaultTitle: "The morning after", date: "2026-03-04",
                            location: "saltmarch", premise: "back at the marsh house" });
});

test("a blank draft still seeds nextDate, as every path does today", () => {
  const d = customDraft("", null, "2026-01-01", false);
  expect(d).toMatchObject({ title: BLANK_TITLE, defaultTitle: BLANK_TITLE,
                            date: "2026-01-01", location: "", premise: "", cast: [] });
});

test("an extraction that returned nothing degrades to the blank defaults", () => {
  const d = customDraft("something", { title: "", date: "", location: null, cast: [] },
                        "2026-01-01", false);
  expect(d).toMatchObject({ title: BLANK_TITLE, date: "2026-01-01", location: "",
                            premise: "something" });
});

test("a suggestion draft drops cast entries whose kind is neither characters nor pcs", () => {
  const d = suggestionDraft({
    ...S,
    cast: [
      { kind: "characters", id: "mara", name: "Mara" },
      { kind: "npc-faction", id: "ghoul", name: "Ghoul" },
    ],
  }, "2026-01-01", false);
  expect(d).toMatchObject({ cast: [{ kind: "characters", id: "mara", name: "Mara" }] });
});

test("a saved draft carries its ledger id, cast, location and premise", () => {
  expect(savedDraft(IDEA, "", false)).toMatchObject({
    source: "saved", lid: "the-tide-book", title: "The tide-book",
    defaultTitle: "The tide-book", location: "saltmarch",
    premise: "A ledger nobody signed.",
    cast: [{ kind: "characters", id: "mara", name: "Mara" }] });
});

test("a saved draft prefers nextDate over its own — the INVERSE of a suggestion", () => {
  // a generated date came out of this minute's snapshot; a saved one is a
  // fossil of whenever it was saved, and set_datetime accepts a date before
  // the campaign's current moment without complaint
  expect(savedDraft(IDEA, "2026-09-09", false).date).toBe("2026-09-09");
  expect(suggestionDraft(S, "2026-09-09", false).date).toBe("2026-03-04");
  // ...and the stored date is still the fallback when nothing was estimated
  expect(savedDraft(IDEA, "", false).date).toBe("2026-03-04");
});

test("a saved draft drops cast entries whose kind is neither characters nor pcs", () => {
  const d = savedDraft({ ...IDEA, cast: [
    { kind: "characters", id: "mara", name: "Mara" },
    { kind: "npc-faction", id: "ghoul", name: "Ghoul" }] }, "", false);
  expect(d).toMatchObject({ cast: [{ kind: "characters", id: "mara", name: "Mara" }] });
});
