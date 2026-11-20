// Sprint 115's chronicle page logic, without the app: the times a person types, the minutes an advance comes to,
// a detail's name as an id, and the sentence each clock change is reviewed in. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const logic = await import(pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static", "pages", "chronicle", "logic.js")).href);

test("a 24-hour time is minutes into the day, and anything else is refused", () => {
  assert.equal(logic.parseTime("00:00"), 0);
  assert.equal(logic.parseTime(" 9:30 "), 570);
  assert.equal(logic.parseTime("23:59"), 1439);
  for (const bad of ["24:00", "12:60", "9.30", "noon", "", undefined]) assert.equal(logic.parseTime(bad), null, String(bad));
  assert.equal(logic.clockTime(570), "09:30");
  assert.equal(logic.clockTime(0), "00:00");
});

test("an advance is whole days, hours and minutes adding up to at least a minute", () => {
  assert.equal(logic.advanceMinutes("1", "2", "3"), 1440 + 120 + 3);
  assert.equal(logic.advanceMinutes("", "", "45"), 45);
  assert.equal(logic.advanceMinutes("0", "0", "0"), null);
  assert.equal(logic.advanceMinutes("1.5", "", ""), null);
  assert.equal(logic.advanceMinutes("-1", "", ""), null);
});

test("a detail's name becomes an id the store accepts, or nothing", () => {
  assert.equal(logic.topicId("Visitor identity"), "visitor-identity");
  assert.equal(logic.topicId("  Café — owner?  "), "cafe-owner");
  assert.equal(logic.topicId("???"), "");
  assert.equal(logic.topicId("x".repeat(300)).length, 100);
  assert.notEqual(logic.newOperationId(), logic.newOperationId());
});

test("each clock change is reviewed in words that say what it does", () => {
  const before = { date: "1024-03-01", time: "10:00" }, after = { date: "1024-03-06", time: "10:00" };
  assert.equal(logic.planText({ operation: "initialize" }, { after: before }), "Start the clock at 1024-03-01 10:00.");
  assert.equal(logic.planText({ operation: "schedule", label: "The ship sails", kind: "deadline", date: "1024-03-05", minute: 570 }, { after: before }),
    "Schedule \"The ship sails\" (deadline) for 1024-03-05 09:30. The clock stays at 1024-03-01 10:00.");
  assert.equal(logic.planText({ operation: "advance" }, { before, after }), "Advance time from 1024-03-01 10:00 to 1024-03-06 10:00.");
  assert.match(logic.planText({ operation: "advance" }, { already_applied: true }), /already made/);
});
