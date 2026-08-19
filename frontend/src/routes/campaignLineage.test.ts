import { describe, expect, it } from "vitest";
import { lineage } from "./campaignLineage";

const rows = (...ids: [string, string?][]) =>
  ids.map(([id, parent]) => ({ id, parent }));

const shape = (out: ReturnType<typeof lineage>) =>
  out.map((r) => `${"  ".repeat(r.depth)}${r.item.id}`);

describe("lineage", () => {
  it("leaves a set with no forks in it exactly as it was given", () => {
    expect(shape(lineage(rows(["a"], ["b"], ["c"])))).toEqual(["a", "b", "c"]);
  });

  it("nests a fork under the campaign it came from", () => {
    expect(shape(lineage(rows(["a"], ["b", "a"])))).toEqual(["a", "  b"]);
  });

  it("nests a fork of a fork one level deeper", () => {
    expect(shape(lineage(rows(["a"], ["b", "a"], ["c", "b"]))))
      .toEqual(["a", "  b", "    c"]);
  });

  it("keeps the caller's order among roots and among siblings", () => {
    // The shelf hands these in "last played" order, and the tree may group them
    // but must not re-rank them.
    expect(shape(lineage(rows(["z"], ["b", "a"], ["a"], ["c", "a"]))))
      .toEqual(["z", "a", "  b", "  c"]);
  });

  it("shows a fork whose parent is not in the set as a root", () => {
    // Deleted, or filtered out by the world column — either way the row still
    // belongs on the page.
    expect(shape(lineage(rows(["b", "gone"])))).toEqual(["b"]);
  });

  it("shows a campaign that names itself as its own parent as a root", () => {
    expect(shape(lineage(rows(["a", "a"])))).toEqual(["a"]);
  });

  it("shows every campaign of a hand-edited parent cycle rather than none", () => {
    // No root, so the ordinary walk reaches neither. A store is plain files the
    // user owns; losing two campaigns off the shelf over it is not an option.
    expect(shape(lineage(rows(["a", "b"], ["b", "a"])))).toEqual(["a", "  b"]);
  });

  it("emits each campaign exactly once", () => {
    const out = lineage(rows(["a"], ["b", "a"], ["c", "b"], ["d", "a"], ["e", "x"]));
    expect(out.map((r) => r.item.id).sort()).toEqual(["a", "b", "c", "d", "e"]);
  });
});
