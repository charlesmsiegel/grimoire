import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Chore, ChoreItems, TodoPayload } from "../api/types";
import { useRevalidated } from "../api/useRevalidated";
import { PageShell, ColumnSection } from "../components/PageShell";
import ReportScopeSelector from "../components/ReportScopeSelector";
import { sceneNumber } from "./sceneNumber";

/** Everything the app noticed that would make play better.
 *
 *  Two properties carry this page, and both are easy to lose:
 *
 *  **Every chore is a live count.** The label is built from a number the server
 *  computed for this request, and a chore at zero is not in the list at all.
 *  Some of what a chore is computed from is reused between requests, under a
 *  key covering every file it read, so a reused input is never older than
 *  those files' stamps (`docs/store-guarantees.md` says where a stamp can miss
 *  a rewrite). Accepting the last proposal removes the absorb chore; writing
 *  the last anchor removes the voice one. A list that can go stale teaches the
 *  reader to distrust it, and then the one entry that mattered is the one they
 *  scroll past.
 *
 *  The one exception is on this side: a second visit to the same scope paints
 *  the last answer this tab had while its own read is on the wire -- for one
 *  revalidated frame, marked busy, with Ignore and Restore disabled -- and the
 *  answer replaces it wholesale (`useRevalidated`).
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

const PRIORITIES = ["alert", "warn", "note"] as const;
const PRIORITY_LABEL: Record<Chore["severity"], string> = {
  alert: "Needs attention", warn: "Recommended", note: "Suggestion",
};
const PRIORITY_ORDER: Record<Chore["severity"], number> = { alert: 0, warn: 1, note: 2 };
const PREVIEW_SIZE = 3;

function scopeName(chore: Chore) {
  return chore.campaign_name || chore.campaign_id || SCOPE_LABEL[chore.scope];
}

/** A preview is a read of the records behind one task, paid only when visible.
 * Active scenes are the deliberate exception: they are a full navigation list
 * at the top, from the existing uncapped scene listing, including campaigns
 * whose incomplete-scene reminder was ignored. Other records load on sight.
 * A new summary object invalidates its preview too,
 * so a reload after Ignore or a settled run cannot keep yesterday's names under
 * today's count. Grouping changes presentation, never the task's campaign. */
