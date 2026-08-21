import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type EntityKind, type JournalEntry, type RecordChange }
  from "../api/client";

// Every ref kind a write-back can carry needs a heading here, because a row
// filed under a kind no heading claims is fetched, counted, and then never
// rendered. Absorb evolves the body of any of the five entity kinds (#224), not
// just lore and locations, so a group's or an item's change would otherwise be
// logged and then be invisible. Keyed by `EntityKind` rather than listed, so a
// sixth kind fails typecheck here instead of quietly going missing; the
// insertion order is the order the headings appear in.
const ENTITY_HEADINGS: Record<EntityKind, string> = {
  lore: "Lore", locations: "Locations", items: "Items",
  groups: "Groups", creatures: "Creatures",
};

const GROUPS: { kind: string; label: string }[] = [
  { kind: "characters", label: "Characters" },
  ...Object.entries(ENTITY_HEADINGS).map(([kind, label]) => ({ kind, label })),
];

/** The two things this panel answers, which are different questions (#31).
 *  Records is the rolling view — what does each record's latest write-back say —
 *  and History is the append-only journal behind it, which is the only one of
 *  the two that can be undone: a rolling entry has already forgotten whatever
 *  the write before it replaced. */
type Tab = "records" | "history";

function sceneLabel(scene: { id: string; title: string; date: string }): string {
  if (!scene.id) return "";
  return scene.title + (scene.date ? ` (${scene.date})` : "");
}

/** What a row in the History rail says about itself: where the change came
 *  from, and whether it still stands. `manual` and `undo` are edits made outside
 *  any scene, so there is no scene to name for them. */
function originLabel(entry: JournalEntry): string {
  if (entry.source === "undo") return "undo";
  if (entry.source === "manual") return "edited by hand";
  const scene = sceneLabel(entry.scene);
  return scene ? `absorbed from ${scene}` : "absorbed";
}

/** The journalled timestamp is UTC (`…Z`, stamped by the store); show it local.
 *  Same helper as `SceneInspector`, and the same reason: without it a hand edit
 *  and a reversal — neither of which belongs to a scene — carry no "when" at
 *  all, and the rail becomes an unordered list of things that happened. */
