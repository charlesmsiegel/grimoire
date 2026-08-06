import { useEffect, useState } from "react";
import { api, type Commitment, type Ledger } from "../api/client";

// Read-only, so it is a sectioned panel rather than the two-pane list/detail
// editor CLAUDE.md prescribes for record pages: there is no record to open and
// nothing to edit. Commitments come first — they are what the campaign still
// owes, which is the question this view exists to answer; plot threads say what
// is merely in motion, standing facts say what is simply true, and the
// chronicle is the backdrop.
const KINDS: { kind: string; label: string }[] = [
  { kind: "promise", label: "Promises" },
  { kind: "threat", label: "Threats" },
  { kind: "foreshadowing", label: "Foreshadowing" },
];

// What a failed load degrades to: the empty state, never a stuck "Loading…".
const EMPTY: Ledger = { plot: [], commitments: [], facts: [], chronicle: [] };

function sceneNote(scene: { title: string; date: string }) {
  if (!scene.title) return null;
  return (
    <span className="field-hint">
      {" · last moved in " + scene.title + (scene.date ? ` (${scene.date})` : "")}
    </span>
  );
}

function CommitmentRow({ c }: { c: Commitment }) {
  return (
    <div className="ledger-row">
      <div className="ledger-row-head">
        <strong>{c.title}</strong>
        <span className="chip on">{c.status}</span>
        {c.due && <span className="chip on">due {c.due}</span>}
      </div>
      {c.latest_beat && <p className="ledger-beat">{c.latest_beat}</p>}
      <p className="ledger-meta">{sceneNote(c.scene)}</p>
    </div>
  );
}

// `refreshKey` is the caller's post-save revision. Without it the effect keys on
// `cid` alone, which does not change when an absorb review is saved — so a
// ledger left open across a save would go on showing the state from before the
// scene that just landed, which is the one thing a continuity view must not do.
export function LedgerPanel({ cid, refreshKey = 0 }: { cid: string; refreshKey?: number }) {
  // What is held is the campaign the rows came FROM, not the rows alone. The
  // panel stays mounted across a campaign switch, so a bare `Ledger | null`
  // goes on rendering the old campaign's promises and facts under the new
  // campaign's name until the new request settles — attributing one game's
  // secrets to another. Comparing the stored cid against the current one
  // during render makes that window impossible rather than short.
  const [loaded, setLoaded] = useState<{ cid: string; data: Ledger } | null>(null);

  useEffect(() => {
    // Superseded responses are dropped rather than raced: two fetches can be in
    // flight after a campaign switch or a post-save refresh, and whichever
    // finishes LAST wins without this — which is precisely the pre-absorb
    // ledger overwriting the one the save just triggered.
    let live = true;
    api.campaignLedger(cid)
      .then((l) => { if (live) setLoaded({ cid, data: l }); })
      .catch(() => { if (live) setLoaded({ cid, data: EMPTY }); });
    return () => { live = false; };
  }, [cid, refreshKey]);

  // Only `cid` blanks the panel. A `refreshKey` bump re-reads the SAME
  // campaign, and dropping to "Loading…" for that would flash the whole view
  // away after every save to show mostly the same rows back.
  const ledger = loaded && loaded.cid === cid ? loaded.data : null;
  if (ledger === null) return <div className="ledger-panel">Loading…</div>;

  const empty = !ledger.commitments.length && !ledger.plot.length
    && !ledger.facts.length && !ledger.chronicle.length;
  if (empty)
    return (
      <div className="ledger-panel">
        <p className="field-hint">
          Nothing on the ledger yet. Absorbing a scene opens the promises,
          threats and plot threads it leaves behind.
        </p>
      </div>
    );

  return (
    <div className="ledger-panel">
      {ledger.commitments.length > 0 && (
        <div className="side-section">
          <h4>Commitments</h4>
          {KINDS.map((g) => {
            const group = ledger.commitments.filter((c) => c.kind === g.kind);
            if (!group.length) return null;
            return (
              <div key={g.kind} className="ledger-group">
                <h5>{g.label}</h5>
                {group.map((c) => <CommitmentRow key={c.id} c={c} />)}
              </div>
            );
          })}
          {/* A commitment stored with an unrecognized kind still belongs on the
              ledger — dropping it would hide the one record type this view is
              named for behind a vocabulary check. */}
          {ledger.commitments
            .filter((c) => !KINDS.some((g) => g.kind === c.kind))
            .map((c) => <CommitmentRow key={c.id} c={c} />)}
        </div>
      )}

      {ledger.plot.length > 0 && (
        <div className="side-section">
          <h4>Open plot threads</h4>
          {ledger.plot.map((t) => (
            <div key={t.id} className="ledger-row">
              <div className="ledger-row-head">
                <strong>{t.title}</strong>
                <span className="chip on">{t.status}</span>
              </div>
              {t.latest_beat && <p className="ledger-beat">{t.latest_beat}</p>}
              <p className="ledger-meta">{sceneNote(t.scene)}</p>
            </div>
          ))}
        </div>
      )}

      {ledger.facts.length > 0 && (
        <div className="side-section">
          <h4>Standing facts</h4>
          {ledger.facts.map((f) => (
            <div key={f.id} className="ledger-row">
              <p className="ledger-beat">{f.text}</p>
              <p className="ledger-meta">
                {f.date && <span className="chip on">{f.date}</span>}
                {/* "recorded in", not the "last moved in" the two sections
                    above use: a fact is written once and retired off this list
                    rather than moved, so naming the scene as its latest
                    movement would misdate every row. */}
                {f.scene.title && (
                  <span className="field-hint">
                    {" · recorded in " + f.scene.title
                      + (f.scene.date ? ` (${f.scene.date})` : "")}
                  </span>
                )}
              </p>
            </div>
          ))}
        </div>
      )}

      {ledger.chronicle.length > 0 && (
        <div className="side-section">
          <h4>Recent facts</h4>
          {ledger.chronicle.map((f) => (
            <div key={f.id} className="ledger-row">
              <p className="ledger-beat">{f.one_line}</p>
              <p className="ledger-meta">
                <span className="field-hint">
                  {f.title}{f.date ? ` · ${f.date}` : ""}
                </span>
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
