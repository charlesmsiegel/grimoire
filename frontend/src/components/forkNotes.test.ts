import { describe, expect, it } from "vitest";
import { forkNotes } from "./forkNotes";

const report = (refused: string[] = [], failed: string[] = []) => ({
  refused: refused.map((label) => ({ label, reason: "it changed since" })),
  failed,
});

describe("forkNotes", () => {
  it("says nothing about a fork that left nothing behind", () => {
    expect(forkNotes(report())).toBe("");
  });

  it("names the records that still hold what a removed scene wrote", () => {
    // Naming them is the point: it is what lets the player go and look. The
    // note deliberately does not claim WHY each one was refused.
    expect(forkNotes(report(["The Pact — lore", "Mara — state"])))
      .toBe("2 records on the fork still hold what a removed scene wrote: "
            + "The Pact — lore, Mara — state");
  });

  it("reads as one record rather than 1 records", () => {
    expect(forkNotes(report(["The Pact — lore"])))
      .toBe("1 record on the fork still holds what a removed scene wrote: The Pact — lore");
  });

  it("reports cleanup that could not run as its own clause", () => {
    // A different kind of incomplete from a refusal, and conflating the two
    // would mislead: the fork exists, this is a thing to go and check.
    const note = forkNotes(report(["The Pact — lore"], ["0002--the-debt/plot_beats"]));
    expect(note).toMatch(/still holds what a removed scene wrote/);
    expect(note).toMatch(/could not be cleaned up \(0002--the-debt\/plot_beats\)/);
  });

  it("reports cleanup failures even when nothing was refused", () => {
    expect(forkNotes(report([], ["0002--the-debt/chronicle"])))
      .toMatch(/^some continuity records on the fork could not be cleaned up/);
  });
});
