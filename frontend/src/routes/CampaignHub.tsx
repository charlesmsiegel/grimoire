import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CampaignBudget, type CampaignMeta, type CharacterSummary,
         type ChronicleEntry, type PCSummary, type RecordChange,
         type SceneIdea, type SceneMeta } from "../api/client";
import type { ShellPayload } from "../api/types";
import { usePublishShellContext } from "../components/ShellStatus";
import { PageShell, ColumnSection } from "../components/PageShell";
import { errorText } from "../api/errors";
import { MoneyColumns, money } from "../components/cost";
import MechanicsConfig from "../components/MechanicsConfig";
import { CalendarConfig } from "../components/CalendarConfig";
import { CampaignCover } from "../components/CoverPanel";

/** The campaign's front door.
 *
 *  Opening a campaign used to drop the reader straight into whichever scene was
 *  played last -- `CampaignView` navigated to `scenes[0]` the moment it found no
 *  scene in the URL. That is the complaint this page answers: there was nowhere
 *  that said what the campaign was, what was waiting, or what to do next.
 *
 *  It leads with **what to do next** and only then with state, and the order is
 *  the argument. With several scenes open it names them all and offers each,
 *  rather than picking one: being sent to whichever was last is the behaviour
 *  this page replaced, and reproducing it inside a card would be the same
 *  mistake with a nicer border.
 */

function Card({ title, tail, children, foot }: {
  title: string; tail?: React.ReactNode;
  children: React.ReactNode; foot?: React.ReactNode;
}) {
  return (
    <section className="hub-card">
      <div className="hub-card-head">
        <h2>{title}</h2>
        {tail !== undefined && <span className="hub-card-tail">{tail}</span>}
      </div>
      <div className="hub-card-body">{children}</div>
      {foot && <div className="hub-card-foot">{foot}</div>}
    </section>
  );
}

/** A card's count, keeping `0` and "nobody asked" apart.
 *
 *  The same sentence the rail's own `num` is built on, and the cost surfaces
 *  before it: `0` is an answer -- nothing is outstanding -- while `null` means
 *  the field was never computed, and must draw nothing rather than a zero a
 *  reader would take for a measurement. Written out here rather than imported
 *  from `shell/rail.ts`, where it is private: what has to stay level between
 *  the two is the rule, and the rule is one line.
 */
function count(v: number | null | undefined): string | undefined {
  return v === null || v === undefined ? undefined : String(v);
}

/** How many absorbed changes the World changes card lists.
 *
 *  Small on purpose. The question this card answers is "did the last scene
 *  move anything", not "what has ever changed" — that is the panel it links
 *  to, which is built for reading and has the History tab beside it. */
const CHANGES_SHOWN = 4;

/** How many saved scene ideas the Play next card offers.
 *
 *  Three, because this is an offer and not a list: a front door that presents
 *  nine equally-weighted options has asked a question rather than answered
 *  one. The picker is where the whole ledger is managed. */
const IDEAS_SHOWN = 3;

/** What a campaign block that predates `money` reads as.
 *
 *  `partial`, not zeros: a payload with no money block is a server that did not
 *  answer the question, and the card must say so rather than render a played
 *  campaign as free. Spelled out here because a `?? {}` would typecheck and
 *  then draw `$0.00`. */
const ZERO_MONEY = {
  calls: 0, cost_usd: 0, estimated_usd: 0, modelled_usd: 0,
  unpriced_calls: 0, unmetered_calls: 0, subscription_calls: 0,
  modelled_calls: 0, priced_calls: 0, total_tokens: 0, partial: true,
};

/** The cast as one list of faces, PCs first.
 *
 *  PCs lead because they are the records the reader plays; the rest follow in
 *  whatever order the store listed them, which is the same order the world's
 *  own pages use.
 *
 *  A `PCSummary` has no `avatar_v` and a `CharacterSummary` does, which is not
 *  an oversight in either: only a character's avatar can be replaced by a
 *  version promote, so only a character's needs a cache token naming the bytes.
 *  Flattening them here rather than at the call site is what keeps that
 *  asymmetry from becoming a conditional inside JSX.
 */
