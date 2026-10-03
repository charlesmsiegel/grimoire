import type { TrackerEntry } from "../../api/client";
import { lastOkKey } from "./TrackerNow";

const entry = (status: TrackerEntry["status"]): TrackerEntry => ({
  status, changed: [], flags: { upstream_changed: false, text_changed: false },
});
const keys = (...ks: string[]) => ks.map((key, index) => ({ index, key }));

describe("lastOkKey", () => {
  test("the latest ok key wins", () => {
    expect(lastOkKey(keys("a", "b", "c"),
      { a: entry("ok"), b: entry("ok"), c: entry("ok") })).toBe("c");
  });

  test("a pending tail still shows the previous ok key", () => {
    expect(lastOkKey(keys("a", "b", "c"),
      { a: entry("ok"), b: entry("ok"), c: entry("pending") })).toBe("b");
  });

  test("a failed key in the middle is skipped, not a stop", () => {
    expect(lastOkKey(keys("a", "b", "c"),
      { a: entry("ok"), b: entry("failed"), c: entry("pending") })).toBe("a");
    expect(lastOkKey(keys("a", "b", "c"),
      { a: entry("ok"), b: entry("failed"), c: entry("ok") })).toBe("c");
  });

  test("a key with no entry is not ok", () => {
    expect(lastOkKey(keys("a", "b"), { a: entry("ok") })).toBe("a");
  });

  test("all failed, all pending, or no keys at all is null", () => {
    expect(lastOkKey(keys("a", "b"), { a: entry("failed"), b: entry("failed") })).toBeNull();
    expect(lastOkKey(keys("a"), { a: entry("pending") })).toBeNull();
    expect(lastOkKey([], {})).toBeNull();
  });
});
