import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { SceneImport } from "../components/SceneImport";
import { api, type CampaignMeta, type CampaignSceneCosts,
         type SceneMeta } from "../api/client";
import type { ShellPayload } from "../api/types";
import { PageShell, ColumnSection } from "../components/PageShell";
import { errorText } from "../api/errors";
import { bucketPrice, UNPRICED } from "../components/cost";
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
 *  Most of a row comes off scene frontmatter, which is what `list_scenes`
 *  already reads. Two columns cannot: **turns** is in the transcript and
 *  **spend** is in the ledger, and reading either per row on the way into this
 *  page would make opening it cost more than playing a turn. So neither is
 *  waited for. Turns arrive for the OPEN scenes only, off `GET /api/shell`,
 *  which already counts them and bounds that cost by how many are open rather
 *  than by the campaign's length. Spend arrives in a second effect, after the
 *  list is on screen, and a row simply has no figure until it does.
 *
 *  A column that has not arrived renders as nothing, never as `0` or `$0.00` —
 *  the cost rule, and the same sentence the rail's tails are built on.
 */

type Filter = "all" | "open" | "absorbed";

/** What to do with this scene, and what to call it.
 *
 *  Four states, four verbs, and each pair that looks alike is the pair worth
 *  keeping apart. `unreviewed` is not `absorbed`: a scene whose proposals
 *  nobody decided is not finished, and calling it "Read" would file it away
 *  with the ones that are. And **Resume is not Open**: a scene with turns in
 *  it is a conversation you are in the middle of, while one with none has not
 *  started — the same click, but the reader knows which they are about to do.
 *
 *  `turns` is only known for open scenes (see `turns` in the view). An open
 *  scene nobody could count reads as "Open", which is the safer of the two
 *  wordings: it never claims there is something to come back to.
 */
function actionFor(s: SceneMeta, waiting: Map<string, number>,
                   turns: Map<string, number>) {
  if (waiting.has(s.id)) return { label: "Wrap up →", tone: "alert" };
  if (s.done) return { label: "Read →", tone: "quiet" };
  return { label: (turns.get(s.id) ?? 0) > 0 ? "Resume →" : "Open →",
           tone: "accent" };
}

