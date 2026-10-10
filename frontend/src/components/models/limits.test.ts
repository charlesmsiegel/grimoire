import { tokensShort, windowWords } from "./limits";

test("a window is shortened downward, never overstated", () => {
  expect(tokensShort(200000)).toBe("200k");
  expect(tokensShort(32768)).toBe("32k");
  expect(tokensShort(8500)).toBe("8k");
  expect(tokensShort(1048576)).toBe("1M");
  expect(tokensShort(1990000)).toBe("1.9M");
  expect(tokensShort(512)).toBe("512");
  expect(windowWords({ value: null, source: "unknown" })).toBe("window unknown");
});
