import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { errorText } from "../api/errors";
import { forkNotes } from "../components/forkNotes";
import { lineage } from "./campaignLineage";
import { byName } from "../sortByName";

/** How far a fork of `depth` generations is indented, in pixels.
 *
 *  Indented rather than boxed: the shelf is a list of books, and a nested card
 *  would make a branch look like a different kind of thing than the campaign it
 *  came from. Capped, because nothing bounds how deep a chain of forks goes and
 *  an uncapped indent walks the card off the right of the page — past the cap a
 *  fork is still visibly a fork by its chip, which is what the depth was
 *  conveying anyway. */
const INDENT_STEP = 28;
const MAX_INDENT_DEPTH = 4;
const indentOf = (depth: number) => Math.min(depth, MAX_INDENT_DEPTH) * INDENT_STEP;

/** "2 days ago", from a stamp the store wrote. Coarse on purpose: a campaign
 *  shelf wants "recently" or "a while back", and a to-the-minute figure on a
 *  card you look at once a week is precision nobody asked for. */
function ago(stamp: string | undefined): string {
  if (!stamp) return "never played";
  const then = Date.parse(stamp.replace(" ", "T"));
  if (Number.isNaN(then)) return "never played";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "last played today";
  if (days === 1) return "last played yesterday";
  if (days < 30) return `last played ${days} days ago`;
  const months = Math.floor(days / 30);
  return months === 1 ? "last played a month ago" : `last played ${months} months ago`;
}

/** How the shelf is ordered. Remembered across visits, and NOT keyed by store
 *  root the way `useOpenCampaign` keys the id it remembers: that is a
 *  reference to a record inside one library and means nothing in another,
 *  where this is a preference about how a page reads and travels with the
 *  reader.
 *
 *  A-Z is the default. Recency is what the shelf was built on and it is still
 *  a click away, but it answers "what was I playing" - a question the rail now
 *  answers on every page, and one the `ago(...)` line on each card answers
 *  again. Nothing answered "where is the campaign called X" except reading
 *  every card.
 *
 *  Anything unrecognised in storage reads as A-Z rather than throwing: this is
 *  a value another version of the app may have written, and a shelf that
 *  refuses to render over it would be worse than one that ignores it.
 */
type Sort = "name" | "played";
const SORT_KEY = "grimoire.sort.campaigns";

function loadSort(): Sort {
  // Storage throws rather than returning null in a locked-down WebView, the
  // same bargain `useOpenCampaign` and `focus.tsx` make.
  try { return localStorage.getItem(SORT_KEY) === "played" ? "played" : "name"; }
  catch { return "name"; }
}

function saveSort(next: Sort): void {
  try { localStorage.setItem(SORT_KEY, next); } catch { /* see loadSort() */ }
}

/** What the toggle prints for a given order. Uppercase because the control
 *  wears `.data-label`, which small-caps everything it holds anyway - spelling
 *  it out here keeps the two states the same width in the source as on screen. */
