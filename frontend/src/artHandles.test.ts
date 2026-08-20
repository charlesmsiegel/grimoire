import { hideArtHandles } from "./artHandles";

test("a complete handle is hidden while the reply streams", () => {
  expect(hideArtHandles("She turns. [[art:characters:sera:gallery_1]] Rain."))
    .toBe("She turns.  Rain.");
  // ...backticked too, since resolution tolerates that shape on the way in
  expect(hideArtHandles("Look. `[[art:campaign:coastline]]`")).toBe("Look. ");
});

test("a half-arrived handle at the tail is hidden rather than flickering", () => {
  // Every prefix of a handle is a state the stream really passes through.
  for (const partial of ["[", "[[", "[[a", "[[ar", "[[art", "[[art:",
                         "[[art:characters", "[[art:characters:sera:gall"]) {
    const out = hideArtHandles(`She turns. ${partial}`);
    expect(out).toBe(partial === "[" ? "She turns. [" : "She turns. ");
  }
});

test("prose that merely contains brackets is left alone", () => {
  expect(hideArtHandles("A [[wiki link]] and [a link](/x)."))
    .toBe("A [[wiki link]] and [a link](/x).");
  // a stray `[[` that is NOT at the tail is the author's, not a partial handle
  expect(hideArtHandles("He said [[ and then stopped talking."))
    .toBe("He said [[ and then stopped talking.");
});

test("resolved markdown is untouched — it is already the finished form", () => {
  const done = "She turns. ![Rain-soaked.](/api/campaigns/c/images/x) Rain.";
  expect(hideArtHandles(done)).toBe(done);
});
