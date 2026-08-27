import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Chore, ChoreItems, TodoPayload } from "../api/types";
import { PageShell, ColumnSection } from "../components/PageShell";

/** Everything the app noticed that would make play better.
 *
 *  Two properties carry this page, and both are easy to lose:
 *
 *  **Every chore is a live count.** The label is built from a number the server
 *  computed on this request, and a chore at zero is not in the list at all.
 *  Accepting the last proposal removes the absorb chore; writing the last
 *  anchor removes the voice one. A list that can go stale teaches the reader to
 *  distrust it, and then the one entry that mattered is the one they scroll
 *  past.
 *
 *  **Ignoring is real.** An ignored chore is counted nowhere — not in the
 *  rail's badge, not in this page's own total — and it moves to its own section
 *  with a Restore, so waving something off is reversible rather than forgotten.
 *  A dismissal that cannot be undone is one nobody dares make.
 */

/** What a scope reads as on a row.
 *
 * Two labels for three scopes, deliberately. The three-way split is what the
 * SERVER needs — it decides which builders can answer with no campaign open —
 * and a reader only ever asks the one question the chip is here to settle: is
 * this about the campaign I have open, or about everything else. */
const SCOPE_LABEL: Record<Chore["scope"], string> = {
  campaign: "This campaign",
  world: "Your library",
  library: "Your library",
};