type Face = {
  key: string; id: string; name: string;
  kind: "pcs" | "characters"; version: string;
  avatar: boolean;
  /** `?v=<bytes>` or empty. Never a counter: an immutable URL is never
   *  revalidated, so a token that reset on mount would pin a replaced image in
   *  the browser cache for a year. */
  token: string;
};

function faces(cast: { chars: CharacterSummary[]; pcs: PCSummary[] }): Face[] {
  return [
    ...cast.pcs.map((p): Face => ({
      key: `pcs:${p.id}`, id: p.id, name: p.name, kind: "pcs",
      version: p.default_version, avatar: !!p.has_avatar, token: "",
    })),
    ...cast.chars.map((c): Face => ({
      key: `characters:${c.id}`, id: c.id, name: c.name, kind: "characters",
      version: c.default_version, avatar: !!c.has_avatar,
      token: c.avatar_v ? `?v=${c.avatar_v}` : "",
    })),
  ];
}

function initials(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("");
}

export default function CampaignHub() {
  const { cid = "" } = useParams();
  const [meta, setMeta] = useState<CampaignMeta | null>(null);
  const [shell, setShell] = useState<ShellPayload | null>(null);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [chronicle, setChronicle] = useState<ChronicleEntry[]>([]);
  const [failed, setFailed] = useState(false);
  /** Why the last scene delete did not happen, or null. */
  const [delFailed, setDelFailed] = useState<string | null>(null);
  /** The three cards that need a read of their own, each `null` until it has
   *  answered and each allowed to stay `null` for good.
   *
   *  Loaded in a SECOND effect, after the four reads the page cannot render
   *  without. That ordering is the whole point: a hub that waits on the cast,
   *  the budget and the change journal before it can say what to do next has
   *  put three summaries in front of its own headline. Each of these fails
   *  soft to a card that says it could not be read, which is not the same as
   *  a card saying there is nothing there. */
  const [cast, setCast] = useState<{ chars: CharacterSummary[]; pcs: PCSummary[] } | null>(null);
  const [castFailed, setCastFailed] = useState(false);
  const [budget, setBudget] = useState<CampaignBudget | null>(null);
  const [changes, setChanges] = useState<RecordChange[] | null>(null);
  const [changesFailed, setChangesFailed] = useState(false);
  const [ideas, setIdeas] = useState<SceneIdea[] | null>(null);
  const [ideasFailed, setIdeasFailed] = useState(false);
  /** Which campaign setting is open, if any.
   *
   *  These used to live only on the scene bar, which meant they were reachable
   *  only from inside a scene -- so opening a campaign gave you no way to bind
   *  a mechanics module, set its calendar or give it a cover. They belong to
   *  the campaign, not to whichever scene you happen to have open, so they are
   *  here. */
  const [panel, setPanel] = useState<"mechanics" | "calendar" | "cover" | null>(null);

  usePublishShellContext(meta ? { campaign: meta.name, scene: "" } : null);

  useEffect(() => {
    if (!cid) return;
    let live = true;
    setFailed(false);
    // Four reads rather than one aggregate: each already exists and answers its
    // own question, and inventing a hub-shaped endpoint would be a fifth place
    // the same counts are derived. `getShell` is the one that already gathers
    // what the rail needs, so the hub and the rail cannot disagree about how
    // many scenes are open.
    Promise.all([
      api.getCampaign(cid).then((r) => r.meta),
      api.getShell(cid),
      api.listScenes(cid),
      api.getChronicle(cid).catch(() => [] as ChronicleEntry[]),
    ]).then(([m, s, sc, ch]) => {
      if (!live) return;
      setMeta(m); setShell(s); setScenes(sc); setChronicle(ch);
    }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [cid]);

  // The cards that are worth having but not worth waiting for. Separate from
  // the effect above so a slow change journal cannot hold up "what to do
  // next", and each settled independently so one failure costs one card.
  useEffect(() => {
    if (!cid) return;
    let live = true;
    setCast(null); setCastFailed(false);
    setChanges(null); setChangesFailed(false);
    setIdeas(null); setIdeasFailed(false);
    setBudget(null);
    const scope = { kind: "campaign", id: cid } as const;
    // THREE reads, and the third is the one that answers the question. Both
    // lists are overlay unions -- the campaign's own records plus everything it
    // inherits from its world -- so on their own they describe the world's
    // population, not this campaign's. Membership is what the appearance record
    // holds: who has actually been on stage here. The lists still supply the
    // names and the avatars; the record decides who is in the card at all.
    //
    // Filtered here rather than served pre-joined, because `roster` is
    // deliberately not name-resolving (a name costs a card read per actor at
    // its locked version) and these two lists are already being read for the
    // faces. Intersecting them costs nothing beyond the third request.
    Promise.all([api.listCharacters(scope), api.listCampaignPCs(cid),
                 api.listAppearances(cid)])
      .then(([chars, pcs, roster]) => {
        if (!live) return;
        // Same `kind:id` spelling `faces` keys on, and the same two kinds the
        // record stores its refs under.
        const appeared = new Set(roster.map((r) => `${r.kind}:${r.id}`));
        setCast({ chars: chars.filter((c) => appeared.has(`characters:${c.id}`)),
                  pcs: pcs.filter((p) => appeared.has(`pcs:${p.id}`)) });
      })
      .catch(() => { if (live) setCastFailed(true); });
    api.campaignChanges(cid)
      .then((rows) => { if (live) setChanges(rows); })
      .catch(() => { if (live) setChangesFailed(true); });
    // A budget is optional and its absence is `level: "off"`, not an error --
    // so a failed read leaves `null` and the bar simply does not draw. There
    // is nothing to warn about: the money columns above it are the card's
    // subject, and the bar is context for them.
    api.getCampaignBudget(cid)
      .then((b) => { if (live) setBudget(b); })
      .catch(() => {});
    // `greetings=false`: the composed greeting half parses the frontmatter of
    // every greeting in the campaign, and this card offers written-down ideas
    // rather than a way into the greeting map -- which the picker already is.
    api.listSceneIdeas(cid, false)
      .then((rows) => {
        if (live) setIdeas(rows.filter((i) => i.status === "active"));
      })
      .catch(() => { if (live) setIdeasFailed(true); });
    return () => { live = false; };
  }, [cid]);

  const camp = shell?.campaign ?? null;
  const open = camp?.open ?? [];
  const waiting = camp?.unreviewed ?? 0;
  const pending = camp?.pending ?? [];
  // A payload that says `null` and a payload that has not arrived are the same
  // answer to a card -- nobody has counted -- so they collapse here rather than
  // being told apart three lines further down where only one of them is
  // reachable.
  const todo = shell?.todo ?? null;
  /** The campaign's all-time money, or `undefined` while the shell read is out.
   *
   *  Three states rather than two, and the middle one is why: `undefined` is
   *  "not yet", `partial: true` is "the ledger could not be totalled", and
   *  anything else is a figure. Collapsing the first two would put "could not
   *  count" on screen for the half-second before the payload lands. */
  const money_ = camp ? (camp.money ?? { ...ZERO_MONEY, partial: true }) : undefined;
  const recap = chronicle.length ? chronicle[chronicle.length - 1] : null;
  /** What each waiting scene is called.
   *
   *  `pending` carries sids and `listScenes` carries titles, and the payload's
   *  own comment says the sids travel for exactly this -- so the reader is not
   *  made to hunt for which scene was absorbed. Without it two waiting scenes
   *  render two buttons with the same words on them, and the only way to tell
   *  which is which is to open one.
   *
   *  A sid with no title is a review sidecar outliving its scene, which the
   *  wrap-up route can still answer; it falls back to the unnamed wording
   *  rather than inventing a name for a scene nobody can read. */
  const sceneTitle = new Map(scenes.map((s) => [s.id, s.title]));

  /** Delete a scene from the hub's preview.
   *
   *  The same call and the same refusal as the scenes list -- see `remove`
   *  there. Both reads are redone because the hub draws the campaign's counts
   *  beside the rows, and a spliced-out row would leave "3 scenes" over two. */
  async function removeScene(s: SceneMeta) {
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
      <ColumnSection label="This campaign">
        <Link className="column-row" to={`/campaigns/${cid}/ledger`}>Ledger &amp; timeline</Link>
        <Link className="column-row" to={`/campaigns/${cid}/costs`}>Costs</Link>
        {/* Only where a module is bound: `sheets` is null when it is not, and
            offering a page that can only say "nothing here" is not an offer. */}
        {camp?.sheets && (
          <Link className="column-row" to={`/campaigns/${cid}/sheets`}>Sheets</Link>
        )}
        <Link className="column-row" to={`/campaigns/${cid}/world`}>World</Link>
      </ColumnSection>
      <ColumnSection label="Settings">
        {([["mechanics", "Mechanics"], ["calendar", "Calendar"],
           ["cover", "Cover"]] as const).map(([id, label]) => (
          <button key={id} type="button"
                  className={"column-row" + (panel === id ? " active" : "")}
                  aria-pressed={panel === id}
                  onClick={() => setPanel(panel === id ? null : id)}>
            {label}
          </button>
        ))}
      </ColumnSection>
      <ColumnSection label="Export">
        <a className="column-row" href={`/api/campaigns/${cid}/export.epub`} download>EPUB</a>
        <a className="column-row" href={`/api/campaigns/${cid}/export.md.zip`} download>Markdown</a>
        <a className="column-row" href={`/api/campaigns/${cid}/export.html`} download>HTML</a>
        <a className="column-row" href={`/api/campaigns/${cid}/export.txt`} download>Plain text</a>
      </ColumnSection>
    </>
  );

  if (failed) {
    return (
      <PageShell column={column} columnLabel="This campaign">
        <div className="page-wide view-anim">
          {/* A failed read is not an empty campaign, and must never render as
              one: "this campaign could not be read" and "this campaign has
              nothing in it" are opposite answers. */}
          <div className="banner error-banner">
            This campaign could not be read.{" "}
            <button className="subtle" onClick={() => setFailed(false)}>Try again</button>
          </div>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell column={column} columnLabel="This campaign">
      {/* One column. The design offered three layouts; a picker that
          rearranges the same cards is a setting the reader has to have an
          opinion about before they can read the page. */}
      <div className="page-wide view-anim hub">
        <div className="eyebrow">
          {[camp?.world_name, camp ? `${camp.scenes} scenes` : null,
            open.length ? `${open.length} open` : null].filter(Boolean).join(" · ")}
        </div>
        <h1 className="screen-title">{meta?.name ?? "…"}</h1>

        {panel && (
          <section className="hub-panel">
            {panel === "mechanics" && (
              <MechanicsConfig cid={cid}
                               onChanged={() => { void api.getShell(cid).then(setShell); }} />
            )}
            {panel === "calendar" && <CalendarConfig scope={{ kind: "campaign", id: cid }} />}
            {panel === "cover" && <CampaignCover cid={cid} />}
          </section>
        )}

        {/* ---- what to do next, before any state ---- */}
        <section className="hub-next">
          <div className="hub-eyebrow">Next up</div>
          {open.length === 0 && (
            <p className="hub-lead">Every scene has been wrapped up.</p>
          )}
          {open.length === 1 && (
            <>
              <h2 className="hub-next-title">{open[0].title}</h2>
              <Link className="hub-primary"
                    to={`/campaigns/${cid}/scenes/${open[0].sid}`}>
                Continue scene →
              </Link>
            </>
          )}
          {open.length > 1 && (
            <>
              <h2 className="hub-next-title">
                {open.length} scenes are open
              </h2>
              {/* Named and offered rather than resumed. Being sent to whichever
                  was played last is exactly what this page exists to stop. */}
              <p className="hub-lead">
                Pick one rather than being sent to whichever was last.
              </p>
              <div className="hub-pickers">
                {open.map((s) => (
                  <Link key={s.sid} className="hub-picker"
                        to={`/campaigns/${cid}/scenes/${s.sid}`}>
                    <span className="hub-picker-title">{s.title}</span>
                    <span className="hub-picker-tail">open →</span>
                  </Link>
                ))}
              </div>
            </>
          )}
          {/* Starting a scene is offered in every state now. It used to render
              only in the empty branch, which made the one thing a reader with
              a scene in flight could not do from the front door "start
              another" -- the front door's whole job.
              Its weight follows what else the panel is offering rather than
              being fixed: with nothing open it IS what to do next, and beside
              Continue or the pickers it is the alternative to them. A second
              filled button there would make the panel ask a question instead
              of answering one. One label and one destination either way, so
              the control keeps the name the Scenes page gives it.
              The secondary dress is the cards' foot line: it is the hub's
              existing "lighter than a button" link, and no new class is worth
              a rule of its own for one link. */}
          {open.length === 0 ? (
            <Link className="hub-primary" to={`/campaigns/${cid}/scenes`}>
              + New scene →
            </Link>
          ) : (
            <div className="hub-card-foot">
              <Link to={`/campaigns/${cid}/scenes`}>+ New scene →</Link>
            </div>
          )}
        </section>

        {/* ---- what is holding the world back ---- */}
        {/* Branched on whether a scene is holding a review, not on how many
            proposals are in it. `unreviewed` is the SUM of proposals across
            the sidecars, so a review whose edits list is empty counts zero
            while its scene is still waiting — and this panel used to answer
            that with "Nothing waiting. Every proposal has been decided",
            which is the one thing it must never say while something is
            waiting. `ScenesView` keys off `pending` and has always been right
            about this. */}
        {pending.length > 0 ? (
          <section className="hub-waiting">
            <div className="hub-eyebrow">Waiting on you</div>
            <p>
              {pending.length === 1 ? "A scene was" : `${pending.length} scenes were`}{" "}
              absorbed but never reviewed
              {/* The count is the reason this matters, so it is said whenever
                  there is one — but a review can hold no proposals at all and
                  still be waiting to be closed, and "0 proposals are still
                  holding the world back" is a sentence that argues against
                  its own panel. */}
              {waiting > 0 ? (
                <> — <strong>{waiting} proposal{waiting === 1 ? "" : "s"}</strong>{" "}
                  {waiting === 1 ? "is" : "are"} still holding the world back.</>
              ) : "."}
            </p>
            {/* One offer per waiting scene, each carrying that scene's name.
                Two scenes used to render two buttons reading the same four
                words, which is a choice with nothing to choose between. */}
            {pending.map((p) => (
              <Link key={p.sid} className="hub-primary"
                    to={`/campaigns/${cid}/scenes/${p.sid}`}>
                {sceneTitle.has(p.sid)
                  ? `Wrap up ${sceneTitle.get(p.sid)} →`
                  : "Open wrap-up →"}
              </Link>
            ))}
          </section>
        ) : (
          <section className="hub-quiet">
            <div className="hub-eyebrow">Nothing waiting</div>
            <p>Every proposal has been decided.</p>
          </section>
        )}

        <div className="hub-grid">
          <Card title="Chronicle"
                foot={<Link to={`/campaigns/${cid}/timeline`}>Full timeline →</Link>}>
            {recap
              ? <p className="hub-prose">{recap.summary || recap.one_line}</p>
              : <p className="field-hint">Nothing absorbed yet.</p>}
          </Card>

          <Card title="Scenes" tail={camp ? String(camp.scenes) : undefined}
                foot={<Link to={`/campaigns/${cid}/scenes`}>All scenes →</Link>}>
            {/* The row is a container now, not the link. It has to be: the ✕
                is a button, and a button inside an anchor is invalid markup
                that on a touch screen opens the scene you meant to delete. So
                the title carries the link and the row carries the rest. */}
            {scenes.slice(0, 5).map((s) => (
              <div key={s.id} className="hub-row">
                <Link className="hub-row-title" to={`/campaigns/${cid}/scenes/${s.id}`}>
                  {s.title}
                </Link>
                <span className={"chip" + (s.done ? "" : " on")}>
                  {s.done ? "absorbed" : "open"}
                </span>
                <button className="hub-row-del" aria-label={`Delete ${s.title}`}
                        title="Delete this scene"
                        onClick={() => { void removeScene(s); }}>✕</button>
              </div>
            ))}
            {delFailed && <p className="field-hint hub-row-error">{delFailed}</p>}
            {!scenes.length && <p className="field-hint">No scenes yet.</p>}
          </Card>

          {/* The money, kept apart. This is the complaint the whole redesign
              opened with -- "it's not surfacing costs (or estimated costs for
              sources that don't report charge amounts)" -- and the answer is
              three columns that are never added, which is what `MoneyColumns`
              is and why it is shared with the Costs page rather than redrawn
              here. The figures are the campaign's whole history: `money` on
              the shell payload is `store.usage_rollup`'s all-time total, which
              is affordable on this page for the same reason it is affordable
              on the rail beside it. */}
          <Card title="Costs"
                foot={<Link to={`/campaigns/${cid}/costs`}>The full ledger →</Link>}>
            {money_ === undefined ? (
              <p className="field-hint">Loading…</p>
            ) : money_.partial ? (
              // The one thing a cost surface may not do is render "could not
              // count" as $0.00, and this is where that case arrives.
              <p className="field-hint money-unpriced">
                The ledger could not be totalled. Nothing here is a zero.
              </p>
            ) : (
              <>
                <MoneyColumns bucket={money_} />
                {budget && budget.level !== "off" && (
                  <>
                    {/* The design's budget bar. Only where a budget is set:
                        with none, `level` is "off" and there is no cap for a
                        bar to be a fraction of -- a full-width empty track
                        would imply one. */}
                    <div className="ctx-bar" role="img"
                         aria-label={`${money(budget.spent_usd ?? 0)} of `
                                     + `${money(budget.limit_usd)} budget`}>
                      <div className={"ctx-bar-fill " + budget.level}
                           style={{ width: `${Math.min(100,
                             Math.round((budget.fraction ?? 0) * 100))}%` }} />
                    </div>
                    <p className="field-hint">
                      {money(budget.spent_usd ?? 0)} of {money(budget.limit_usd)}
                      {" · "}{budget.period === "total" ? "all time" : "this month"}
                    </p>
                  </>
                )}
              </>
            )}
          </Card>

          {/* Who has appeared in this campaign -- not who could. Portraits and
              nothing else, because the count is already on the world's own
              pages and what a front door can add is recognition: a reader who
              knows the game reads a wall of faces faster than a list of names,
              and one who does not has the tooltip and the link. The PC keeps a
              mark of its own -- it is the record the reader plays, and telling
              it apart at a glance survives losing the caption that used to say
              so. "Everyone" below is the escape hatch to the full world list,
              which is a different question and says so. */}
          <Card title="Cast"
                tail={cast ? String(cast.chars.length + cast.pcs.length) : undefined}
                foot={<Link to={`/campaigns/${cid}/world?section=characters`}>
                        Everyone →
                      </Link>}>
            {castFailed ? (
              // "The cast could not be read" and "no cast" are opposite
              // answers, and a failed read must never render as an empty one.
              <p className="field-hint error">The cast could not be read.</p>
            ) : !cast ? (
              <p className="field-hint">Loading…</p>
            ) : !cast.chars.length && !cast.pcs.length ? (
              <p className="field-hint">Nobody has been cast yet.</p>
            ) : (
              // Every face, uncapped. A cap made sense while each face cost a
              // name-width slot and the list was the whole world's; a campaign
              // has as many faces as it has actually seated, and at portrait
              // size they wrap into a few rows rather than a column of text.
              <div className="hub-faces">
                {faces(cast).map((who) => (
                  // The name is the LABEL, not a caption: with no text under
                  // the portrait the link would otherwise announce as its href,
                  // and hovering is how a reader puts a name to a face they do
                  // not recognise. `alt=""` because the label already names it
                  // -- alt text as well would say it twice.
                  <Link key={who.key} title={who.name}
                        aria-label={who.kind === "pcs" ? `${who.name} (PC)` : who.name}
                        className={"hub-face" + (who.kind === "pcs" ? " pc" : "")}
                        to={`/campaigns/${cid}/world`
                            + `?section=${who.kind}&id=${who.id}`}>
                    {who.avatar
                      ? <img className="hub-face-avatar" alt=""
                             src={api.actorImageUrl({ kind: "campaign", id: cid },
                                                    who.kind, who.id,
                                                    who.version, "avatar")
                                  + who.token} />
                      : <span className="initials-avatar" aria-hidden>
                          {initials(who.name)}
                        </span>}
                  </Link>
                ))}
              </div>
            )}
          </Card>

          <Card title="Open threads"
                tail={camp ? String(camp.ledger_open) : undefined}
                foot={<Link to={`/campaigns/${cid}/ledger`}>The ledger →</Link>}>
            <p className="field-hint">
              {camp?.ledger_open
                ? `${camp.ledger_open} still open.`
                : "Nothing is owed."}
            </p>
          </Card>

          {/* What play has actually changed. The rolling view -- the latest
              write-back per record -- which is the one that answers "what does
              the world say now"; the append-only history behind it is a tab on
              the panel this links to, and is a different question. */}
          <Card title="World changes"
                tail={changes ? String(changes.length) : undefined}
                foot={<Link to={`/campaigns/${cid}/ledger`}>Every change →</Link>}>
            {changesFailed ? (
              <p className="field-hint error">The changes could not be read.</p>
            ) : !changes ? (
              <p className="field-hint">Loading…</p>
            ) : !changes.length ? (
              <p className="field-hint">Play has not changed the world yet.</p>
            ) : (
              changes.slice(0, CHANGES_SHOWN).map((c) => (
                <div key={`${c.ref.kind}:${c.ref.id}`} className="hub-row">
                  <span className="hub-row-title">{c.name}</span>
                  <span className="field-hint">
                    {c.fields.length} field{c.fields.length === 1 ? "" : "s"}
                    {c.scene.title ? ` · ${c.scene.title}` : ""}
                  </span>
                </div>
              ))
            )}
          </Card>

          {/* What the app noticed. The hub already reads `shell` for every
              other count on this page and `/todo` is a real route, so the one
              thing the reader had to go out to the rail for was the list of
              what is worth doing next -- which is the question this page
              claims to answer.
              The count goes through `count` rather than a truthiness test:
              `0` is "nothing outstanding" and renders, `null` is "nobody
              computed it" and must draw no tail at all. The body says which
              of the two it is, because a card with no tail and no sentence
              cannot. */}
          <Card title="To do" tail={count(todo)}
                foot={<Link to="/todo">Everything noticed →</Link>}>
            <p className="field-hint">
              {todo === null
                ? "No count was reported."
                : todo
                  ? `${todo} still to answer.`
                  : "Nothing outstanding."}
            </p>
          </Card>

          {/* What to play next, and where the reason comes from.
              The design draws generated suggestions here with the reason each
              was suggested. Generating them is an LLM call, and a call that
              fires because a page loaded is exactly what `useSceneSuggestions`
              was rebuilt to stop -- so this card shows the ideas the reader has
              already SAVED (`#88`'s scene ledger, which survives everything and
              belongs to them rather than to a cache), and sends anyone who
              wants fresh ones to the picker, where the button that spends the
              money is. The premise is the reason: it is what the idea was
              written down for. */}
          <Card title="Play next"
                tail={ideas ? String(ideas.length) : undefined}
                foot={<Link to={`/campaigns/${cid}/scenes`}>Start a scene →</Link>}>
            {ideasFailed ? (
              <p className="field-hint error">The scene ledger could not be read.</p>
            ) : !ideas ? (
              <p className="field-hint">Loading…</p>
            ) : !ideas.length ? (
              <p className="field-hint">
                Nothing saved. The new-scene picker can suggest some.
              </p>
            ) : (
              ideas.slice(0, IDEAS_SHOWN).map((idea) => (
                <div key={idea.id} className="hub-row hub-idea">
                  <span className="hub-row-title">{idea.title}</span>
                  {idea.premise && (
                    <span className="field-hint">{idea.premise}</span>
                  )}
                </div>
              ))
            )}
          </Card>

          {/* Only where a module is bound. "No mechanics" is a legal state, not
              a coverage of 0 of 0, and a card whose whole content is "this does
              not apply here" is a card that should not be on the page. */}
          {camp?.sheets && (
            <Card title="Mechanics &amp; sheets"
                  tail={`${camp.sheets.sheeted} of ${camp.sheets.total}`}
                  foot={<Link to={`/campaigns/${cid}/sheets`}>Sheet coverage →</Link>}>
              <p className="field-hint">
                {camp.sheets.total - camp.sheets.sheeted} without a sheet.
              </p>
            </Card>
          )}
        </div>
      </div>
    </PageShell>
  );
}
