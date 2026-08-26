import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent,
         type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { APP_ROWS, CAMPAIGN_ROWS, type RailCtx, type RailRow } from "../shell/rail";
import type { ShellPayload } from "../api/types";

/** The app's navigation, and the half of the shell that never moves.
 *
 *  The rule this component and `PageShell` split between them:
 *
 *      The rail navigates the app. A column indexes the page.
 *
 *  The rail answers *which page of the app am I on* — a question whose answer
 *  is the same on every page, so it is asked once, in chrome that outlives
 *  every route. A page's column answers *which of this page's records am I
 *  reading*, which only that page can ask. A page that builds a second surface
 *  to answer the rail's question has misread the rail; a page that puts its
 *  records in the rail has misread its column.
 *
 *  Two tiers: the app, and the campaign that is open. The second tier is
 *  visible from anywhere, which is the whole point — "what is waiting in my
 *  campaign" is not a question you should have to navigate into the campaign to
 *  ask.
 *
 *  Rows are driven by the tables in `shell/rail.ts` rather than written out
 *  here, and a row whose destination does not exist yet is **not rendered** —
 *  absent from the DOM, not disabled. Most campaign-tier pages are still to be
 *  built, so the rail ships complete in shape and sparse in fact.
 */

function tailOf(row: RailRow, payload: ShellPayload | null) {
  const text = row.tail?.(payload);
  // `undefined` and `"0"` are different answers and must stay so: `0` means
  // nothing is waiting, absent means nobody computed it. Rendering the second
  // as the first is the cost rule's mistake in another domain.
  if (text === undefined) return { text: undefined, label: row.label };
  return { text, label: `${row.label}, ${row.tailLabel?.(payload) ?? text}` };
}

function Row({ row, ctx, payload, onPick }: {
  row: RailRow; ctx: RailCtx; payload: ShellPayload | null; onPick: () => void;
}) {
  const { pathname } = useLocation();
  const to = row.to(ctx);
  if (to === null) return null;
  const active = row.match(pathname, ctx);
  const { text, label } = tailOf(row, payload);
  // `Link`, not `NavLink`, and that is the point rather than a preference.
  // NavLink decides "active" for itself with a prefix match, so it would mark
  // Play as the current page on /campaigns/:cid/ledger — the two-active-rows
  // defect that `row.match` exists to prevent, arriving through the back door
  // of the component instead of the table. One source of truth, and it is the
  // table.
  return (
    <Link to={to} onClick={onPick} aria-label={label}
          aria-current={active ? "page" : undefined}
          className={"rail-row" + (active ? " active" : "")}>
      <span className="rail-icon" aria-hidden>{row.icon}</span>
      <span className="rail-label">{row.label}</span>
      {text !== undefined && <span className="rail-tail" aria-hidden>{text}</span>}
    </Link>
  );
}

function Tier({ label, aria, rows, ctx, payload, onPick, children }: {
  /** The eyebrow a reader sees. */
  label: string;
  /** What a screen reader calls the landmark. Separate from `label` because
   *  the app tier's eyebrow is the wordmark, and a landmark called "Grimoire"
   *  inside an app called Grimoire names nothing. */
  aria: string;
  rows: RailRow[]; ctx: RailCtx;
  payload: ShellPayload | null; onPick: () => void; children?: ReactNode;
}) {
  return (
    <nav className="rail-tier" aria-label={aria}>
      <div className="rail-eyebrow">{label}</div>
      {children}
      {rows.map((r) => (
        <Row key={r.id} row={r} ctx={ctx} payload={payload} onPick={onPick} />
      ))}
    </nav>
  );
}

