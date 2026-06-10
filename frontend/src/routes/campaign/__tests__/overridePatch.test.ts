import { describe, expect, it } from "vitest";

import { overridePatch } from "../overridePatch";

describe("overridePatch", () => {
  it("includes only changed and added keys", () => {
    const initial = { id: "x", name: "Grendel", threat_level: "high", tags: ["beast"] };
    const draft = { ...initial, name: "Grendel, Awakened", habitat: ["fens"] };
    expect(overridePatch(initial, draft)).toEqual({
      name: "Grendel, Awakened",
      habitat: ["fens"],
    });
  });

  it("emits null tombstones for removed keys and never patches id", () => {
    const initial = { id: "x", name: "Grendel", comment: "tmp" };
    const draft = { id: "renamed-anyway", name: "Grendel" };
    expect(overridePatch(initial, draft)).toEqual({ comment: null });
  });

  it("compares nested values structurally", () => {
    const initial = { voice: { summary: "gruff", samples: ["hm"] } };
    const draft = { voice: { summary: "gruff", samples: ["hm"] } };
    expect(overridePatch(initial, draft)).toEqual({});
    const changed = { voice: { summary: "warm", samples: ["hm"] } };
    expect(overridePatch(initial, changed)).toEqual({
      voice: { summary: "warm", samples: ["hm"] },
    });
  });
});
