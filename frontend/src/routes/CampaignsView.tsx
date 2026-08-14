import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";

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

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  // Keyed by id AND version, not id alone: a cover that failed to load must
  // not keep its replacement hidden after the next listCampaigns() refresh.
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  /** "" is All worlds. The column's worlds are a filter over the shelf, not a
   *  navigation away from it — picking one must not cost you the page. */
  const [world, setWorld] = useState("");

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
  const ranked = useMemo(() => [...campaigns].sort((a, b) => stamp(b).localeCompare(stamp(a))),
    [campaigns]);
  const shown = world ? ranked.filter((c) => c.world === world) : ranked;
  // The one you are most likely to have meant. It gets the border, the glow
  // and the only rename/delete controls on the page: those are rare, and a ✕
  // on every card is a ✕ you can hit by accident on the wrong campaign.
  const activeId = shown[0]?.id;

  const worldName = (id: string) => worlds.find((w) => w.id === id)?.name ?? id;
  const countIn = (id: string) => campaigns.filter((c) => c.world === id).length;

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

  const column = (
    <>
      <button className="column-primary" onClick={() => navigate("/campaigns/new")}
              disabled={worlds.length === 0}>
        ＋ New campaign
      </button>
      <ColumnSection label="Worlds" count={worlds.length}>
        {worlds.map((w) => (
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
            <span className="data-label">SORT · LAST PLAYED ▾</span>
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

        <div className="shelf">
          {shown.map((c) => (
            <article className={"campaign-card" + (c.id === activeId ? " active" : "")} key={c.id}>
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
                <div className="campaign-meta">
                  {c.scenes} {c.scenes === 1 ? "scene" : "scenes"} · {ago(stamp(c))}
                  {" · "}
                  {c.absorbed_through
                    ? `absorbed through ${c.absorbed_through}`
                    : "not yet absorbed"}
                </div>
                {c.blurb && <p className="campaign-blurb">{c.blurb}</p>}
                <div className="chip-row">
                  <span className="chip">{worldName(c.world).toUpperCase()}</span>
                  {c.module && <span className="chip">{c.module.toUpperCase()}</span>}
                </div>
              </div>

              <div className="campaign-actions">
                <Link className="btn-accent continue" to={`/campaigns/${c.id}`}>
                  {c.last_scene ? "Continue" : "Open"} <span aria-hidden>→</span>
                </Link>
                {c.last_scene && <div className="campaign-last">{c.last_scene}</div>}
                {c.id === activeId && (
                  <div className="row-actions">
                    <button aria-label={`Rename ${c.name}`}
                            onClick={() => setRenaming({ id: c.id, name: c.name })}>✎</button>
                    <button aria-label={`Delete ${c.name}`} onClick={() => remove(c)}>✕</button>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
    </PageShell>
  );
}
