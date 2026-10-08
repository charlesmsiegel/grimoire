import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { NewSceneChooser } from "../components/NewSceneChooser";
import { sanitizeSeed, type ChooserSeed } from "../components/pressureControls";
import { SceneImport } from "../components/SceneImport";
import { api, type CampaignMeta, type CampaignSceneCosts,
         type SceneMeta } from "../api/client";
import { PageShell, ColumnSection } from "../components/PageShell";
import { errorText } from "../api/errors";
import { bucketPrice, estimatedTokensTitle, UNPRICED } from "../components/cost";
import { usePublishShellContext } from "../components/ShellStatus";
import { useCampaignShell } from "../shell/ShellPayloadContext";
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
 *  waited for. Turns arrive for the OPEN scenes only, off the rail's
 *  `GET /api/shell` (drawn from `ShellPayloadContext`, not read again here),
 *  which already counts them and bounds that cost by how many are open rather
 *  than by the campaign's length. Which scenes hold a review rides the same
 *  payload. Spend arrives in a second effect, after the list is on screen, and
 *  a row simply has no figure until it does.
 *
 *  None of them is waited for, the shell included: it used to sit in the same
 *  `Promise.all` as the list and hold every row until the slowest read in the
 *  chrome had answered. Until it does, a row says what its frontmatter knows --
 *  "open" or "absorbed", "Open →" or "Read →" -- and gains its review chip,
 *  its wrap-up link and its turn count when the payload for this campaign
 *  lands.
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
  // A closed branch is read-only: a sibling was absorbed, so there is nothing
  // here to open or resume.
  if (s.done || s.closed_by) return { label: "Read →", tone: "quiet" };
  return { label: (turns.get(s.id) ?? 0) > 0 ? "Resume →" : "Open →",
           tone: "accent" };
}

