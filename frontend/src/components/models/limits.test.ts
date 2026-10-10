import { compactTokens, windowWords } from "./limits";

test("a window is said the way providers round it", () => {
  expect(compactTokens(131072)).toBe("128k");
  expect(compactTokens(200000)).toBe("200k");
  expect(compactTokens(8192)).toBe("8k");
  expect(compactTokens(32000)).toBe("32k");
  expect(compactTokens(1000000)).toBe("1M");
  expect(compactTokens(1048576)).toBe("1M");
  expect(compactTokens(4097)).toBe("4k");
  expect(compactTokens(512)).toBe("512");
});

test("an unknown window says so, never zero", () => {
  expect(windowWords({ value: 200000, source: "catalog" })).toBe("200k window");
  expect(windowWords({ value: null, source: "unknown" })).toBe("window unknown");
  expect(windowWords(null)).toBe("window unknown");
});