export default function AppRail(
  { payload, status, cid, dataDir, open, onClose, docked, onRetry }: {
    payload: ShellPayload | null;
    status: "loading" | "ready" | "failed";
    cid: string | null;
    dataDir: string;
    /** Drawer state. Ignored while `docked`. */
    open: boolean;
    onClose: () => void;
    docked: boolean;
    onRetry: () => void;
  },
) {
  const ctx: RailCtx = { cid };
  const camp = payload?.campaign ?? null;
  const panel = useRef<HTMLDivElement>(null);

  // As a drawer this is a dialog, and `useHotkeys({modal:true})` only supplies
  // Escape and the suppression of the view's own bindings — it does not move
  // focus, contain Tab, or name anything. The rest is here.
  const drawer = !docked && open;
  useHotkeys(drawer ? [{ keys: "escape", run: onClose }] : [], { modal: drawer });

  // Where focus was when the drawer opened, so closing can put it back.
  const returnTo = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!drawer) return;
    returnTo.current = document.activeElement as HTMLElement | null;
    // Focus goes into the drawer, so the first Tab is inside it rather than
    // behind it.
    panel.current?.focus();
    return () => {
      const back = returnTo.current;
      returnTo.current = null;
      // `isConnected` is the whole reason this is a check rather than a call.
      // The control that opens the drawer is the header's ☰, which only exists
      // below RAIL_PX — so the one close that is triggered by *widening* has no
      // opener left to return to. Focusing a detached node sends focus to
      // <body>, silently losing the reader's place; skipping leaves it where
      // the newly docked rail can pick it up.
      if (back?.isConnected) back.focus();
    };
  }, [drawer]);

  /** Tab containment, as a handler on the dialog rather than a listener on it.
   *
   *  `useHotkeys({modal:true})` gives Escape and the suppression of the view's
   *  own bindings; it does not stop Tab leaving. Without this the reader tabs
   *  out of an `aria-modal` dialog and through the header and page controls
   *  visually behind it — the part of modality that is invisible to a mouse and
   *  total to a keyboard.
   *
   *  Not a registry binding, and not an `addEventListener`: `shortcuts/` owns
   *  the keys the app *answers*, and Tab here is not answered, only kept from
   *  leaving one element. */
  function containTab(e: ReactKeyboardEvent<HTMLElement>) {
    if (e.key !== "Tab") return;
    const node = panel.current;
    if (!node) return;
    const stops = node.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (!stops.length) return;
    const first = stops[0], last = stops[stops.length - 1];
    const on = document.activeElement;
    if (e.shiftKey && (on === first || on === node)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && on === last) { e.preventDefault(); first.focus(); }
  }

  // Picking a row closes the drawer; so does any other route change, whichever
  // way it happened (palette, back button, a link in the page).
  const { pathname } = useLocation();
  useEffect(() => { if (!docked) onClose(); }, [pathname, docked, onClose]);

  const body = (
    <>
      <Tier label="Grimoire" aria="Main" rows={APP_ROWS} ctx={ctx} payload={payload}
            onPick={docked ? () => {} : onClose} />

      {camp && (
        <Tier label="Open campaign" aria="Open campaign" rows={CAMPAIGN_ROWS} ctx={ctx} payload={payload}
              onPick={docked ? () => {} : onClose}>
          <div className="rail-campaign">
            <div className="rail-campaign-name">{camp.name}</div>
            {/* Derived, never a literal: every part of this line is computed
                from the payload, so it cannot agree with the data on the day it
                was written and drift the moment anything changes. */}
            <div className="rail-campaign-meta">
              {[camp.world_name,
                `${camp.scenes} ${camp.scenes === 1 ? "scene" : "scenes"}`,
                camp.open.length ? `${camp.open.length} open` : null,
              ].filter(Boolean).join(" · ")}
            </div>
          </div>
        </Tier>
      )}

      {camp && camp.open.length > 0 && (
        <nav className="rail-tier" aria-label="Open scenes">
          <div className="rail-eyebrow">{camp.open.length} open</div>
          {camp.open.map((s) => (
            <Link key={s.sid} to={`/campaigns/${camp.id}/scenes/${s.sid}`}
                  onClick={docked ? undefined : onClose}
                  className="rail-row rail-scene">
              <span className="rail-label">{s.title}</span>
              {/* `turns` is null in this slice, so no tail — rather than a
                  count derived from something that would be wrong for exactly
                  the oldest scenes. */}
              {s.turns !== null && <span className="rail-tail" aria-hidden>{s.turns}t</span>}
            </Link>
          ))}
        </nav>
      )}

      {status === "failed" && (
        <div className="rail-stale">
          <span>Counts may be out of date.</span>
          <button type="button" onClick={onRetry}>Retry</button>
        </div>
      )}

      <div className="rail-foot">
        <span className="rail-root" title={dataDir}>{dataDir || "~/.grimoire"}</span>
        <span className="rail-local">LOCAL</span>
      </div>
    </>
  );

  if (docked) return <aside className="app-rail">{body}</aside>;
  if (!open) return null;
  return (
    <>
      <div className="rail-backdrop" onClick={onClose} aria-hidden />
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions --
          the rule counts `dialog` as non-interactive, which is right for its
          usual case and wrong for this one: a modal dialog is exactly where a
          key handler belongs, and the handler is what CONTAINS Tab rather than
          answering it. The element is focusable (tabIndex -1) and named. */}
      <aside className="app-rail drawer" ref={panel} tabIndex={-1}
             onKeyDown={containTab}
             role="dialog" aria-modal="true" aria-label="Navigation">
        {body}
      </aside>
    </>
  );
}
