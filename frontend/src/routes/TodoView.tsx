import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Chore, TodoPayload } from "../api/types";
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

function Row({ chore, onIgnore, busy, restore }: {
  chore: Chore; onIgnore: (id: string, on: boolean) => void;
  busy: boolean; restore?: boolean;
}) {
  return (
    <li className={"chore chore-" + chore.severity}>
      <span className="chore-dot" aria-hidden />
      <div className="chore-body">
        <div className="chore-what">{chore.what}</div>
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

        {!cid && !failed && (
          // Every chore this page can compute is about a campaign. Saying so is
          // better than an empty list, which would read as "nothing to do".
          <p className="empty-state">
            <span className="empty-what">Open a campaign first.</span>{" "}
            What the app can notice is about the campaign you are playing.
          </p>
        )}

        {cid && !failed && data && (
          <p className="field-hint">
            {data.count === 0
              ? "Nothing outstanding. Anything ignored is below."
              : `${data.count} thing${data.count === 1 ? "" : "s"} the app noticed. `
                + "Ignore anything you disagree with."}
          </p>
        )}

        {groups.map((g) => (
          <section key={g} id={g}>
            <h2 className="chore-group">{g}</h2>
            <ul className="chore-list">
              {chores.filter((c) => c.group === g).map((c) => (
                <Row key={c.id} chore={c} onIgnore={ignore} busy={busy} />
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
                <Row key={c.id} chore={c} onIgnore={ignore} busy={busy} restore />
              ))}
            </ul>
          </section>
        )}
      </div>
    </PageShell>
  );
}
