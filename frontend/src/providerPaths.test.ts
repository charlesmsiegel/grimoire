import { EDIT_LIMITS, EDIT_RATES, modelLimitsPath, modelPath, modelRatesPath } from "./providerPaths";

test("a model's address encodes the model segment by segment", () => {
  expect(modelPath("saltmarch", "vendor/m")).toBe("/providers/saltmarch/models/vendor/m");
  expect(modelPath("salt march", "a b/c?d")).toBe("/providers/salt%20march/models/a%20b/c%3Fd");
});

test("modelLimitsPath opens the size form, encoding per segment", () => {
  expect(EDIT_LIMITS).toBe("limits");
  expect(modelLimitsPath("saltmarch", "vendor/m"))
    .toBe("/providers/saltmarch/models/vendor/m?edit=limits");
  expect(modelLimitsPath("saltmarch", "vendor/m #2"))
    .toBe("/providers/saltmarch/models/vendor/m%20%232?edit=limits");
  // Its sibling, unchanged.
  expect(modelRatesPath("saltmarch", "vendor/m"))
    .toBe(`/providers/saltmarch/models/vendor/m?edit=${EDIT_RATES}`);
});
