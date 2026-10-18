import type { EpistemicDetail, QuickReplyEntry, Scene } from "../api/types";
import type { RunHandle } from "../api/stream";
import { fixture, type DeepPartial } from "./fixture";

test("keeps partial fixture values unchanged while checking the supplied fields", () => {
  const partial = { meta: { id: "s" } };
  const scene: Scene = fixture(partial);
  expect(scene).toBe(partial);

  // These calls are compiler regressions: removing NoInfer must not let the
  // literal choose its own type and silently accept an impossible API value.
  // @ts-expect-error -- a supplied scene id must still be a string.
  const invalidId: Scene = fixture({ meta: { id: 7 } });
  // @ts-expect-error -- nested fields must belong to the actual API response.
  const invalidKey: Scene = fixture({ meta: { missing: true } });
  expect(invalidId.meta.id).toBe(7);
  expect(invalidKey.meta).toEqual({ missing: true });
});

test("checks hoisted partials at their literal definitions", () => {
  const partial = { meta: { id: "s" } } satisfies DeepPartial<Scene>;
  const scene: Scene = fixture(partial);
  expect(scene).toBe(partial);

  const renamed = {
    meta: {
      id: "s",
      // @ts-expect-error -- a hoisted literal must still use API field names.
      renamed: true,
    },
  } satisfies DeepPartial<Scene>;
  const renamedArray = [{ meta: {
    id: "s",
    // @ts-expect-error -- definition checks also reach hoisted array elements.
    renamed: true,
  } }] satisfies DeepPartial<Scene[]>;
  expect(renamed.meta.renamed).toBe(true);
  expect(renamedArray[0].meta.renamed).toBe(true);

  const wrongFieldType = { meta: { id: 7 } };
  // @ts-expect-error -- named values still check the types of known fields.
  const invalid: Scene = fixture(wrongFieldType);
  expect(invalid.meta.id).toBe(7);
});

test("checks array elements and union members in partial fixtures", () => {
  const run: RunHandle = fixture({ state: "running" });
  expect(run.state).toBe("running");
  // @ts-expect-error -- the API has no finished run state.
  const invalidRun: RunHandle = fixture({ state: "finished" });
  // @ts-expect-error -- every supplied post still requires string content.
  const invalidPost: Scene = fixture({ messages: [{ content: 7 }] });
  expect(invalidRun.state).toBe("finished");
  expect(invalidPost.messages[0].content).toBe(7);
});

test("checks the position and length of a supplied API tuple", () => {
  const detail: EpistemicDetail = fixture({ pending: [["s", "part", [1, 2]]] });
  expect(detail.pending).toEqual([["s", "part", [1, 2]]]);

  // @ts-expect-error -- the first tuple slot is a scene id, not a number array.
  const wrongPosition: EpistemicDetail = fixture({ pending: [[[], "part", [1]]] });
  // @ts-expect-error -- a supplied pending tuple needs all three slots.
  const missingSlot: EpistemicDetail = fixture({ pending: [["s", "part"]] });
  // @ts-expect-error -- a pending tuple has exactly three slots.
  const extraSlot: EpistemicDetail = fixture({ pending: [["s", "part", [1], "extra"]] });
  expect(wrongPosition.pending?.[0]?.[0]).toEqual([]);
  expect(missingSlot.pending?.[0]).toHaveLength(2);
  expect(extraSlot.pending?.[0]).toHaveLength(4);
});

test("preserves readonly tuples and partial object fields within tuple slots", () => {
  const value: readonly [Scene, string] = fixture<readonly [Scene, string]>([{ meta: { id: "s" } }, "part"]);
  expect(value[0].meta.id).toBe("s");
  // @ts-expect-error -- readonly tuple slots keep their position too.
  const wrongSlot = fixture<readonly [Scene, string]>(["part", { meta: { id: "s" } }]);
  expect(wrongSlot[0]).toBe("part");
});

test("a partial object union still belongs to one branch", () => {
  const hidden = fixture<QuickReplyEntry>({ id: "x", hidden: true });
  const shown = fixture<QuickReplyEntry>({ id: "x", label: "Mara", kind: "send" });
  expect(hidden).toEqual({ id: "x", hidden: true });
  expect(shown).toEqual({ id: "x", label: "Mara", kind: "send" });

  // @ts-expect-error -- a hidden entry cannot also carry a normal reply's fields.
  const mixed = fixture<QuickReplyEntry>({ id: "x", hidden: true, label: "Mara", kind: "send" });
  // @ts-expect-error -- union exclusivity also applies within array elements.
  const nested = fixture<{ replies: QuickReplyEntry[] }>({ replies: [{ id: "x", hidden: true, label: "Mara" }] });
  expect(mixed).toMatchObject({ hidden: true, label: "Mara" });
  expect(nested.replies[0]).toMatchObject({ hidden: true, label: "Mara" });
});

test("supplied fixture fields distinguish omission from explicit undefined", () => {
  const omitted: Scene = fixture({ meta: {} });
  expect(omitted.meta).not.toHaveProperty("id");

  // @ts-expect-error -- a supplied required API field cannot be undefined.
  const invalidId: Scene = fixture({ meta: { id: undefined } });
  // @ts-expect-error -- required fields within array elements obey the same rule.
  const invalidContent: Scene = fixture({ messages: [{ content: undefined }] });
  expect(invalidId.meta).toHaveProperty("id", undefined);
  expect(invalidContent.messages[0]).toHaveProperty("content", undefined);

  const allowed = fixture<{ note: string | undefined; optional?: string | undefined }>({ note: undefined, optional: undefined });
  expect(allowed).toEqual({ note: undefined, optional: undefined });
  // @ts-expect-error -- an optional string permits omission, not an undefined value.
  const invalidOptional = fixture<{ note?: string }>({ note: undefined });
  expect(invalidOptional).toHaveProperty("note", undefined);
  // @ts-expect-error -- a hidden entry cannot supply another branch's field, even undefined.
  const invalidUnion = fixture<QuickReplyEntry>({ id: "x", hidden: true, label: undefined });
  expect(invalidUnion).toHaveProperty("label", undefined);
});
