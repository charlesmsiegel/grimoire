/** One arrow of a swipe control. */
export type SwipeStep = { name: string; disabled: boolean; run: () => void };

/** ‹ n/m ›: which of a reply's takes is on screen, and the way to the others.
 *
 *  Under the text of the one post it tours, and always drawn there. It used to
 *  sit in the hover gutter, which hid the count behind a pointer a touch
 *  screen does not have and squeezed two ~5px arrows and the figure into a
 *  26px column they overflowed. The arrows are real targets now (the
 *  stylesheet takes them to 44px where nothing hovers), and while a turn runs
 *  they are disabled rather than gone, so the row does not jump. */
export function SwipeNav({ shown, title, previous, next }: {
  /** The position, as read: "2/3", or "–/3" with nothing active. */
  shown: string;
  /** What the active take was made with, for the figure's tooltip. */
  title?: string | undefined;
  previous: SwipeStep;
  next: SwipeStep;
}) {
  return (
    <div className="swipe-nav">
      <button type="button" aria-label={previous.name} disabled={previous.disabled}
              onClick={previous.run}>‹</button>
      <span className="swipe-count" title={title}>{shown}</span>
      <button type="button" aria-label={next.name} disabled={next.disabled}
              onClick={next.run}>›</button>
    </div>
  );
}
