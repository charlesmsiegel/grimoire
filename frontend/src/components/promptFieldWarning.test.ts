import { promptFieldWarning } from "./promptFieldWarning";

test("only unreadable known fields produce warnings; empty, unavailable and legacy captures do not", () => {
  expect(promptFieldWarning()).toBe("");
  expect(promptFieldWarning({ world_default_prompt: "unavailable", campaign_default_prompt: "empty" })).toBe("");
  expect(promptFieldWarning({ world_default_prompt: "unreadable" })).toMatch(/world default prompt.*could not be read/i);
  expect(promptFieldWarning({ scene_focus: "unreadable" })).toMatch(/scene focus.*could not be read/i);
});
