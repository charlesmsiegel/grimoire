import { EDIT_LIMITS, EDIT_RATES, modelLimitsPath, modelRatesPath } from "./providerPaths";

test("modelLimitsPath encodes the model per segment, so its own slash stays a path", () => {
  expect(modelLimitsPath("saltmarch", "vendor/m")).toBe(
    "/providers/saltmarch/models/vendor/m?edit=limits");
  expect(modelLimitsPath("salt march", "vendor/m?x#y")).toBe(
    "/providers/salt%20march/models/vendor/m%3Fx%23y?edit=limits");
  expect(EDIT_LIMITS).not.toBe(EDIT_RATES);
  expect(modelRatesPath("saltmarch", "vendor/m")).toBe(
    "/providers/saltmarch/models/vendor/m?edit=rates");
});
