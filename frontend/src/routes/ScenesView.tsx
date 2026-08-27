import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { api, type CampaignMeta, type SceneMeta } from "../api/client";
import type { ShellPayload } from "../api/types";
import { PageShell, ColumnSection } from "../components/PageShell";
import { usePublishShellContext } from "../components/ShellStatus";
import { sceneNumber } from "./sceneNumber";

/** Every scene in the campaign, newest first.
 *
 *  The rail says how many there are and the hub shows the last five; this is
 *  the list you read top to bottom. Its one real job is the **state-dependent
 *  action**: a scene mid-play, a scene opened but never played, a scene
 *  absorbed but not reviewed and a scene finished are four different things to
 *  do next, and offering the same "Open" for all of them makes the reader work
 *  out which is which from the chips.
 *
 *  Everything on a row comes off scene frontmatter, which is what
 *  `list_scenes` already reads. Turn counts and per-scene spend are on the
 *  design's row and are deliberately absent here: neither is in frontmatter,
 *  and reading a transcript or the ledger per row would make opening this page
 *  cost more than playing a turn. They arrive with the slice that gives them a
 *  cheap source, and until then the row says less rather than guessing.
 */

type Filter = "all" | "open" | "absorbed";

/** What to do with this scene, and what to call it.
 *
 *  Three states, three verbs. `unreviewed` is the one worth keeping separate:
 *  an absorbed scene whose proposals nobody decided is not finished, and
 *  calling it "Read" would file it away with the ones that are.
 */
function actionFor(s: SceneMeta, waiting: Map<string, number>) {
  if (waiting.has(s.id)) return { label: "Wrap up →", tone: "alert" };
  if (s.done) return { label: "Read →", tone: "quiet" };
  return { label: "Open →", tone: "accent" };
}

