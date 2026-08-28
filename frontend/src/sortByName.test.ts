import { describe, expect, it } from "vitest";
import { byName } from "./sortByName";

describe("byName", () => {
  it("orders alphabetically without regard to case", () => {
    const rows = [{ name: "saltmarch" }, { name: "Ashfall" }, { name: "Bright" }];
    expect(byName(rows).map((r) => r.name)).toEqual(["Ashfall", "Bright", "saltmarch"]);
  });

  it("orders embedded numbers by value, not by digit", () => {
    const rows = [{ name: "Chapter 10" }, { name: "Chapter 2" }];
    expect(byName(rows).map((r) => r.name)).toEqual(["Chapter 2", "Chapter 10"]);
  });

  it("files past a leading article, so The Verdigris Crown is under V", () => {
    const rows = [{ name: "The Verdigris Crown" }, { name: "Ashfall" }, { name: "Winterlight" }];
    expect(byName(rows).map((r) => r.name))
      .toEqual(["Ashfall", "The Verdigris Crown", "Winterlight"]);
  });

  it("files past 'a' and 'an' too, whatever their case", () => {
    const rows = [
      { name: "an Ending" }, { name: "A Long Run" }, { name: "THE Bell" }, { name: "Cormorant" },
    ];
    // Bell, Cormorant, Ending, Long Run - the article is gone from every key,
    // so "Cormorant" sits between two names whose first letters it is between.
    expect(byName(rows).map((r) => r.name))
      .toEqual(["THE Bell", "Cormorant", "an Ending", "A Long Run"]);
  });

  it("only strips an article that is a whole word", () => {
    // "Anvil" and "Theft" begin with those letters and are not articles, and a
    // stop-word list that fired on a prefix would file them under V and F.
    const rows = [{ name: "Theft" }, { name: "Anvil" }, { name: "Ashfall" }];
    expect(byName(rows).map((r) => r.name)).toEqual(["Anvil", "Ashfall", "Theft"]);
  });

  it("keeps the article when it is the whole name", () => {
    // Stripping would leave an empty key, and every such record would tie at
    // the top of the list rather than filing anywhere.
    const rows = [{ name: "Ashfall" }, { name: "The" }, { name: "Umbral" }];
    expect(byName(rows).map((r) => r.name)).toEqual(["Ashfall", "The", "Umbral"]);
  });

  it("breaks a tie on the full name, so an article is not a coin toss", () => {
    const rows = [{ name: "The Ashfall" }, { name: "Ashfall" }];
    expect(byName(rows).map((r) => r.name)).toEqual(["Ashfall", "The Ashfall"]);
  });

  it("leaves the caller's array alone", () => {
    const rows = [{ name: "B" }, { name: "A" }];
    byName(rows);
    expect(rows.map((r) => r.name)).toEqual(["B", "A"]);
  });
});