function whenLabel(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function Diff({ diff }: { diff: JournalEntry["diff"] }) {
  if (!diff.length) return <p className="field-hint">Nothing to show for this change.</p>;
  return (
    <div className="record-diff">
      {diff.map((d, i) => (
        <div key={i} className={"diff-line diff-" + d.op}>{d.text}</div>
      ))}
    </div>
  );
}

function RecordsTab({ rows }: { rows: RecordChange[] }) {
  const [sel, setSel] = useState<string | null>(null);
  if (rows.length === 0)
    return <p className="field-hint">No record changes yet.</p>;

  const active = rows.find((r) => `${r.ref.kind}/${r.ref.id}` === sel) ?? null;

  return (
    <div className="editor">
      <div className="editor-list">
        {GROUPS.map((g) => {
          const group = rows.filter((r) => r.ref.kind === g.kind);
          if (!group.length) return null;
          return (
            <div key={g.kind} className="side-section">
              <h4>{g.label}</h4>
              {group.map((r) => {
                const key = `${r.ref.kind}/${r.ref.id}`;
                return (
                  <button key={key} className={"row" + (key === sel ? " active" : "")}
                          onClick={() => setSel(key)}>
                    {r.name}
                    <span className="field-hint">
                      {" · changed in " + r.scene.title + (r.scene.date ? ` (${r.scene.date})` : "")}
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="editor-body">
        {active ? (
          <div className="detail-view">
            <h3>{active.name}</h3>
            {active.fields.map((f, fi) => (
              <div key={`${f.field}-${fi}`} className="side-section">
                <h4>{f.label}</h4>
                <Diff diff={f.diff} />
              </div>
            ))}
          </div>
        ) : (
          <p className="field-hint">Select a record to see what changed.</p>
        )}
      </div>
    </div>
  );
}

function HistoryTab({ cid, entries, onUndone }:
                    { cid: string; entries: JournalEntry[]; onUndone: () => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (entries.length === 0)
    return <p className="field-hint">Nothing has been changed in this campaign yet.</p>;

  const active = entries.find((e) => e.id === sel) ?? null;

  async function undo(entry: JournalEntry) {
    setBusy(true);
    setErr(null);
    try {
      await api.undoJournalEntry(cid, entry.id);
      // Re-read rather than patching the row in place: undoing appends a second
      // entry (the reversal, which is itself undoable), so the list the server
      // returns is a different list and not the old one with one flag flipped.
      onUndone();
      setSel(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "could not undo that change");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="editor">
      <div className="editor-list">
        {entries.map((e) => (
          <button key={e.id} className={"row" + (e.id === sel ? " active" : "")}
                  onClick={() => { setSel(e.id); setErr(null); }}>
            {e.label || e.name || `${e.ref.kind}/${e.ref.id}`}
            <span className="field-hint">
              {" · " + originLabel(e) + (e.undone ? " · undone" : "")}
            </span>
          </button>
        ))}
      </div>
      <div className="editor-body">
        {active ? (
          // The list/detail pattern: the change itself on the left, what can be
          // done about it in the sidebar — the same shape every record page in
          // this app uses, with Undo where Edit normally sits.
          <div className="detail-view">
            <div className="detail-main">
              <h3>{active.label || active.name || active.ref.id}</h3>
              <Diff diff={active.diff} />
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button onClick={() => undo(active)}
                        disabled={busy || !active.undoable}>
                  {active.undone ? "Undone" : "Undo this change"}
                </button>
              </div>
              {/* The reason lives beside the disabled button, not in a tooltip:
                  "this cannot be taken back, and here is why" is the whole
                  answer, and hiding half of it behind a hover is not one. */}
              {!active.undoable && (
                <p className="field-hint">
                  {active.undone
                    ? "Already undone — the reversal is its own entry above."
                    : active.why || "This change cannot be undone."}
                </p>
              )}
              {err && <div className="banner error-banner" role="alert">{err}</div>}
              <div className="side-section">
                <h4>Change</h4>
                <span className="chip on">{active.kind}</span>
                <p className="field-hint">{originLabel(active)}</p>
                <p className="field-hint">{whenLabel(active.ts)}</p>
                {active.undone && (
                  <p className="field-hint">undone {whenLabel(active.undone.ts)}</p>
                )}
              </div>
            </aside>
          </div>
        ) : (
          <p className="field-hint">Select a change to see it, and to undo it.</p>
        )}
      </div>
    </div>
  );
}

export function ChangesPanel({ cid }: { cid: string }) {
  const [tab, setTab] = useState<Tab>("records");
  const [rows, setRows] = useState<RecordChange[] | null>(null);
  const [entries, setEntries] = useState<JournalEntry[] | null>(null);

  const loadJournal = useCallback(() => {
    api.campaignJournal(cid).then(setEntries).catch(() => setEntries([]));
    // The rolling view moves too: undoing a browsable change repoints that
    // record's delta at the reversal (`store/undo._roll_back_panels`), so a
    // Records tab left on the old response would describe a change the record
    // no longer holds.
    api.campaignChanges(cid, true).then(setRows).catch(() => setRows([]));
  }, [cid]);

  useEffect(() => {
    api.campaignChanges(cid).then(setRows).catch(() => setRows([]));
  }, [cid]);

  // Only once the reader asks for it: the history is the larger read of the
  // two, and the panel opens on Records.
  useEffect(() => {
    if (tab === "history" && entries === null) loadJournal();
  }, [tab, entries, loadJournal]);

  return (
    <div className="changes-panel">
      <div className="tabs" role="tablist" aria-label="Changes">
        {([["records", "Records"], ["history", "History"]] as [Tab, string][])
          .map(([key, label]) => (
            <button key={key} role="tab" aria-selected={tab === key}
                    className={"tab" + (tab === key ? " active" : "")}
                    onClick={() => setTab(key)}>{label}</button>
          ))}
      </div>
      <div role="tabpanel">
        {tab === "records"
          ? (rows === null ? <div>Loading…</div> : <RecordsTab rows={rows} />)
          : (entries === null ? <div>Loading…</div>
             : <HistoryTab cid={cid} entries={entries} onUndone={loadJournal} />)}
      </div>
    </div>
  );
}