export default function ScenesView({ ready = true }: { ready?: boolean }) {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [choosing, setChoosing] = useState(false);
  /** A Story Graph handoff's seed for the chooser, or null for an ordinary
   *  open. Cleared when the chooser closes, so `+ New scene` never inherits
   *  steering the reader has already walked away from. */
  const [seed, setSeed] = useState<ChooserSeed | null>(null);
  /** A handoff's seed is waiting for the scene list before it opens the
   *  chooser (Decision 20). One-way: it is released once -- by the list
   *  landing or failing -- and nothing sets it again, so clearing an error
   *  banner can never re-hold a chooser the reader is already using. */
  const [held, setHeld] = useState(false);
  const [importing, setImporting] = useState(false);
  /** Per-scene spend, or null until it lands. A second effect on purpose: the
   *  read behind it scans the ledger's whole history, which is the right cost
   *  for the Costs page and the wrong one to put in front of a list. */
  const [costs, setCosts] = useState<CampaignSceneCosts | null>(null);
  const [meta, setMeta] = useState<CampaignMeta | null>(null);
  const [scenes, setScenes] = useState<SceneMeta[] | null>(null);
  /** The rail's shell read, only when it is about this campaign. */
  const { payload: shell, failed: shellFailed, retry: retryShell } = useCampaignShell(cid);
  const [failed, setFailed] = useState(false);
  /** Bumped by the list banner's Try again, so the list is read again rather
   *  than the banner merely cleared over an empty page. */
  const [reload, setReload] = useState(0);
  /** Why the last delete did not happen, or null. Separate from `failed`,
   *  which means the LIST could not be read: one says "look again", the other
   *  says "that scene is busy" over a list that is perfectly fine. */
  const [delFailed, setDelFailed] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");

  usePublishShellContext(meta ? { campaign: meta.name, scene: "" } : null);

  // The Story Graph opens this page with the chooser already steered -- a
  // driver to focus, or an anchor to set -- as history state (§16.5). The
  // graph's sending side is Slice F's; this is the receiving side, and it
  // copies `CampaignView`'s seedPrompt adoption: adopted once, then cleared
  // off the entry, because history outlives the visit. Left in place, Back
  // into this entry or a reload of it would reopen a chooser the reader had
  // already closed, steered the way they had since undone. A state that is not
  // one of the two shapes is someone else's to write, so it opens nothing --
  // but it is cleared all the same.
  const rawHandoff = (location.state as { chooser?: unknown } | null)?.chooser;
  const handoff = useMemo(() => sanitizeSeed(location.state), [location.state]);
  useEffect(() => {
    if (rawHandoff === undefined) return;
    setSeed(handoff);
    setHeld(handoff !== null);
    // Search and hash carried along: the replace exists to drop the STATE.
    navigate(location.pathname + location.search + location.hash,
             { replace: true, state: null });
  }, [rawHandoff, handoff, location.pathname, location.search, location.hash, navigate]);

  // A seeded open waits for the scene list (Decision 20): the chooser ranks
  // against `afterSid`, and opening before the list lands would make it re-ask
  // when the newest scene arrives -- a second paid ranked call. A failed list
  // releases it anyway, with no reference.
  useEffect(() => {
    if (!held || (scenes === null && !failed)) return;
    setHeld(false);
    setChoosing(true);
  }, [held, scenes, failed]);

  useEffect(() => {
    if (!cid) return;
    let live = true;
    setFailed(false);
    Promise.all([
      api.getCampaign(cid).then((r) => r.meta),
      api.listScenes(cid),
    ]).then(([m, sc]) => {
      if (!live) return;
      setMeta(m); setScenes(sc);
    }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [cid, reload]);

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
      if (filter === "open" && (s.done || s.closed_by)) return false;
      if (filter === "absorbed" && !s.done) return false;
      if (needle && !s.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [scenes, filter, q]);

  const counts = useMemo(() => ({
    all: scenes?.length ?? 0,
    open: (scenes ?? []).filter((s) => !s.done && !s.closed_by).length,
    absorbed: (scenes ?? []).filter((s) => s.done).length,
  }), [scenes]);

  /** How many listed scenes share each branch group. A group of one is no
   *  branch at all -- the chip marks scenes that have an alternative. */
  const groupSize = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of scenes ?? []) {
      if (s.branch_group) m.set(s.branch_group, (m.get(s.branch_group) ?? 0) + 1);
    }
    return m;
  }, [scenes]);

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
   *  exists. The shell's half is the rail's read, and `deleteScene` already
   *  tells the rail (`notifyShell`) -- the mutator is the one place every
   *  delete goes through, so asking again from here would be a second request
   *  for the same answer. */
  async function remove(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'? This cannot be undone.`)) return;
    setDelFailed(null);
    try {
      await api.deleteScene(cid, s.id);
    } catch (err) {
      setDelFailed(`'${s.title}' was not deleted: ${errorText(err)}`);
      return;
    }
    setScenes(await api.listScenes(cid));
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
                    onClick={() => {
                      // An ordinary open: never steered by, nor held behind, a
                      // handoff still waiting for the list.
                      setSeed(null); setHeld(false); setChoosing(true);
                    }}>+ New scene</button>
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
            cid={cid} ready={ready} seed={seed ?? undefined}
            // Ranking reference: the newest scene, or none in a fresh campaign.
            afterSid={scenes?.[0]?.id ?? null}
            onClose={(createdSid) => {
              setChoosing(false);
              setSeed(null);
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
              setSeed(null);
              navigate(`/campaigns/${cid}/scenes/${sid}`,
                       initialPrompt ? { state: { seedPrompt: initialPrompt } } : undefined);
            }} />
        )}

        {failed && (
          // A failed read is not an empty campaign. The two must never render
          // the same way: one means "look again", the other "start writing".
          <div className="banner error-banner">
            The scenes could not be read.{" "}
            <button className="subtle" onClick={() => setReload((n) => n + 1)}>Try again</button>
          </div>
        )}

        {delFailed && (
          <div className="banner error-banner">
            {delFailed}{" "}
            <button className="subtle" onClick={() => setDelFailed(null)}>Dismiss</button>
          </div>
        )}

        {/* The shell read failing costs the two things it carries and nothing
            else. The rows still stand, saying only what their frontmatter
            knows -- which is the honest rendering of "not counted" -- and a
            banner over them saying the scenes could not be read would report
            the wrong thing as broken. */}
        {!failed && shellFailed && (
          <div className="banner error-banner" role="status">
            Waiting reviews and turn counts could not be read.{" "}
            <button className="subtle" onClick={retryShell}>Try again</button>
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
              <li key={s.id} className={"scene-item" + (s.done || s.closed_by ? "" : " open")}>
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
                {/* One cell for the chips: the status, and `branch` when the
                    scene has a sibling. A closed branch says so in place of
                    "open" -- unless a review is waiting, which still wins. */}
                <span className="scene-item-chips">
                  {!waiting.has(s.id) && !s.done && s.closed_by ? (
                    <span className="chip" title="A sibling branch was absorbed">closed</span>
                  ) : (
                    <span className={"chip" + (s.done ? "" : " on")}>
                      {waiting.has(s.id)
                        ? `${waiting.get(s.id)} unreviewed`
                        : s.done ? "absorbed" : "open"}
                    </span>
                  )}
                  {s.branch_group && (groupSize.get(s.branch_group) ?? 0) > 1 && (
                    <span className="chip">branch</span>
                  )}
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
                                 + (price === UNPRICED ? " money-unpriced" : "")}
                      title={row ? estimatedTokensTitle(row) : undefined}>
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
