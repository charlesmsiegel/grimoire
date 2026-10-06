import type { QuickReply, QuickReplyDraft, QuickReplyEntry } from "../api/types";
import {
  hideEntry, isHide, moveEntry, overrideOf, quickReplyAvailability, quickReplyTitle, removeEntry,
  upsertEntry, type QuickReplyContext,
} from "./quickReplies";

const base: QuickReplyContext = { busy: false, rolling: false, renaming: false, sceneLocked: false,
  posts: 3, moduleKnown: true, pcless: false, ready: true, openerOffered: false, taskRunning: () => false };
const roll: QuickReply = { id: "r", label: "Roll", kind: "roll", notation: "2d6+3" };

test("send mirrors the Send button; insert is never disabled", () => {
  const send: QuickReply = { id: "s", label: "Look", kind: "send", text: "I take in the room.", mode: "send" };
  for (const ctx of [{ busy: true }, { rolling: true }, { renaming: true }])
    expect(quickReplyAvailability(send, { ...base, ...ctx }).disabled).toBe(true);
  expect(quickReplyAvailability(send, { ...base, sceneLocked: true }).disabled).toBe(false);
  expect(quickReplyAvailability({ ...send, mode: "insert" }, { ...base, busy: true }).disabled).toBe(false);
  expect(quickReplyAvailability(send, base)).toEqual({ shown: true, disabled: false, title: "I take in the room." });
});

test("a send reply in a PC-less scene names why", () => {
  const a = quickReplyAvailability({ id: "s", label: "L", kind: "send", text: "t", mode: "insert" }, { ...base, pcless: true });
  expect(a).toEqual({ shown: true, disabled: true, title: "This scene has no player character" });
  expect(quickReplyAvailability({ id: "d", label: "D", kind: "direct", text: "t", mode: "send" },
    { ...base, pcless: true }).disabled).toBe(false);
});

test("roll takes the dice button's guards and waits for the module read", () => {
  for (const ctx of [{ busy: true }, { sceneLocked: true }, { posts: 0 }, { rolling: true }, { moduleKnown: false }])
    expect(quickReplyAvailability(roll, { ...base, ...ctx }).disabled).toBe(true);
  expect(quickReplyAvailability(roll, base)).toEqual({ shown: true, disabled: false, title: "2d6+3" });
  expect(quickReplyTitle({ ...roll, roll_label: "Perception" })).toBe("2d6+3 — Perception");
});

test("tasks: running from either surface, locked, no connection, empty scene", () => {
  const sum: QuickReply = { id: "t", label: "Summarize", kind: "task", task: "rolling_summary" };
  const brk: QuickReply = { id: "b", label: "Break?", kind: "task", task: "scene_break" };
  const next: QuickReply = { id: "n", label: "Next", kind: "task", task: "next_scene" };
  for (const r of [sum, brk]) {
    expect(quickReplyAvailability(r, base).disabled).toBe(false);
    for (const ctx of [{ posts: 0 }, { sceneLocked: true }, { ready: false }])
      expect(quickReplyAvailability(r, { ...base, ...ctx }).disabled).toBe(true);
    expect(quickReplyAvailability(r, { ...base, taskRunning: (t) => t === r.task }).disabled).toBe(true);
  }
  expect(quickReplyAvailability(sum, { ...base, taskRunning: (t) => t === "scene_break" }).disabled).toBe(false);
  expect(quickReplyAvailability(sum, base).title).toBe("Refresh the scene summary now");
  expect(quickReplyAvailability(brk, base).title).toBe("Ask whether the scene should break now");
  expect(quickReplyAvailability(next, { ...base, posts: 0 }).disabled).toBe(true);
  expect(quickReplyAvailability(next, { ...base, ready: false, sceneLocked: true }))
    .toEqual({ shown: true, disabled: false, title: "Choose the next scene" });
});

test("opener is absent unless the cast panel is offered", () => {
  const o: QuickReply = { id: "o", label: "Open", kind: "opener" };
  expect(quickReplyAvailability(o, base).shown).toBe(false);
  expect(quickReplyAvailability(o, { ...base, openerOffered: true, posts: 0, ready: false }))
    .toEqual({ shown: true, disabled: true, title: "Open the opener generator" });
  expect(quickReplyAvailability(o, { ...base, openerOffered: true, posts: 0 }).disabled).toBe(false);
});

test("an unknown kind is never shown", () => {
  const p = { id: "p", label: "Ping", kind: "plugin" } as unknown as QuickReply;
  expect(quickReplyAvailability(p, base).shown).toBe(false);
});

// ---- the editor's set edits ----

const A: QuickReply = { id: "a", label: "A", kind: "opener" };
const B: QuickReply = { id: "b", label: "B", kind: "send", text: "b", mode: "send" };
const C: QuickReply = { id: "c", label: "C", kind: "opener" };

test("upsert replaces in place by id, else appends; never mutates", () => {
  const set: (QuickReplyEntry | QuickReplyDraft)[] = [A, B, C];
  const b2 = { ...B, label: "B2" };
  expect(upsertEntry(set, b2, "b")).toEqual([A, b2, C]);
  expect(upsertEntry(set, { label: "New", kind: "opener" })).toEqual([A, B, C, { label: "New", kind: "opener" }]);
  expect(set).toEqual([A, B, C]);
});

test("moveEntry swaps with a neighbour and is a no-op at either end", () => {
  expect(moveEntry([A, B, C], "a", 1)).toEqual([B, A, C]);
  expect(moveEntry([A, B, C], "c", -1)).toEqual([A, C, B]);
  expect(moveEntry([A, B, C], "a", -1)).toEqual([A, B, C]);
  expect(moveEntry([A, B, C], "c", 1)).toEqual([A, B, C]);
});

test("moveEntry can step over entries outside the list it moves within", () => {
  const hide = { id: "w", hidden: true as const };
  const among = (e: QuickReplyEntry) => !isHide(e);
  expect(moveEntry([A, hide, C], "a", 1, among)).toEqual([C, hide, A]);
  expect(moveEntry([hide, A, C], "a", -1, among)).toEqual([hide, A, C]);
});

test("remove, hide and override", () => {
  expect(removeEntry([A, B, C], "b")).toEqual([A, C]);
  expect(hideEntry([A], "w1")).toEqual([A, { id: "w1", hidden: true }]);
  const copy = overrideOf(B);
  expect(copy).toEqual(B);
  expect(copy).not.toBe(B);
});
