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
  const recap = chronicle.length ? chronicle[chronicle.length - 1] : null;

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
            <>
              <p className="hub-lead">Every scene has been wrapped up.</p>
              <Link className="hub-primary" to={`/campaigns/${cid}/scenes`}>
                Start a scene →
              </Link>
            </>
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
        </section>

        {/* ---- what is holding the world back ---- */}
        {waiting > 0 ? (
          <section className="hub-waiting">
            <div className="hub-eyebrow">Waiting on you</div>
            <p>
              A scene was absorbed but never reviewed — <strong>{waiting} proposal
              {waiting === 1 ? "" : "s"}</strong>{" "}
              {waiting === 1 ? "is" : "are"} still holding the world back.
            </p>
            {camp?.pending?.map((p) => (
              <Link key={p.sid} className="hub-primary"
                    to={`/campaigns/${cid}/scenes/${p.sid}`}>
                Open wrap-up →
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