export default function ScenesView({ ready = true }: { ready?: boolean }) {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const [choosing, setChoosing] = useState(false);
  const [meta, setMeta] = useState<CampaignMeta | null>(null);
  const [scenes, setScenes] = useState<SceneMeta[] | null>(null);
  const [shell, setShell] = useState<ShellPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");

  usePublishShellContext(meta ? { campaign: meta.name, scene: "" } : null);

  useEffect(() => {
    if (!cid) return;
    let live = true;
    setFailed(false);
    Promise.all([
      api.getCampaign(cid).then((r) => r.meta),
      api.listScenes(cid),
      api.getShell(cid),
    ]).then(([m, sc, sh]) => {
      if (!live) return;
      setMeta(m); setScenes(sc); setShell(sh);
    }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [cid]);

  // Which scenes are holding a review, and how many proposals each is
  // holding — a map rather than a set, so the chip can say "how many" instead
  // of just "some". It comes from the same payload the rail and the hub read
  // — so all three agree about what is waiting.
  const waiting = useMemo(
    () => new Map((shell?.campaign?.pending ?? []).map((p) => [p.sid, p.proposals])),
    [shell]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (scenes ?? []).filter((s) => {
      if (filter === "open" && s.done) return false;
      if (filter === "absorbed" && !s.done) return false;
      if (needle && !s.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [scenes, filter, q]);

  const counts = useMemo(() => ({
    all: scenes?.length ?? 0,
    open: (scenes ?? []).filter((s) => !s.done).length,
    absorbed: (scenes ?? []).filter((s) => s.done).length,
  }), [scenes]);

  const column = (
    <>
      <ColumnSection label="Show">
        {([["all", "All scenes"], ["open", "Open"], ["absorbed", "Absorbed"]] as const)
          .map(([id, label]) => (
            <button key={id} type="button"
                    className={"column-row" + (filter === id ? " active" : "")}
                    onClick={() => setFilter(id)}>
              <span className="column-row-label">{label}</span>
              <span className="column-count">{counts[id]}</span>
            </button>
          ))}
      </ColumnSection>
      <ColumnSection label="This campaign">
        <Link className="column-row" to={`/campaigns/${cid}`}>Overview</Link>
        <Link className="column-row" to={`/campaigns/${cid}/ledger`}>Ledger &amp; timeline</Link>
        <Link className="column-row" to={`/campaigns/${cid}/costs`}>Costs</Link>
      </ColumnSection>
    </>
  );

  return (
    <PageShell column={column} columnLabel="Scenes">
      <div className="page-wide view-anim">
        <div className="eyebrow">
          {[meta?.name, "every scene, newest first", counts.open ? `${counts.open} open` : null]
            .filter(Boolean).join(" · ")}
        </div>
        <div className="scenes-head">
          <h1 className="screen-title">Scenes</h1>
          {/* Creation lives here, not on the transcript. The play view is
              always inside a scene now, so an empty campaign has no composer
              to type the first one into -- this is where a campaign starts. */}
          <button type="button" className="hub-primary"
                  onClick={() => setChoosing(true)}>+ New scene</button>
        </div>

        {choosing && (
          <NewSceneChooser
            cid={cid} ready={ready}
            // Ranking reference: the newest scene, or none in a fresh campaign.
            afterSid={scenes?.[0]?.id ?? null}
            onClose={(createdSid) => {
              setChoosing(false);
              // A scene salvaged from a soft failure still exists, so the list
              // has to learn about it even though the reader backed out.
              if (createdSid) api.listScenes(cid).then(setScenes).catch(() => {});
            }}
            onCreated={(sid) => {
              setChoosing(false);
              navigate(`/campaigns/${cid}/scenes/${sid}`);
            }} />
        )}

        {failed && (
          // A failed read is not an empty campaign. The two must never render
          // the same way: one means "look again", the other "start writing".
          <div className="banner error-banner">
            The scenes could not be read.{" "}
            <button className="subtle" onClick={() => setFailed(false)}>Try again</button>
          </div>
        )}

        {!failed && (
          <div className="scenes-tools">
            <input className="rail-search" type="search" value={q}
                   placeholder="Filter by title…" aria-label="Filter scenes by title"
                   onChange={(e) => setQ(e.target.value)} />
            <span className="field-hint">
              {shown.length === counts.all
                ? `${counts.all} ${counts.all === 1 ? "scene" : "scenes"}`
                : `${shown.length} of ${counts.all}`}
            </span>
          </div>
        )}

        {!failed && scenes !== null && !scenes.length && (
          <p className="empty-state">
            No scenes yet. The campaign starts with the first one.
          </p>
        )}

        {!failed && scenes !== null && scenes.length > 0 && !shown.length && (
          // A filter that matches nothing is not an empty campaign either.
          <p className="empty-state">No scene here matches that.</p>
        )}

        <ol className="scene-list">
          {shown.map((s) => {
            const act = actionFor(s, waiting);
            const n = sceneNumber(s.id);
            return (
              <li key={s.id} className={"scene-item" + (s.done ? "" : " open")}>
                <Link className="scene-item-main" to={`/campaigns/${cid}/scenes/${s.id}`}>
                  <span className="scene-item-n">{n ?? "—"}</span>
                  <span className="scene-item-title">{s.title}</span>
                  {/* Below the breakpoint the secondary columns fold into this
                      one mono line rather than being clipped: the title and the
                      action are what the row is for, and they never give way. */}
                  <span className="scene-item-sub">
                    {[s.date, s.place, s.pcless ? "no PC" : null]
                      .filter(Boolean).join(" · ")}
                  </span>
                </Link>
                <span className={"chip" + (s.done ? "" : " on")}>
                  {waiting.has(s.id)
                    ? `${waiting.get(s.id)} unreviewed`
                    : s.done ? "absorbed" : "open"}
                </span>
                <Link className={"scene-item-act " + act.tone}
                      to={`/campaigns/${cid}/scenes/${s.id}`}>{act.label}</Link>
              </li>
            );
          })}
        </ol>
      </div>
    </PageShell>
  );
}