function Row({ chore, onIgnore, busy, restore, cid, showScope }: {
  chore: Chore; onIgnore: (id: string, on: boolean) => void;
  busy: boolean; restore?: boolean; cid: string | null; showScope?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ChoreItems | null>(null);
  const [failed, setFailed] = useState(false);

  // Fetched when the row is first opened, and kept afterwards. Naming every
  // instance of every chore up front is the cost the list exists to avoid —
  // `sheets` sweeps the cast, `taglines` walks the roster — and the reader
  // expands one at a time.
  useEffect(() => {
    if (!open || items || failed) return;
    let live = true;
    api.getChoreItems(chore.id, cid)
      .then((r) => { if (live) setItems(r); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [open, items, failed, chore.id, cid]);

  const panelId = `chore-items-${chore.id}`;
  return (
    <li className={"chore chore-" + chore.severity}>
      <div className="chore-line">
        <span className="chore-dot" aria-hidden />
        <div className="chore-body">
          <button type="button" className="chore-what" aria-expanded={open}
                  aria-controls={panelId} onClick={() => setOpen((v) => !v)}>
            <span className="chore-caret" aria-hidden>{open ? "▾" : "▸"}</span>
            {chore.what}
            {/* Inside the button, not beside it. `taglines` and
                `world-taglines` render the same sentence — "3 characters with
                no tagline" — so without this they are two controls with one
                accessible name, which is the version of the collision a screen
                reader gets and cannot see its way around. */}
            {showScope && (
              <span className="chore-scope">{SCOPE_LABEL[chore.scope]}</span>
            )}
          </button>
          {/* The half a bare count cannot carry. A number with no consequence
              attached is a number the reader learns to skip. */}
          <div className="chore-why">{chore.why}</div>
        </div>
        <div className="chore-actions">
          {chore.fix && !restore && (
            <Link className="chore-fix" to={chore.fix}>{chore.fix_label} →</Link>
          )}
          <button type="button" className="chore-ignore" disabled={busy}
                  onClick={() => onIgnore(chore.id, !restore)}>
            {restore ? "Restore" : "Ignore"}
          </button>
        </div>
      </div>

      {open && (
        <div className="chore-items" id={panelId}>
          {failed && <p className="field-hint">These could not be read.</p>}
          {!failed && !items && <p className="field-hint">…</p>}
          {items && items.items.length === 0 && (
            // The count said there was something. An empty expansion means the
            // two disagree, which is worth saying rather than showing nothing.
            <p className="field-hint">Nothing to list here.</p>
          )}
          {items && items.items.length > 0 && (
            <ul>
              {items.items.map((it) => (
                <li key={it.id}>
                  {it.fix
                    ? <Link className="chore-item-label" to={it.fix}>{it.label}</Link>
                    : <span className="chore-item-label">{it.label}</span>}
                  {it.detail && <span className="chore-item-detail">{it.detail}</span>}
                </li>
              ))}
            </ul>
          )}
          {items?.truncated && (
            // A cap nobody mentions reads as "that is all of them".
            <p className="field-hint">
              Showing {items.items.length} of {items.total}.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export default function TodoView({ cid }: { cid: string | null }) {
  const [data, setData] = useState<TodoPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    let live = true;
    setFailed(false);
    api.getTodo(cid)
      .then((d) => { if (live) setData(d); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [cid]);

  useEffect(load, [load]);

  function ignore(id: string, on: boolean) { void ignoreAsync(id, on); }

  async function ignoreAsync(id: string, on: boolean) {
    setBusy(true);
    try {
      await api.setChoreIgnored(id, on);
      // Re-read rather than patching in place: ignoring is not the only thing
      // that can have changed the list, and the counts are the point.
      setData(await api.getTodo(cid));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  const chores = data?.chores ?? [];
  const groups: string[] = [];
  for (const c of chores) if (!groups.includes(c.group)) groups.push(c.group);

  // The chip earns its place only where it tells two rows apart, so it appears
  // exactly when both kinds are on the page. With no campaign open every row is
  // the library's and the label would repeat down the whole list, which is the
  // count-with-no-consequence problem in another form: a word on every row is a
  // word the reader learns to skip.
  //
  // Falsy labels are dropped rather than counted: `scope` is a field a response
  // predating it would not carry, and an `undefined` in the set would turn the
  // chip on and then render nothing.
  const scopeLabels = new Set(
    [...chores, ...(data?.ignored ?? [])].map((c) => SCOPE_LABEL[c.scope]).filter(Boolean),
  );
  const showScope = scopeLabels.size > 1;

  const column = (
    <ColumnSection label="Groups" count={data?.count ?? undefined}>
      {groups.map((g) => (
        <a key={g} className="column-row" href={`#${encodeURIComponent(g)}`}>
          <span className="column-row-label">{g}</span>
          <span className="column-count">
            {chores.filter((c) => c.group === g).length}
          </span>
        </a>
      ))}
      {/* No second copy of "nothing outstanding" here: main says it, and the
          section's own count already says 0. Two elements carrying one fact is
          two places for it to disagree. */}
    </ColumnSection>
  );

  return (
    <PageShell column={column} columnLabel="To do">
      <div className="page-wide view-anim">
        <div className="eyebrow">Everything that would make play better</div>
        <h1 className="screen-title">To do</h1>

        {failed && (
          <div className="banner error-banner">
            The list could not be read.{" "}
            <button className="subtle" onClick={load}>Try again</button>
          </div>
        )}

        {!failed && data && (
          <p className="field-hint">
            {data.count === 0
              ? "Nothing outstanding. Anything ignored is below."
              : `${data.count} thing${data.count === 1 ? "" : "s"} the app noticed. `
                + "Ignore anything you disagree with."}
            {/* Not an empty state, and deliberately not instead of the list.
                The library's own chores — an undescribed image backlog, a world
                whose cast has no taglines — answer with no campaign open, so
                there is something here to read; what is missing is the half
                about the campaign being played. Saying only "open a campaign
                first" here used to hide a list that had entries. */}
            {!cid && (
              <>
                {" "}Chores about a campaign need one open:{" "}
                <Link to="/">pick a campaign</Link>.
              </>
            )}
          </p>
        )}

        {groups.map((g) => (
          <section key={g} id={g}>
            <h2 className="chore-group">{g}</h2>
            <ul className="chore-list">
              {chores.filter((c) => c.group === g).map((c) => (
                <Row key={c.id} chore={c} onIgnore={ignore} busy={busy} cid={cid}
                     showScope={showScope} />
              ))}
            </ul>
          </section>
        ))}

        {!!data?.ignored.length && (
          <section>
            <h2 className="chore-group">Ignored</h2>
            <p className="field-hint">
              Not counted anywhere, and not gone: these are here so the decision
              can be taken back.
            </p>
            <ul className="chore-list">
              {data.ignored.map((c) => (
                <Row key={c.id} chore={c} onIgnore={ignore} busy={busy} restore cid={cid}
                     showScope={showScope} />
              ))}
            </ul>
          </section>
        )}
      </div>
    </PageShell>
  );
}
