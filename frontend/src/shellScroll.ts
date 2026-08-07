/** Scroll the shell's main column back to the top.
 *
 *  `window.scrollTo(0, 0)` no longer does this. The shell pins a status bar to
 *  the bottom of the window, which is only honest if the page cannot scroll
 *  out from under it, so `.shell-main` owns the scroll offset and the document
 *  never scrolls — leaving a window-level scroll a silent no-op that drops the
 *  reader into the middle of the record they just opened.
 *
 *  Falls back to the window for anything mounted outside the shell: the record
 *  editors are rendered bare in their own tests, and a helper that only worked
 *  inside `App` would make those tests pass while proving nothing. */
export function scrollShellToTop(): void {
  const main = document.querySelector(".shell-main");
  if (main) main.scrollTo(0, 0);
  else window.scrollTo(0, 0);
}
