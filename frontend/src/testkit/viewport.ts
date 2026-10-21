/** jsdom reports an `innerWidth` of 1024 unless a test says otherwise. Set it
 *  before render — `PageShell` reads the width once on mount and then listens —
 *  and call the returned function to put the original back. */
export function setInnerWidth(px: number): () => void {
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: px, configurable: true, writable: true });
  return () => Object.defineProperty(window, "innerWidth",
    { value: orig, configurable: true, writable: true });
}
