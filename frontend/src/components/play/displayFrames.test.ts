import { applyDisplay } from "./displayFrames";

test("applyDisplay retracts within the current part only", () => {
  expect(applyDisplay("A said.B<thi", 7, { keep: 1, tail: "" })).toBe("A said.B");
});

test("applyDisplay appends the tail after what it keeps", () => {
  expect(applyDisplay("", 0, { keep: 0, tail: "Hello" })).toBe("Hello");
  expect(applyDisplay("Hello", 0, { keep: 5, tail: " there" })).toBe("Hello there");
  // A closing tag can take back more than the last frame added.
  expect(applyDisplay("Hi <think>plan", 0, { keep: 3, tail: "" })).toBe("Hi ");
});

test("applyDisplay never reaches into an earlier part", () => {
  // keep 0 empties the current part and leaves the one before it whole.
  expect(applyDisplay("First.Second", 6, { keep: 0, tail: "Next" })).toBe("First.Next");
});

test("applyDisplay counts keep in characters the server counts, not UTF-16 units", () => {
  // The server is Python: an emoji outside the BMP is one character there and
  // two code units here, so a plain slice would cut it in half.
  expect(applyDisplay("x.\u{1F600}ab", 2, { keep: 2, tail: "!" })).toBe("x.\u{1F600}a!");
});
