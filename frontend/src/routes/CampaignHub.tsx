import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CampaignMeta, type ChronicleEntry, type SceneMeta } from "../api/client";
import type { ShellPayload } from "../api/types";
import { usePublishShellContext } from "../components/ShellStatus";
import { PageShell, ColumnSection } from "../components/PageShell";
import MechanicsConfig from "../components/MechanicsConfig";
import { CalendarConfig } from "../components/CalendarConfig";
import { CampaignCover } from "../components/CampaignCover";

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

export default function CampaignHub() {
  const { cid = "" } = useParams();
  const [meta, setMeta] = useState<CampaignMeta | null>(null);
  const [shell, setShell] = useState<ShellPayload | null>(null);
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [chronicle, setChronicle] = useState<ChronicleEntry[]>([]);
  const [failed, setFailed] = useState(false);
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

  const camp = shell?.campaign ?? null;
  const open = camp?.open ?? [];
  const waiting = camp?.unreviewed ?? 0;
  const pending = camp?.pending ?? [];
  // A payload that says `null` and a payload that has not arrived are the same
  // answer to a card -- nobody has counted -- so they collapse here rather than
  // being told apart three lines further down where only one of them is
  // reachable.
  const todo = shell?.todo ?? null;
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
            {scenes.slice(0, 5).map((s) => (
              <Link key={s.id} className="hub-row" to={`/campaigns/${cid}/scenes/${s.id}`}>
                <span className="hub-row-title">{s.title}</span>
                <span className={"chip" + (s.done ? "" : " on")}>
                  {s.done ? "absorbed" : "open"}
                </span>
              </Link>
            ))}
            {!scenes.length && <p className="field-hint">No scenes yet.</p>}
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
