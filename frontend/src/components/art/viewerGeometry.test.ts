import { clampPan, fitScale, focusWindow } from "./viewerGeometry";

test("fit preserves tiny originals and contains maps", () => {
  expect(fitScale({ width: 100, height: 50 }, { width: 800, height: 600 })).toBe(1);
  expect(fitScale({ width: 1600, height: 800 }, { width: 400, height: 400 })).toBe(.25);
  expect(fitScale({ width: 0, height: 0 }, { width: 0, height: 0 })).toBe(1);
});
test("pan clamps both axes and shrinks after resize", () => {
  expect(clampPan({ width: 800, height: 600 }, { width: 400, height: 400 }, 1,
    { x: 999, y: -999 })).toEqual({ x: 200, y: -100 });
  expect(clampPan({ width: 800, height: 600 }, { width: 1000, height: 1000 }, 1,
    { x: 200, y: -100 })).toEqual({ x: 0, y: 0 });
});
test.each([0, 50, 100])("focus %i crops the overflowing axis", (focus) => {
  expect(focusWindow({ width: 1000, height: 500 }, 1, focus)).toEqual({ x: 500 * focus / 100, y: 0, width: 500, height: 500 });
  expect(focusWindow({ width: 500, height: 1000 }, 1.6, focus)).toEqual({ x: 0, y: 687.5 * focus / 100, width: 500, height: 312.5 });
});
