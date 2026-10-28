import { useEffect, useState } from "react";
import { api, type Briefing, type BriefingRow } from "../../api/client";
import { SideSection, type SectionControls } from "./SideSection";

/** Posts after which the briefing (#118) stops opening itself. It is a *pre*-scene
 *  briefing: past a few exchanges the scene has said what it is about and the
 *  section is just occupying the top of the rail. Only the DEFAULT moves — once
 *  the reader toggles it, `collapsed.briefing` is set and their choice wins in
 *  both directions forever, which auto-collapsing on a timer could not do. */
const BRIEFING_OPEN_POSTS = 6;

/** What a failed briefing load degrades to: the empty state, never a stuck
 *  "Loading…" and never a section that outlives the scene it described. */
const NO_BRIEFING: Briefing = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null,
};

// One component for threads and commitments: they differ by `due` alone, which
// only commitments carry, so an optional field is the whole variation and a
// second near-identical component would be repetition with a footnote.
function BriefingRows({ label, rows }:
  { label: string; rows: (BriefingRow & { due?: string })[] }) {
  if (!rows.length) return null;
  return (
    <div className="ledger-group">
      <h5>{label}</h5>
      {rows.map((r) => (
        <div className="ledger-row" key={r.id}>
          <div className="ledger-row-head">
            <strong>{r.title}</strong>
            <span className="chip on">{r.status}</span>
            {r.due && <span className="chip on">due {r.due}</span>}
            {/* The flag the view exists for. Names rather than a tick, because a
                scene with two players needs to know which of them it is. */}
            {r.involves.length > 0 && (
              <span className="chip on">involves {r.involves.join(", ")}</span>
            )}
          </div>
          {r.latest_beat && <p className="ledger-beat">{r.latest_beat}</p>}
        </div>
      ))}
    </div>
  );
}

export function BriefingSection({ cid, sid, refreshKey, posts, collapsed, toggleSection }:
  SectionControls & { cid: string; sid: string; refreshKey: number; posts?: number | undefined }) {
  // Held with the campaign AND scene it came from, the way `LedgerPanel` holds
  // its campaign: the inspector stays mounted across both switches, so a bare
  // `Briefing` would go on flagging the previous scene's cast under the new
  // scene's name until the new request settles. Comparing during render makes
  // that window impossible rather than short.
  //
  // `cid` as well as `sid`, which this first got wrong (Codex review): the
  // route is `/campaigns/:cid` with no `key`, so React Router REUSES
  // `CampaignView` from one campaign to the next, and scene ids are per-campaign
  // (`0001--…`) so they repeat freely across them. Two campaigns sitting on the
  // same scene id would have matched on `sid` alone — showing one game's
  // commitments, relationships and prior fact under the other's name.
  const [brief, setBrief] = useState<{ cid: string; sid: string; data: Briefing } | null>(null);
  // Its own effect rather than a line in the load above, because it is the one
  // request here whose late answer can be *wrong* rather than merely stale: a
  // superseded briefing describes a different scene's cast. `live` drops it.
  useEffect(() => {
    let live = true;
    api.sceneBriefing(cid, sid)
      .then((b) => { if (live) setBrief({ cid, sid, data: b }); })
      .catch(() => { if (live) setBrief({ cid, sid, data: NO_BRIEFING }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);
  // Deliberately keyed on cid+sid and NOT on `refreshKey`, which is the same
  // call `LedgerPanel` makes and states its reason for: a refresh re-reads the
  // SAME scene, so blanking on it would tear the section down and rebuild it
  // after every save and every cast edit, shifting every section below it, to
  // show mostly the same rows back. The cost is that a resolved commitment can
  // linger for the length of one request (Codex review); the section is a hint
  // surface whose rows are already "what was true when the scene opened", so a
  // brief stale flag is inside its contract and a flicker on every save is not.
  const briefing = brief && brief.cid === cid && brief.sid === sid ? brief.data : null;
  // Rendered only when it has something to say. An empty briefing is the normal
  // state of a brand-new campaign, and an always-present "Nothing yet" heading
  // at the top of the rail would push the sections that do have content down for
  // every scene of the first session.
  const hasBriefing = !!briefing && (briefing.commitments.length > 0 || briefing.plot.length > 0
    || briefing.relationships.length > 0 || !!briefing.last_time);
  const occurrences = new Map<string, number>();
  const relationships = (briefing?.relationships ?? []).map((text) => {
    const occurrence = occurrences.get(text) ?? 0;
    occurrences.set(text, occurrence + 1);
    return { key: JSON.stringify([text, occurrence]), text };
  });
  return <>
      {hasBriefing && briefing && (
        <SideSection id="briefing" title="Briefing"
                     collapsed={collapsed.briefing ?? (posts ?? 0) > BRIEFING_OPEN_POSTS}
                     onToggle={toggleSection}>
          {briefing.last_time && (
            <p className="ledger-beat">
              <span className="field-hint">Last time · {briefing.last_time.title}
                {briefing.last_time.date ? ` (${briefing.last_time.date})` : ""}</span>
              <br />{briefing.last_time.one_line}
            </p>
          )}
          {/* Commitments before threads, the order `LedgerPanel` argues for: what
              the story still OWES is the question, and a thread is only in motion. */}
          <BriefingRows label="Commitments" rows={briefing.commitments} />
          <BriefingRows label="Open plot threads" rows={briefing.plot} />
          {briefing.relationships.length > 0 && (
            <div className="ledger-group">
              <h5>Between them</h5>
              {/* The API supplies display strings, not pair identities. Distinct
                  same-named pairs can produce duplicate lines. Text plus its
                  occurrence preserves those lines without tying unrelated rows
                  to their array positions; true pair identity is unavailable. */}
              {relationships.map(({ key, text }) => (
                <div className="field-hint" key={key}>{text}</div>
              ))}
            </div>
          )}
        </SideSection>
      )}
  </>;
}
