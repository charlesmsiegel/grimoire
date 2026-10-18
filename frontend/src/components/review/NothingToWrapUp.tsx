// The wrap-up address with nothing to wrap up.
import { Link } from "react-router-dom";
import type { SceneMeta } from "../../api/client";
import { useCampaignShell } from "../../shell/ShellPayloadContext";
import { ColumnSection, PageShell } from "../PageShell";

const scenePath = (cid: string, sid: string) =>
  `/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}`;

/** What `/wrap-up` says when its scene is holding no review.
 *
 *  Reached by a bookmark, a back button after a save, or a link to a review
 *  somebody else already decided -- all ordinary, none of them errors.
 *  Rendering the transcript here would answer "what is waiting?" with a scene,
 *  which is a different question; the scene is one click away and named, so
 *  nothing is lost by saying so.
 *
 *  Its column is the question the page is about: which of this campaign's
 *  scenes ARE waiting, each a link to its own wrap-up. It used to be no column
 *  at all -- a blank 274px strip beside the page, and an empty sheet on a
 *  phone -- which said less than the rail's badge beside it did.
 */
export default function NothingToWrapUp({ cid, campaignName, sid, sceneTitle, scenes }: {
  cid: string;
  campaignName: string;
  sid: string | null;
  sceneTitle: string;
  /** The campaign's scenes, for the waiting ones' titles. */
  scenes: SceneMeta[];
}) {
  const { payload, failed, retry } = useCampaignShell(cid);
  const pending = payload?.campaign?.pending ?? null;
  const titleOf = new Map(scenes.map((s) => [s.id, s.title]));
  // The route's scene may still be waiting: its detail read can be pending or
  // refused while the shell already knows about the stored review. Only the
  // shell's next successful list can remove it from this column.
  const waiting = pending ?? [];
  const column = (
    <ColumnSection label="Waiting for a review">
      {failed && (
        <p className="column-empty" role="status">
          Waiting reviews could not be read. <button className="subtle" onClick={retry}>Try again</button>
        </p>
      )}
      {pending === null ? (
        failed ? null : <p className="column-empty">Checking…</p>
      ) : waiting.length === 0 ? (
        <p className="column-empty">No scene in this campaign is.</p>
      ) : waiting.map((p) => (
        <Link key={p.sid} className="column-row" to={`${scenePath(cid, p.sid)}/wrap-up`}>
          <span className="column-row-label">{titleOf.get(p.sid) ?? "An untitled scene"}</span>
          <span className="column-row-count"
                aria-label={`${p.proposals} ${p.proposals === 1 ? "proposal" : "proposals"}`}>
            {p.proposals}
          </span>
        </Link>
      ))}
    </ColumnSection>
  );
  return (
    <PageShell column={column} columnLabel="Waiting reviews">
      <div className="page-wide view-anim">
        <div className="eyebrow">{campaignName}</div>
        <h1 className="screen-title">Nothing to wrap up</h1>
        <p className="empty-state">
          {sceneTitle
            ? `${sceneTitle} is not holding a review. Every proposal from it `
              + "has been decided, or it has not been ended yet."
            : "This scene is not holding a review."}
        </p>
        <div className="wrapup-nothing-actions">
          {sid && (
            <Link className="hub-primary" to={scenePath(cid, sid)}>Open the scene →</Link>
          )}
          <Link className="subtle" to={`/campaigns/${encodeURIComponent(cid)}`}>
            Back to the campaign
          </Link>
        </div>
      </div>
    </PageShell>
  );
}