function RecordPreview({ chore, cid, full = false }: {
  chore: Chore; cid: string | null; full?: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(() => full || typeof IntersectionObserver === "undefined");
  const [expanded, setExpanded] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{
    chore: Chore; cid: string | null;
  } & ({ items: ChoreItems } | { failed: true }) | null>(null);

  useEffect(() => {
    if (visible || !element.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    let live = true;
    const campaign = chore.campaign_id ?? (chore.scope === "campaign" ? cid : null);
    // The task-items endpoint caps at 200. A list of *all* active scenes cannot
    // silently inherit that cap, and the ordinary scene listing already carries
    // the same done/closed-by fields the task's live enumeration filters.
    const read = full && campaign ? api.listScenes(campaign).then((scenes): ChoreItems => {
      const active = scenes.filter((scene) => !scene.done && !scene.closed_by);
      const titles = new Map<string, { total: number; ordinals: Map<number | null, number> }>();
      for (const scene of active) {
        let group = titles.get(scene.title);
        if (!group) {
          group = { total: 0, ordinals: new Map() };
          titles.set(scene.title, group);
        }
        group.total++;
        const number = sceneNumber(scene.id);
        group.ordinals.set(number, (group.ordinals.get(number) ?? 0) + 1);
      }
      return { items: active.map((scene) => {
        const group = titles.get(scene.title);
        let label = scene.title;
        if (group && group.total > 1) {
          // Updated-order list position is not a story number. Branch siblings
          // can share even that number, and legacy ids carry none: only those
          // collisions need the id as a last-resort distinction.
          const number = sceneNumber(scene.id);
          const suffix = number !== null && group.ordinals.get(number) === 1
            ? `Scene ${number}` : scene.id;
          label += ` (${suffix})`;
        }
        return {
          id: scene.id, label,
          detail: [scene.date, scene.place].filter(Boolean).join(" · ") || "No time or place set",
          fix: `/campaigns/${campaign}/scenes/${scene.id}`,
        };
      }), total: active.length, truncated: false };
    }) : api.getChoreItems(chore.id, campaign);
    read
      .then((items) => { if (live) setLoaded({ chore, cid, items }); })
      .catch(() => { if (live) setLoaded({ chore, cid, failed: true }); });
    return () => { live = false; };
  }, [visible, chore, cid, attempt, full]);

  const current = loaded?.chore === chore && loaded.cid === cid ? loaded : null;
  const items = current && "items" in current ? current.items : null;
  const shown = items ? (expanded || full ? items.items : items.items.slice(0, PREVIEW_SIZE)) : [];
  const panelId = `chore-items-${chore.campaign_id ?? cid ?? "library"}-${chore.id}`;
  return (
    <div className={"chore-items" + (full ? " chore-scenes" : "")} ref={element}>
      {current && "failed" in current ? (
        <p className="field-hint">Records could not be read.{" "}
          <button type="button" className="subtle" onClick={() => {
            setLoaded(null); setAttempt((n) => n + 1);
          }}>Retry records</button>
        </p>
      ) : !items ? <p className="field-hint">Loading records…</p> : (
        <>
          {items.items.length === 0 && (
            // The count and its live enumeration can move between requests.
            <p className="field-hint">{full ? "No active scenes." : "Nothing to list here."}</p>
          )}
          <ul id={panelId}>
            {shown.map((it) => (
              <li key={it.id}>
                {it.fix
                  ? <Link className="chore-item-label" to={it.fix}>{it.label}</Link>
                  : <span className="chore-item-label">{it.label}</span>}
                {it.detail && <span className="chore-item-detail">{it.detail}</span>}
              </li>
            ))}
          </ul>
          {!full && items.items.length > PREVIEW_SIZE && (
            <button type="button" className="chore-more" aria-expanded={expanded}
                    aria-controls={panelId} onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Show fewer" : `Show all ${items.items.length}`}
            </button>
          )}
          {items.truncated && (
            // The endpoint's cap is distinct from this page's short preview.
            <p className="field-hint">Showing {shown.length} of {items.total}.</p>
          )}
        </>
      )}
    </div>
  );
}

function Row({ chore, onIgnore, busy, restore, cid, showScope, showType, scopeLabel }: {
  chore: Chore; onIgnore: (chore: Chore, on: boolean) => void;
  busy: boolean; restore?: boolean; cid: string | null; showScope: boolean; showType?: boolean; scopeLabel: string;
}) {
  return (
    <li className={"chore chore-" + chore.severity}>
      <div className="chore-line">
        <div className="chore-body">
          <div className="chore-meta">
            {!restore && <span className="chore-priority">{PRIORITY_LABEL[chore.severity]}</span>}
            {showType && <span>{chore.group}</span>}
          </div>
          <h3 className="chore-what">
            {chore.what}
            {/* Inside the heading, not beside it. `taglines` and
                `world-taglines` render the same sentence — "3 characters with
                no tagline" — so without this they are two headings with one
                accessible name, which is the version of the collision a screen
                reader gets and cannot see its way around. */}
            {showScope && <span className="chore-scope">{scopeLabel}</span>}
          </h3>
          {/* The half a bare count cannot carry. A number with no consequence
              attached is a number the reader learns to skip. */}
          <div className="chore-why">{chore.why}</div>
        </div>
        <div className="chore-actions">
          {chore.fix && !restore && (
            <Link className="chore-fix" to={chore.fix}>{chore.fix_label} →</Link>
          )}
          <button type="button" className="chore-ignore" disabled={busy}
                  onClick={() => onIgnore(chore, !restore)}>
            {restore ? "Restore" : "Ignore"}
          </button>
        </div>
      </div>
      {!restore && <RecordPreview chore={chore} cid={cid} />}
    </li>
  );
}

export default function TodoView({ cid }: { cid: string | null }) {
  const [params, setParams] = useSearchParams();
  const grouping = params.get("group") === "campaign" ? "campaign" : "type";
  const askedPriority = params.get("priority");
  const priority = askedPriority === "alert" || askedPriority === "warn" || askedPriority === "note"
    ? askedPriority : "all";
  const [ignoredOpen, setIgnoredOpen] = useState(false);

  function choose(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value === "all" || value === "type") next.delete(key);
    else next.set(key, value);
    if (next.toString() !== params.toString()) setParams(next);
  }

  // Keyed on the scope: campaign A's chores are never drawn under campaign B,
  // where an Ignore would wave off the wrong campaign's. A scope visited before
  // paints its last answer at once, and nothing is ever blanked between two
  // visits of the same one.
  const { data, stale, failed: readFailed, reload } = useRevalidated<TodoPayload>(
    JSON.stringify(["todo", cid ?? ""]), () => api.rememberedTodo(cid), () => api.getTodo(cid));
  // An ignore that did not land. The list itself is still the last good read.
  const [ignoreFailed, setIgnoreFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  function retry() {
    setIgnoreFailed(false);
    void reload();
  }

  function ignore(chore: Chore, on: boolean) { void ignoreAsync(chore, on); }

  async function ignoreAsync(chore: Chore, on: boolean) {
    setBusy(true);
    setIgnoreFailed(false);
    try {
      await api.setChoreIgnored(chore.id, on, chore.campaign_id ??
        (chore.scope === "campaign" ? cid ?? undefined : undefined));
    } catch {
      setIgnoreFailed(true);
      setBusy(false);
      return;
    }
    // Re-read rather than patching in place: ignoring is not the only thing
    // that can have changed the list, and the counts are the point. Through
    // `reload`, so the visit's own read -- still on the wire, perhaps, and
    // sent before this write -- cannot land after it and paint the chore back.
    await reload();
    setBusy(false);
  }

  const allChores = [...(data?.chores ?? []), ...(data?.ignored ?? [])];
  const sceneTasks = allChores.filter((chore) => chore.id === "open-scenes");
  // Active scenes already have their own navigation list. Their old reminder
  // must not also become a type, campaign task or ignored card below it. Counts
  // here describe the remaining tasks; the server's badge still counts all
  // reminders for callers that do not render the dedicated scene list.
  const chores = (data?.chores ?? []).filter((chore) => chore.id !== "open-scenes");
  const ignored = (data?.ignored ?? []).filter((chore) => chore.id !== "open-scenes");
  const taskCount = chores.length;
  // Identity is the campaign id, never its display name. Derive names before
  // filtering, so every heading and scope chip disambiguates the same pair,
  // including the active scene list and ignored tasks.
  const campaignLabels = new Map<string, string>();
  for (const chore of allChores) {
    const campaign = chore.scope === "campaign" ? chore.campaign_id ?? cid : null;
    if (campaign) campaignLabels.set(campaign, chore.campaign_name || campaign);
  }
  const nameCounts = new Map<string, number>();
  for (const name of campaignLabels.values()) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
  for (const [campaign, name] of campaignLabels) {
    if ((nameCounts.get(name) ?? 0) > 1) campaignLabels.set(campaign, `${name} (${campaign})`);
  }
  function labelFor(chore: Chore) {
    return (chore.scope === "campaign" && chore.campaign_id ? campaignLabels.get(chore.campaign_id) : null)
      ?? scopeName(chore);
  }
  /** The headings, in reading order.
   *
   *  The server's, because it is the server that assigns each chore its theme
   *  and orders the themes; grouping them here used to quietly reorder that:
   *  the order fell out of whichever chore happened to be first in each group,
   *  so it moved with the data. The fallback is that old first-appearance
   *  order, for a payload from before `groups` existed -- which renders
   *  something reasonable rather than nothing. */
  const groups: string[] = data?.groups ?? (() => {
    const seen: string[] = [];
    for (const c of chores) if (!seen.includes(c.group)) seen.push(c.group);
    return seen;
  })();

  const filtered = chores.filter((chore) => priority === "all" || chore.severity === priority);
  // Type order is the server's. Campaign groups use ids, because equal names
  // are valid, and library work belongs to no campaign. The same sections feed
  // the column and main, so neither can index tasks the filter has hidden.
  let sections: { id: string; label: string; chores: Chore[] }[];
  if (grouping === "type") {
    sections = groups.map((group) => ({ id: group, label: group,
      chores: filtered.filter((chore) => chore.group === group) }));
  } else {
    const byCampaign = new Map<string, { id: string; label: string; chores: Chore[] }>();
    for (const chore of filtered) {
      const campaign = chore.scope === "campaign" ? chore.campaign_id ?? cid : null;
      const id = campaign ? `campaign:${campaign}` : "library";
      let section = byCampaign.get(id);
      if (!section) {
        section = { id, label: campaign ? labelFor(chore) : "Your library", chores: [] };
        byCampaign.set(id, section);
      }
      section.chores.push(chore);
    }
    sections = [...byCampaign.values()];
    sections.sort((a, b) => a.id === "library" ? 1 : b.id === "library" ? -1
      : a.label.localeCompare(b.label));
  }
  sections = sections.filter((section) => section.chores.length > 0);
  for (const section of sections) {
    section.chores.sort((a, b) => PRIORITY_ORDER[a.severity] - PRIORITY_ORDER[b.severity]);
  }

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
    allChores.map(labelFor),
  );
  const showScope = cid === null || scopeLabels.size > 1;

  const column = (<>
    <ColumnSection label="Play">
      <a className="column-row" href="#active-scenes">
        <span className="column-row-label">Active scenes</span>
      </a>
    </ColumnSection>
    <ColumnSection label={grouping === "type" ? "Types" : "Campaigns"}
                   count={data ? filtered.length : undefined}>
      {sections.map((section) => (
        <a key={section.id} className="column-row" href={`#${encodeURIComponent(section.id)}`}>
          <span className="column-row-label">{section.label}</span>
          <span className="column-count">{section.chores.length}</span>
        </a>
      ))}
      {/* No second copy of "nothing outstanding" here: main says it, and the
          section's own count already says 0. Two elements carrying one fact is
          two places for it to disagree. */}
    </ColumnSection>
    </>
  );

  return (
    <PageShell column={column} columnLabel="To do" busy={stale}
               scrollKey={`${grouping}:${priority}`}>
      <div className="page-wide todo-page view-anim">
        <div className="eyebrow">A little preparation for better play</div>
        <h1 className="screen-title">To do</h1>
        <p className="todo-intro">See what needs attention, why it matters, and where to pick it up.</p>
        <div className="todo-controls">
          <ReportScopeSelector report="todo" cid={cid}
                               suffix={params.toString() ? `?${params.toString()}` : ""} />
          <label className="todo-grouping">
            <span className="section-label">Group by</span>
            <select aria-label="Group by" value={grouping}
                    onChange={(event) => choose("group", event.target.value)}>
              <option value="type">Type</option>
              <option value="campaign">Campaign</option>
            </select>
          </label>
        </div>
        <section className="todo-active-scenes" id="active-scenes" aria-labelledby="todo-active-heading">
          <h2 className="chore-group" id="todo-active-heading">Active scenes</h2>
          {!data && !readFailed && <p className="field-hint">Loading active scenes…</p>}
          {readFailed && <p className="field-hint">Active scenes are unavailable.</p>}
          {data && !stale && sceneTasks.length === 0 && <p className="field-hint">No active scenes.</p>}
          {sceneTasks.map((chore) => (
            <div className="todo-scene-campaign" key={chore.campaign_id ?? cid ?? "library"}>
              {cid === null && <h3 className="todo-scene-campaign-name">{labelFor(chore)}</h3>}
              <RecordPreview chore={chore} cid={cid} full />
            </div>
          ))}
        </section>
        <div className="todo-priorities" role="group" aria-label="Filter by priority">
          <button type="button" aria-pressed={priority === "all"} onClick={() => choose("priority", "all")}>
            All {data && <span>{taskCount}</span>}
          </button>
          {PRIORITIES.map((level) => (
            <button key={level} type="button" className={`todo-priority-${level}`}
                    aria-pressed={priority === level} onClick={() => choose("priority", level)}>
              {PRIORITY_LABEL[level]} {data && <span>{chores.filter((c) => c.severity === level).length}</span>}
            </button>
          ))}
        </div>

        {readFailed && (
          <div className="banner error-banner">
            The list could not be read.{" "}
            <button className="subtle" onClick={retry}>Try again</button>
          </div>
        )}
        {ignoreFailed && (
          <div className="banner error-banner">That change could not be saved. Try Ignore or Restore again.</div>
        )}

        {/* "Nothing outstanding" is a statement, so it waits for this visit's
            own read; a remembered count above zero is only a count. */}
        {!readFailed && data && (taskCount > 0 || !stale) && (
          <p className="todo-summary" role="status">
            {taskCount === 0
              ? (sceneTasks.length > 0 ? "No other tasks outstanding. Anything ignored is below."
                : "Nothing outstanding. Anything ignored is below.")
              : `Showing ${filtered.length} of ${taskCount} task${taskCount === 1 ? "" : "s"}. `
                + "Ignore anything you don’t need; you can restore it later."}
            {/* Not an empty state, and deliberately not instead of the list.
                The library's own chores — an undescribed image backlog, a world
                whose cast has no taglines — answer with no campaign open, so
                there is something here to read; what is missing is the half
                about the campaign being played. Saying only "open a campaign
                first" here used to hide a list that had entries. */}
          </p>
        )}

        {taskCount > 0 && filtered.length === 0 && (
          <p className="field-hint">No tasks at this priority. Choose All to see the rest.</p>
        )}

        {sections.map((section) => (
          <section key={section.id} id={section.id} aria-labelledby={`todo-heading-${encodeURIComponent(section.id)}`}>
            <h2 className="chore-group" id={`todo-heading-${encodeURIComponent(section.id)}`}>{section.label}</h2>
            <ul className="chore-list">
              {section.chores.map((c) => (
                <Row key={`${c.campaign_id ?? cid ?? "library"}:${c.id}`} chore={c} onIgnore={ignore}
                     busy={busy || stale} cid={cid} showScope={grouping === "type" && showScope}
                     showType={grouping === "campaign"} scopeLabel={labelFor(c)} />
              ))}
            </ul>
          </section>
        ))}

        {ignored.length > 0 && (
          <section className="todo-ignored">
            <button type="button" className="todo-ignored-toggle" aria-expanded={ignoredOpen}
                    aria-controls="todo-ignored-list" onClick={() => setIgnoredOpen((v) => !v)}>
              <span aria-hidden>{ignoredOpen ? "▾" : "▸"}</span> Ignored ({ignored.length})
            </button>
            <p className="field-hint">Set aside, with no effect on your task count. Restore one whenever you need it.</p>
            {ignoredOpen && <ul className="chore-list" id="todo-ignored-list">
              {ignored.map((c) => (
                <Row key={`${c.campaign_id ?? cid ?? "library"}:${c.id}`} chore={c} onIgnore={ignore}
                     busy={busy || stale} restore cid={cid} showScope={showScope} showType scopeLabel={labelFor(c)} />
              ))}
            </ul>}
          </section>
        )}
      </div>
    </PageShell>
  );
}
