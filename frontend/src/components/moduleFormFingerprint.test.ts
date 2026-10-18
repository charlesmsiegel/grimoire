import { moduleFormFingerprint } from "./moduleFormFingerprint";

test("object member insertion order does not make a module form dirty", () => {
  expect(moduleFormFingerprint({ fields: [{ max: 5, min: 0, default: 2 }], label: "" }))
    .toBe(moduleFormFingerprint({ label: "", fields: [{ min: 0, default: 2, max: 5 }] }));
});

test("field and group array order still changes a module form", () => {
  expect(moduleFormFingerprint({ fields: [{ key: "a" }, { key: "b" }] }))
    .not.toBe(moduleFormFingerprint({ fields: [{ key: "b" }, { key: "a" }] }));
  expect(moduleFormFingerprint({ groups: ["a", "b"] }))
    .not.toBe(moduleFormFingerprint({ groups: ["b", "a"] }));
});

test.each([[null, false], [false, 0], [0, ""], ["", null]])(
  "distinct JSON values stay distinct: %s and %s", (before, after) => {
    expect(moduleFormFingerprint({ value: before })).not.toBe(moduleFormFingerprint({ value: after }));
  });

test("omitted optional values differ from an explicit null", () => {
  expect(moduleFormFingerprint({})).not.toBe(moduleFormFingerprint({ max: null }));
});
