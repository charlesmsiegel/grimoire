/** jsdom reports an `innerWidth` of 1024 unless a test says otherwise. Set it
 *  before render — `PageShell` reads the width once on mount and then listens —
 *  and call the returned function to put the original back.
 *
 *  Named `setInnerWidth` rather than `atWidth` so it cannot be confused with
 *  `testkit/stylesheet.ts`'s `stylesheet().atWidth`, which returns the CSS text
 *  of a `@media (max-width: Npx)` block and overrides nothing. */
export function setInnerWidth(px: number): () => void {
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: px, configurable: true, writable: true });
  return () => Object.defineProperty(window, "innerWidth",
    { value: orig, configurable: true, writable: true });
}
