import { changeOf, draftOf, withoutShadowed, fullLayer } from "./fieldLayer";
import type { TrackerField } from "../../api/client";

const MOOD: TrackerField = {
  key: "visible_mood", label: "Visible mood", type: "enum", aware: "present", hint: "h",
  options: ["calm", "fear"],
};
const POSE: TrackerField = { key: "pose", label: "Pose", type: "text", aware: "present", hint: "p" };

test("an enum made text is a change of type alone, with no options", () => {
  const draft = { ...draftOf(MOOD), type: "text" as const };
  expect(changeOf(MOOD, draft)).toEqual({ type: "text" });
});

test("a text field made an enum is a change of type and options", () => {
  const draft = { ...draftOf(POSE), type: "enum" as const, options: "slouch, stand" };
  expect(changeOf(POSE, draft)).toEqual({ type: "enum", options: ["slouch", "stand"] });
});

test("an untouched draft changes nothing", () => {
  expect(changeOf(MOOD, draftOf(MOOD))).toEqual({});
  expect(changeOf(POSE, draftOf(POSE))).toEqual({});
});

test("withoutShadowed drops only the additions a lower layer owns, and keeps the rest", () => {
  const own: TrackerField = { key: "gait", label: "Gait", type: "text", aware: "present", hint: "" };
  const layer = fullLayer({ fields: [POSE, own], off: ["pose"], change: { visible_mood: { label: "M" } } });
  const next = withoutShadowed(layer, new Set(["pose"]));
  expect(next.fields).toEqual([own]);
  expect(next.off).toEqual(["pose"]);
  expect(next.change).toEqual({ visible_mood: { label: "M" } });
  // Nothing to drop: the very same object, so no needless churn.
  expect(withoutShadowed(layer, new Set(["x"]))).toBe(layer);
});
