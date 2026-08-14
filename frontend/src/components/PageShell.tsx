import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

/** Below this the column and main cannot share a row: 274px of column leaves a
 *  375px phone about 100px of content. Matches the breakpoint `index.css` uses
 *  for the same decision — the two have to agree or the layout and the control
 *  that drives it disagree about which one is on screen. */
const PHONE_PX = 720;

/** The one shape every page is built on: a 274px context column beside main.
 *
 *  The column answers *what am I navigating*; main answers *what am I
 *  reading*. That single rule is what replaced the play view's rail plus
 *  inspector, the world view's ten-tab strip, the library's card hub and
 *  config's long scroll — so a page that wants a second navigation surface is
 *  a page that has misread its own column.
 *
 *  The internal structure is load-bearing, not decoration:
 *
 *      <aside>
 *        <div flex:1 min-height:0 overflow-y:auto>   ← `column`
 *        <div flex:none>                             ← `footer`, pinned
 *      </aside>
 *
 *  `min-height: 0` on the scrolling half is the part that is easy to drop and
 *  expensive to lose: without it a long column (a dossier, forty locations)
 *  grows the flex item past its parent, and the pinned footer — WHERE / WHEN /
 *  SKY on the play view, the theme control in config — slides out of the
 *  bottom of an `overflow: hidden` shell at short viewport heights, where it
 *  cannot be scrolled back to.
 *
 *  Main scrolls independently of the column. Both scroll independently of the
 *  header, which never moves. */
export function PageShell(
  { column, footer, children, columnLabel = "Context", className = "" }: {
    column: ReactNode;
    /** Pinned to the foot of the column, outside its scroll port. */
    footer?: ReactNode;
    children: ReactNode;
    /** Names the column for a screen reader. Pages whose column is a record
     *  index rather than navigation should say so. */
    columnLabel?: string;
    className?: string;
  },
) {
  // Two library sections in a row render the same component tree, so React
  // reuses the scroll port rather than remounting it, and the destination
  // would open at the offset the previous page was left at — heading off
  // screen, often near the bottom. Keyed to pathname only: a search or hash
  // change is the same page.
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useEffect(() => { mainRef.current?.scrollTo(0, 0); }, [pathname]);

  // ---- the phone: the column is the page, and main is a push ----
  //
  // At 375px there is no room for both, so they take turns. Main is what you
  // land on, because a deep link is a request for content and answering it with
  // an index would be ignoring it; the column arrives on demand and leaves
  // again as soon as picking something moves the route.
  //
  // That last part is what makes this a *push* rather than a tab switch: a
  // column row that navigates hands you the thing you asked for, and one that
  // only filters (the campaigns shelf's worlds) leaves the column up, because
  // you can see the filter working behind it and will probably pick another.
  //
  // `innerWidth` rather than `matchMedia` so the reading is the one the CSS
  // gets and jsdom needs no shim. Event-driven either way — no polling.
  const [phone, setPhone] = useState(() => window.innerWidth <= PHONE_PX);
  useEffect(() => {
    const onResize = () => setPhone(window.innerWidth <= PHONE_PX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const [showColumn, setShowColumn] = useState(false);
  useEffect(() => { setShowColumn(false); }, [pathname]);

  const onPhone = phone && showColumn;
  return (
    <div className={"shell" + (className ? " " + className : "")
                    + (phone ? " phone" : "") + (onPhone ? " show-column" : "")}>
      <aside className="context-column" aria-label={columnLabel}>
        <div className="column-scroll">{column}</div>
        {footer !== undefined && <div className="column-pinned">{footer}</div>}
      </aside>
      <main className="shell-main" ref={mainRef}>
        {/* Phone only, and rendered rather than CSS-hidden: a control that does
            nothing at desktop width should not be in the tab order there. */}
        {phone && (
          <button type="button" className="phone-index"
                  aria-expanded={showColumn}
                  onClick={() => setShowColumn((v) => !v)}>
            {showColumn ? "Close" : `‹ ${columnLabel}`}
          </button>
        )}
        {children}
      </main>
    </div>
  );
}

/** The two pages that legitimately have no column: the first-run wizard and
 *  the campaign wizard, both of which are one centred question at a time and
 *  would be answering "what am I navigating" with "nothing, finish this
 *  first". Everything else gets a column. */
export function PlainShell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <main className="shell-main">{children}</main>
    </div>
  );
}

/** A labelled block inside the column: a Space Mono section label, optionally
 *  with a live count on the right, above its rows. */
export function ColumnSection(
  { label, count, children }: { label: string; count?: ReactNode; children: ReactNode },
) {
  return (
    <div className="column-section">
      <div className="column-section-head">
        <span className="section-label">{label}</span>
        {count !== undefined && count !== null && <span className="column-count">{count}</span>}
      </div>
      {children}
    </div>
  );
}

export default PageShell;
