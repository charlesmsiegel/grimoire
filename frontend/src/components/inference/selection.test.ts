import { droppedFallbackWords } from "./selection";

test("a fallback known unable to call tools says so in the server's phrase", () => {
  expect(droppedFallbackWords(["tools"], "Planted tool runs")).toBe(
    "The fallback is known not to fit Planted tool runs (it cannot call tools), "
    + "so it is never sent.");
});
