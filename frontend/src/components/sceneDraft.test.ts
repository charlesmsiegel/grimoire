import { greetingDraft, suggestionDraft, customDraft, BLANK_TITLE } from "./sceneDraft";

const G = { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true };
const S = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
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