export default function ScenesView({ ready = true }: { ready?: boolean }) {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const [choosing, setChoosing] = useState(false);
  const [importing, setImporting] = useState(false);
  /** Per-scene spend, or null until it lands. A second effect on purpose: the
   *  read behind it scans the ledger's whole history, which is the right cost
   *  for the Costs page and the wrong one to put in front of a list. */
  const [costs, setCosts] = useState<CampaignSceneCosts | null>(null);
  const [meta, setMeta] = useState<CampaignMeta | null>(null);
  const [scenes, setScenes] = useState<SceneMeta[] | null>(null);
  const [shell, setShell] = useState<ShellPayload | null>(null);
  const [failed, setFailed] = useState(false);
  /** Why the last delete did not happen, or null. Separate from `failed`,
   *  which means the LIST could not be read: one says "look again", the other
   *  says "that scene is busy" over a list that is perfectly fine. */
  const [delFailed, setDelFailed] = useState<string | null>(null);
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

  // What each scene cost, fetched after the list rather than with it, and
  // dropped silently on failure: a row with no figure is the honest rendering
  // of "not counted", and a banner over a list of scenes because the ledger
  // was busy would be reporting the wrong thing as broken.
  useEffect(() => {
    if (!cid) return;
    let live = true;
    setCosts(null);
    api.getCampaignSceneCosts(cid, "recent")
      .then((c) => { if (live) setCosts(c); })
      .catch(() => {});
    return () => { live = false; };
  }, [cid]);

  // Which scenes are holding a review, and how many proposals each is
  // holding — a map rather than a set, so the chip can say "how many" instead
  // of just "some". It comes from the same payload the rail and the hub read
  // — so all three agree about what is waiting.
  const waiting = useMemo(
    () => new Map((shell?.campaign?.pending ?? []).map((p) => [p.sid, p.proposals])),
    [shell]);

  /** How many model replies each OPEN scene holds.
   *
   *  Only the open ones, because that is all `/api/shell` counts — and that
   *  bound is the reason it is affordable to count at all. An absorbed scene
   *  is finished, so "how far in is it" is not a question its row has to
   *  answer; `undefined` here means nobody counted, which the row draws as
   *  nothing rather than as zero turns. */
  const turns = useMemo(
    () => new Map((shell?.campaign?.open ?? [])
      .flatMap((o) => (o.turns === null ? [] : [[o.sid, o.turns] as const]))),
    [shell]);

  /** What each scene cost, keyed by scene id. */
  const spend = useMemo(
    () => new Map((costs?.scenes ?? []).map((r) => [r.scene, r] as const)),
    [costs]);

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

  /** Delete a scene from the list.
   *
   *  The play page deletes the scene you are reading and can disable its own
   *  button while a turn runs, because it knows. This list does not: a run
   *  holding a scene is state it never reads. So the 409 `scene_busy` is
   *  shown rather than guarded against, and the row stays where it was.
   *
   *  Re-read rather than spliced out of `scenes`: deleting cascades into the
   *  absorbed count in the eyebrow and the shell's open/pending sets, and a
   *  local splice would leave both describing a campaign that no longer
   *  exists. */
  async function remove(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'? This cannot be undone.`)) return;
    setDelFailed(null);
    try {
      await api.deleteScene(cid, s.id);
    } catch (err) {
      setDelFailed(`'${s.title}' was not deleted: ${errorText(err)}`);
      return;
    }
    const [sc, sh] = await Promise.all([api.listScenes(cid), api.getShell(cid)]);
    setScenes(sc); setShell(sh);
  }

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
          <div className="scenes-head-actions">
            {/* Importing a transcript makes a scene, so it belongs beside the
                other way of making one rather than a click deeper inside the
                picker -- which is where it was, and which is a strange place
                to look for it when what you have is a file. */}
            <button type="button" className="subtle"
                    onClick={() => setImporting(true)}>Import a transcript</button>
            {/* Creation lives here, not on the transcript. The play view is
                always inside a scene now, so an empty campaign has no composer
                to type the first one into -- this is where a campaign starts. */}
            <button type="button" className="hub-primary"
                    onClick={() => setChoosing(true)}>+ New scene</button>
          </div>
        </div>

        {importing && (
          <div className="scenes-import">
            <SceneImport cid={cid}
                         onBack={() => setImporting(false)}
                         onCancel={() => setImporting(false)}
                         onImported={(sid) => {
                           setImporting(false);
                           navigate(`/campaigns/${cid}/scenes/${sid}`);
                         }} />
          </div>
        )}

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
            // The premise rides the navigation. Unlike the in-campaign chooser,
            // this one creates a scene on a page with no opener box to hand it
            // to -- the box belongs to the route we are about to land on -- so
            // dropping the second argument here left the reader on a scene
            // whose box was empty, holding a premise they had just approved two
            // panes ago. History state, because the handoff has to survive a
            // route change; `CampaignView` adopts it once and clears it.
            onCreated={(sid, initialPrompt) => {
              setChoosing(false);
              navigate(`/campaigns/${cid}/scenes/${sid}`,
                       initialPrompt ? { state: { seedPrompt: initialPrompt } } : undefined);
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

        {delFailed && (
          <div className="banner error-banner">
            {delFailed}{" "}
            <button className="subtle" onClick={() => setDelFailed(null)}>Dismiss</button>
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
            const act = actionFor(s, waiting, turns);
            const n = sceneNumber(s.id);
            const t = turns.get(s.id);
            const row = spend.get(s.id);
            // `bucketPrice` rather than a bare figure, so a scene whose calls
            // nobody priced reads "not reported" instead of `$0.00` -- and a
            // scene the ledger has no rows for at all draws nothing, because
            // "never generated against" is not "cost nothing".
            const price = row ? bucketPrice(row) : null;
            return (
              <li key={s.id} className={"scene-item" + (s.done ? "" : " open")}>
                {/* Six cells, each fact written ONCE. At full width they are
                    a grid and the list reads down a column; below the
                    breakpoint the same nodes reflow into a mono line under the
                    title. Rendering a fact twice and hiding one copy per width
                    is the other way to do this, and it puts every figure on
                    the page twice for a screen reader. */}
                <Link className="scene-item-main"
                      to={waiting.has(s.id)
                        ? `/campaigns/${cid}/scenes/${s.id}/wrap-up`
                        : `/campaigns/${cid}/scenes/${s.id}`}>
                  <span className="scene-item-n">{n ?? "—"}</span>
                  <span className="scene-item-title">{s.title}</span>
                </Link>
                <span className={"chip" + (s.done ? "" : " on")}>
                  {waiting.has(s.id)
                    ? `${waiting.get(s.id)} unreviewed`
                    : s.done ? "absorbed" : "open"}
                </span>
                <span className="scene-item-when">
                  {[s.date, s.place, s.pcless ? "no PC" : null]
                    .filter(Boolean).join(" · ")}
                </span>
                {/* Turns and spend: neither is frontmatter, so either can be
                    absent, and an absent one draws nothing rather than `0` or
                    `$0.00`. `:empty` collapses the cell, so a column of prices
                    never has a blank in it that reads as a zero. */}
                <span className="scene-item-turns"
                      title={t === undefined ? undefined
                             : `${t} ${t === 1 ? "turn" : "turns"}`}>
                  {t === undefined ? "" : `${t}t`}
                </span>
                <span className={"scene-item-spend"
                                 + (price === UNPRICED ? " money-unpriced" : "")}>
                  {price ?? ""}
                </span>
                <Link className={"scene-item-act " + act.tone}
                      to={waiting.has(s.id)
                        ? `/campaigns/${cid}/scenes/${s.id}/wrap-up`
                        : `/campaigns/${cid}/scenes/${s.id}`}>{act.label}</Link>
                {/* Outside the link, not inside it: a button nested in an
                    anchor is invalid, and on a touch screen the anchor wins
                    the tap often enough to open the scene you meant to
                    delete. Named for its scene -- three ✕ reading "Delete"
                    are three controls a screen reader cannot tell apart. */}
                <button className="scene-item-del" aria-label={`Delete ${s.title}`}
                        title="Delete this scene"
                        onClick={() => { void remove(s); }}>✕</button>
              </li>
            );
          })}
        </ol>
      </div>
    </PageShell>
  );
}
