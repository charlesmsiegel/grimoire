import type { InferenceSettings, MigrationStatus } from "../../api/client";
import { migrationBanner, migrationLine } from "./migration";

const BUSY = "campaign saltmarch: busy; finished on the next start";

function view(format: string, migration: Partial<MigrationStatus>, newer = false) {
  return {
    format, newer,
    migration: { state: "done", reason: "", skipped: [], ...migration },
  } as unknown as InferenceSettings;
}

test("the banner speaks before the switch and for a newer library, and never after", () => {
  expect(migrationBanner(undefined)).toBeNull();
  expect(migrationBanner(view("1", { state: "pending" }))?.state).toBe("pending");
  expect(migrationBanner(view("3", {}, true))?.state).toBe("newer");
  expect(migrationBanner(view("2", { state: "newer" }))?.state).toBe("newer");
  expect(migrationBanner(view("2", { state: "failed", reason: "disk full" }))).toBeNull();
});

test("the line is silent where the banner speaks, and when nothing is left", () => {
  expect(migrationLine(null)).toBeNull();
  expect(migrationLine(view("1", { state: "pending", skipped: [BUSY] }))).toBeNull();
  expect(migrationLine(view("3", { skipped: [BUSY] }, true))).toBeNull();
  expect(migrationLine(view("2", { state: "done" }))).toBeNull();
});

test("the line says what is left, and a reason and a skip are both kept", () => {
  expect(migrationLine(view("2", { state: "running" })))
    .toBe("Part of this library has not finished upgrading yet.");
  expect(migrationLine(view("2", { state: "pending", skipped: [BUSY] })))
    .toBe(`The upgrade left 1 thing for later (${BUSY}).`);
  expect(migrationLine(view("2", { state: "failed", reason: "disk full." })))
    .toBe("The upgrade has not finished: disk full.");
  expect(migrationLine(view("2", { state: "failed", reason: "disk full", skipped: [BUSY] })))
    .toBe(`The upgrade has not finished: disk full. It left 1 thing for later (${BUSY}).`);
  expect(migrationLine(view("2", { state: "done", skipped: [BUSY, "provider local: refused"] })))
    .toBe(`The upgrade left 2 things for later (${BUSY}; provider local: refused).`);
});