const sortLabel = (s: Sort) => (s === "name" ? "A–Z" : "LAST PLAYED");

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  /** What the last fork left behind that the new campaign cannot show: records
   *  a removed scene's absorb wrote that could not be put back, and cleanup
   *  that could not run. Shown once, above the shelf, because a fork from an
   *  earlier turn is an approximation and saying nothing would let the branch
   *  read as the past exactly. */
  const [forkNote, setForkNote] = useState("");
  // Keyed by id AND version, not id alone: a cover that failed to load must
  // not keep its replacement hidden after the next listCampaigns() refresh.
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  /** "" is All worlds. The column's worlds are a filter over the shelf, not a
   *  navigation away from it — picking one must not cost you the page. */
  const [world, setWorld] = useState("");
  const [sort, setSort] = useState<Sort>(loadSort);

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then(setWorlds);
  }, []);

  // `activity` folds in the newest scene; `updated` alone only moves on
  // metadata writes, so ordering by it ranks a campaign renamed months ago
  // above one played into last night. Copy first — sort mutates, and this
  // array is state.
  //
  // `||` rather than `??` on both halves: `read_activity` answers "" for a
  // campaign whose stamp is missing or unreadable, and an empty string is a
  // value, so `??` would keep it and sort a perfectly good `updated` to the
  // bottom. The trailing "" is for the campaign.md that carries neither.
  const stamp = (c: CampaignMeta) => c.activity || c.updated || "";
  const ranked = useMemo(
    () => (sort === "name"
      ? byName(campaigns)
      : [...campaigns].sort((a, b) => stamp(b).localeCompare(stamp(a)))),
    [campaigns, sort]);
  // Memoized, not because filtering is expensive but because `rows` below is
  // keyed on this array's identity: `ranked.filter(...)` returns a fresh array
  // every render, so a bare expression here would make that memo miss on every
  // render while looking like it did not.
  const shown = useMemo(
    () => (world ? ranked.filter((c) => c.world === world) : ranked), [ranked, world]);
  // The one you are most likely to have meant. It gets the border and the
  // glow -- and only those. The rename/fork/delete strip used to hang off this
  // id as well, on the reasoning that a ✕ on every card is a ✕ you can hit by
  // accident on the wrong campaign. What that actually bought was a shelf
  // where deleting a campaign meant playing it first, since the controls
  // followed the stamps and nothing else. The accident guard is the confirm in
  // `remove`, which names the campaign; this marks which one you meant, not
  // which one you may act on.
  //
  // Computed from the stamps rather than read off row zero, which is what it
  // used to be. Row zero only meant "most recently played" while the shelf was
  // sorted that way, and under A-Z it means "alphabetically first" - which
  // would have put the destructive controls on a campaign chosen by spelling.
  // Asking the stamps directly is what makes the sort a presentation choice
  // instead of a change to what the page thinks you meant.
  //
  // Taken from `shown` rather than from all campaigns, so the world filter
  // moves the glow to the filtered set's own most recent - and from `shown`
  // rather than the tree below, which groups a fork under its parent and so
  // starts with a root rather than with anything about recency.
  const activeId = useMemo(
    () => shown.reduce<CampaignMeta | null>(
      (best, c) => (!best || stamp(c).localeCompare(stamp(best)) > 0 ? c : best),
      null)?.id,
    [shown]);
  // Forks nested under the campaign they came from, roots and siblings each
  // still in whatever order `shown` is in (`campaignLineage`). So the sort
  // orders each generation rather than the page: a fork called "Ashfall" sits
  // under a parent called "Winterlight" even under A-Z, because the tree is
  // answering "where did this come from" and the sort is only answering
  // "where in its family does it sit". Derived from `shown`, so the world
  // column filters the family and not just the rows.
  const rows = useMemo(() => lineage(shown), [shown]);
  const nameOf = (id: string) => campaigns.find((c) => c.id === id)?.name ?? "";

  /** The filter column's worlds, A-Z. Same order as the Worlds grid itself, so
   *  a world sits in the same place on both pages. */
  const worldRows = useMemo(() => byName(worlds), [worlds]);

  const worldName = (id: string) => worlds.find((w) => w.id === id)?.name ?? id;
  const countIn = (id: string) => campaigns.filter((c) => c.world === id).length;

  function toggleSort() {
    const next: Sort = sort === "name" ? "played" : "name";
    setSort(next);
    saveSort(next);
  }

  async function rename() {
    if (!renaming) return;
    await api.renameCampaign(renaming.id, renaming.name);
    setRenaming(null);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  /** Fork a campaign from where it stands. Cutting one back to an earlier
   *  scene is offered on the campaign's own page, where the scene you would
   *  cut at is the one you are reading — asking for a scene id here would mean
   *  a picker over a campaign this page never opens.
   *
   *  The list is refreshed rather than navigated away from: the fork appears
   *  under its parent, which is the point of the tree. */
  async function forkFromNow(c: CampaignMeta) {
    const name = window.prompt(`Fork '${c.name}' as?`, `${c.name} (fork)`)?.trim();
    if (!name) return;
    let report;
    try {
      report = await api.forkCampaign(c.id, name);
    } catch (err) {
      // Reported, not dropped. `rename` and `remove` above let a rejection
      // become an unhandled promise — the row simply does not change, which
      // reads as "nothing happened" and is nearly true for them. It is not true
      // here: a fork holds two campaign locks, so 409 CAMPAIGN BUSY is a
      // reachable answer for a campaign being played in another window, and a
      // silent one would look identical to a fork that worked. The campaign
      // page reports the same failure the same way.
      setForkNote(`'${c.name}' could not be forked: ${errorText(err)}`);
      return;
    }
    setForkNote(forkNotes(report));
    setCampaigns(await api.listCampaigns());
  }

  const column = (
    <>
      <button className="column-primary" onClick={() => navigate("/campaigns/new")}
              disabled={worlds.length === 0}>
        ＋ New campaign
      </button>
      <ColumnSection label="Worlds" count={worlds.length}>
        {worldRows.map((w) => (
          <button key={w.id}
                  className={"column-row" + (world === w.id ? " active" : "")}
                  onClick={() => setWorld(w.id)}>
            <span className="column-row-label">{w.name}</span>
            <span className="column-row-count">{countIn(w.id)}</span>
          </button>
        ))}
        <button className={"column-row" + (world === "" ? " active" : "")}
                onClick={() => setWorld("")}>
          <span className="column-row-label">All worlds</span>
          <span className="column-row-count" aria-hidden>→</span>
        </button>
      </ColumnSection>
    </>
  );

  const footer = (
    <>
      <Link className="column-link" to="/worlds">The Library <span aria-hidden>→</span></Link>
      <Link className="column-link" to="/connections">Connections <span aria-hidden>→</span></Link>
    </>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Worlds">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow">
              {world ? `${worldName(world).toUpperCase()} · ` : ""}
              {shown.length} {shown.length === 1 ? "CAMPAIGN" : "CAMPAIGNS"}
            </div>
            <h1 className="screen-title">Campaigns</h1>
          </div>
          <div className="shelf-head-meta">
            {/* The label this replaced said "SORT · LAST PLAYED ▾" and did
                nothing — a control that looks like a control and is not is
                worse than no control at all. Two states, so it cycles on click
                rather than opening a menu over a choice of two. */}
            <button type="button" className="data-label sort-toggle" onClick={toggleSort}
                    aria-label={sort === "name" ? "Sort by last played" : "Sort by name"}>
              SORT · {sortLabel(sort)} ▾
            </button>
          </div>
        </div>

        {worlds.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">No worlds yet.</span> A campaign is played
            inside one, so that is the first thing to make.{" "}
            <Link to="/worlds">Create a world →</Link>
          </p>
        )}
        {worlds.length > 0 && shown.length === 0 && (
          <p className="empty-state">
            <span className="empty-what">
              {world ? `No campaigns in ${worldName(world)} yet.` : "No campaigns yet."}
            </span>{" "}
            <Link to="/campaigns/new">Start one →</Link>
          </p>
        )}

        {forkNote && (
          <p className="banner" role="status">
            {forkNote}
            <button className="chip-clear" aria-label="Dismiss fork note"
                    onClick={() => setForkNote("")}>✕</button>
          </p>
        )}

        <div className="shelf">
          {rows.map(({ item: c, depth }) => (
            <article className={"campaign-card" + (c.id === activeId ? " active" : "")
                                + (depth ? " forked" : "")} key={c.id}
                     style={depth ? { marginLeft: `${indentOf(depth)}px` } : undefined}>
              <div className="shelf-cover">
                {c.cover && !broken[`${c.id}:${c.cover}`] ? (
                  // w=208 for a box index.css sizes at 104px wide: 2x of
                  // headroom, so the cover is still sharp on a 2x display
                  // rather than upscaled. More than that only costs bytes.
                  <img src={api.campaignCoverUrl(c.id, { w: 208, v: c.cover })}
                       alt={`${c.name} cover`}
                       onError={() => setBroken((b) => ({ ...b, [`${c.id}:${c.cover}`]: true }))} />
                ) : (
                  <span className="cover-empty" aria-hidden>◆</span>
                )}
              </div>

              <div className="campaign-body">
                {renaming?.id === c.id ? (
                  <input
                    className="row-rename" aria-label="Rename campaign" autoFocus
                    value={renaming.name}
                    onChange={(e) => setRenaming({ id: c.id, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") rename();
                      if (e.key === "Escape") setRenaming(null);
                    }}
                  />
                ) : (
                  <h2 className="campaign-name">
                    <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
                  </h2>
                )}
                {/* Absorbed over total, because the two are a different
                    question and the gap between them is the campaign's state:
                    playing ahead of the absorb is normal, and the fraction is
                    where that shows. */}
                <div className="campaign-meta">
                  {c.absorbed}/{c.scenes} {c.scenes === 1 ? "scene" : "scenes"}
                  {" · "}{ago(stamp(c))}
                </div>
                {c.blurb && <p className="campaign-blurb">{c.blurb}</p>}
                <div className="chip-row">
                  <span className="chip">{worldName(c.world).toUpperCase()}</span>
                  {c.module && <span className="chip">{c.module.toUpperCase()}</span>}
                  {/* The chip is shown for every fork, indented or not: a fork
                      whose parent has been deleted or filtered out sits at the
                      top level, and the only place its lineage can be read is
                      here. `nameOf` answers "" for a parent that is gone, so
                      the id stands in: a slug still says more than nothing. */}
                  {c.parent && (
                    <span className="chip on">
                      FORKED FROM {(nameOf(c.parent) || c.parent).toUpperCase()}
                      {c.forked_from_scene ? " · AT AN EARLIER SCENE" : ""}
                    </span>
                  )}
                </div>
              </div>

              <div className="campaign-actions">
                <Link className="btn-accent continue" to={`/campaigns/${c.id}`}>
                  {c.last_scene ? "Continue" : "Open"} <span aria-hidden>→</span>
                </Link>
                {c.last_scene && <div className="campaign-last">{c.last_scene}</div>}
                <div className="row-actions">
                  <button aria-label={`Rename ${c.name}`}
                          onClick={() => setRenaming({ id: c.id, name: c.name })}>✎</button>
                  {/* Fork sits with the other two because it is the third thing
                      you do to a campaign from outside it. Forking from here
                      branches at the campaign as it stands; forking at a chosen
                      turn is on the campaign's own page. */}
                  <button aria-label={`Fork ${c.name}`} title="Fork this campaign"
                          onClick={() => forkFromNow(c)}>⑂</button>
                  <button aria-label={`Delete ${c.name}`} onClick={() => remove(c)}>✕</button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </PageShell>
  );
}
