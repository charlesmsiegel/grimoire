import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { agingLabel } from "../aging";
import {
  api, type Ledger, type RecordChange, type RelationshipChange, type RetiredFact,
  type StandingFact,
} from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";

type SectionKey =
  | "facts" | "threads" | "commitments" | "relationships" | "standings"
  | "changes" | "timeline";

/** One row of the table, whatever section built it.
 *
 *  Every section answers the same four questions — which record, what it says,
 *  as of when, and in which scene — so they share one row shape and one
 *  renderer. The alternative, a table per section, is six places for the
 *  columns to drift apart while claiming to be one ledger. */
type Row = {
  key: string;
  /** The 40px column. A fact id (`f9`) for the section whose ids are short by
   *  design; a glyph everywhere else, since a thread id is a slug and would be
   *  a truncated word pretending to be an identifier. */
  mark: string;
  what: ReactNode;
  /** The second line under `what`: a supersession, a beat, a diff. */
  note?: ReactNode;
  asOf: string;
  scene: string;
  /** Struck through at 55%: this row is history, and it is still on the page. */
  retired?: boolean;
  /** The alert colour — a threat is the one thing here that is owed AGAINST
   *  you, which the design gives its own weight for that reason. */
  alert?: boolean;
};

const SECTIONS: { key: SectionKey; label: string; eyebrow: string;
                  columns: [string, string, string, string] }[] = [
  { key: "facts", label: "Standing facts", eyebrow: "DATED TRUTHS · RETIRED, NEVER EDITED",
    columns: ["ID", "FACT", "AS OF", "SCENE"] },
  { key: "threads", label: "Threads", eyebrow: "OPEN AND ADVANCED · PLOT.JSON",
    columns: ["", "THREAD", "STATUS", "SCENE"] },
  { key: "commitments", label: "Commitments", eyebrow: "WHAT IS STILL OWED · COMMITMENTS.JSON",
    columns: ["", "COMMITMENT", "DUE", "SCENE"] },
  { key: "relationships", label: "Relationships", eyebrow: "FEELINGS AND BONDS · 0–5 METERS",
    columns: ["", "BETWEEN", "STANDING", "SINCE"] },
  { key: "standings", label: "Relationship history", eyebrow: "EVERY DELTA, NEWEST FIRST",
    columns: ["", "BETWEEN", "WAS", "SCENE"] },
  { key: "changes", label: "Recent changes", eyebrow: "WHAT THE LAST ABSORB MOVED",
    columns: ["", "RECORD", "FIELD", "SCENE"] },
  { key: "timeline", label: "Timeline", eyebrow: "THE CHRONICLE, NEWEST FIRST",
    columns: ["", "WHAT HAPPENED", "DATE", "SCENE"] },
];

/** What a failed load degrades to: the empty state, never a stuck "Loading…" —
 *  the same policy the panel this screen replaced ran on. */
const EMPTY: Ledger = {
  plot: [], commitments: [], facts: [], retired: [], relationships: [], chronicle: [],
  stale_after_days: 0,
};

/** The rows of the standing-facts table, with every supersession chain hung
 *  under the fact that ended it.
 *
 *  This ordering IS the screen. A retired fact keeps the place its recording
 *  scene gave it in `facts`/`retired`, but it is rendered directly beneath the
 *  fact that replaced it, because the pair is one sentence about the world
 *  changing and reading half of it in date order tells you nothing. A chain
 *  three deep nests the same way, newest first.
 *
 *  What `showRetired` covers, and what it deliberately does not:
 *
 *  - A **superseded** fact is shown whenever the fact that replaced it is on
 *    screen, toggle or no toggle. Hiding it by default would hide the only
 *    history this store keeps that a snapshot cannot, and would leave the row
 *    above it claiming a truth with no account of what it overturned.
 *  - A fact **retired outright** — nothing replaced it, `superseded_by` is
 *    empty — is what the toggle is for. It is not part of any chain, it is off
 *    the ledger for good, and a list of standing truths that silently carries
 *    every lapsed one is a list nobody can read at a glance.
 *
 *  A chain whose head is itself retired-outright goes with its head: showing
 *  "REPLACED BY f9" for an f9 that is nowhere on the page is worse than not
 *  showing it at all.
 */
function factRows(ledger: Ledger, showRetired: boolean): Row[] {
  // Predecessors indexed by what replaced them. A list per id rather than one
  // entry: two scenes can each retire a fact into the same replacement, and a
  // hand-edited file can say so about any number of them.
  const priorTo = new Map<string, RetiredFact[]>();
  for (const f of ledger.retired) {
    if (!f.superseded_by) continue;
    const at = priorTo.get(f.superseded_by);
    if (at) at.push(f); else priorTo.set(f.superseded_by, [f]);
  }
  const known = new Set([...ledger.facts, ...ledger.retired].map((f) => f.id));

  const out: Row[] = [];
  // facts.json is hand-editable, so `superseded_by` can point in a circle (f1
  // superseded by f2 and f2 by f1, both written by hand). Without this the walk
  // below recurses until the tab dies, and an infinite loop is not a degraded
  // row. It also keeps a fact reachable by two paths from listing twice.
  const seen = new Set<string>();

  /** The annotation on a row that REPLACED something: the id it overturned and
   *  the sentence that id used to say, so the change is legible without
   *  hunting down the page for the struck-through row underneath. */
  function supersedes(fid: string): ReactNode {
    const priors = priorTo.get(fid) ?? [];
    if (!priors.length) return undefined;
    return priors.map((p) => `SUPERSEDED ${p.id} · “${p.text}”`).join("  ·  ");
  }

  function emit(f: StandingFact | RetiredFact, retired: boolean) {
    if (seen.has(f.id)) return;
    seen.add(f.id);
    const ended = "superseded_by" in f ? f : null;
    out.push({
      key: f.id, mark: f.id, retired, what: f.text,
      note: ended
        ? `RETIRED IN ${ended.retired_scene.title || "AN UNKNOWN SCENE"}`
          + (ended.superseded_by ? ` · REPLACED BY ${ended.superseded_by}` : "")
        : supersedes(f.id),
      asOf: f.date, scene: f.scene.title,
    });
    // Depth-first, so a chain three deep reads newest at the top and oldest at
    // the bottom — the order it happened in, upside down, which is the order a
    // ledger is read in.
    for (const prior of priorTo.get(f.id) ?? []) emit(prior, true);
  }

  for (const f of ledger.facts) emit(f, false);
  if (showRetired) {
    // The heads of the retired chains: a fact that was retired outright, or one
    // whose replacement this campaign no longer holds (a hand-edited file, a
    // record deleted by hand). Heads first, so a fully-retired chain still
    // renders as a chain instead of scattering its links down the table in
    // recording order.
    for (const f of ledger.retired) {
      if (!f.superseded_by || !known.has(f.superseded_by)) emit(f, true);
    }
    // And whatever a cycle left unreachable, rather than silently dropping it.
    for (const f of ledger.retired) emit(f, true);
  }
  return out;
}

/** The note line, skipping the parts that are empty — a stale badge on a thread
 *  with no beat should not leave a dangling separator. */
function joinNote(...parts: string[]): string {
  return parts.filter(Boolean).join(" · ");
}

function rowsFor(section: SectionKey, ledger: Ledger, changes: RecordChange[],
                 standings: RelationshipChange[], showRetired: boolean): Row[] {
  if (section === "facts") return factRows(ledger, showRetired);
  // The aging badge (#103) leads the note on both sections: "overdue by 12
  // days" is the reason to read the row, and a reader scanning for what the
  // campaign has let slip should not have to reach the end of a beat to find
  // it. Empty for a record inside the campaign's patience, and for one there is
  // no clock or dated scene to measure — an unbadged row is "cannot tell", the
  // same thing this table showed before aging existed.
  if (section === "threads")
    return ledger.plot.map((t) => ({
      key: t.id, mark: "▸", what: t.title,
      note: joinNote(agingLabel(t.aging), t.latest_beat),
      // No `alert` here: a thread has no deadline, so it can only ever be
      // stale, and colouring a row for a state it cannot reach would imply the
      // section has an urgency it does not.
      asOf: t.status.toUpperCase(), scene: t.scene.title,
    }));
  if (section === "commitments")
    return ledger.commitments.map((c) => ({
      key: c.id, mark: c.kind === "threat" ? "◆" : "◇",
      alert: c.kind === "threat" || c.aging?.state === "overdue",
      what: c.title,
      note: joinNote(agingLabel(c.aging), c.kind.toUpperCase(), c.latest_beat),
      asOf: c.due || "NO DEADLINE", scene: c.scene.title,
    }));
  if (section === "relationships")
    return ledger.relationships.map((r) => ({
      key: r.id,
      // Directed and symmetric are different facts about two people, and the
      // arrow is the whole difference: A distrusting B says nothing about what
      // B feels back.
      mark: r.kind === "bond" ? "↔" : "→",
      what: `${r.a_name} ${r.kind === "bond" ? "↔" : "→"} ${r.b_name}`,
      note: r.kind === "bond"
        ? (r.type ? r.type.toUpperCase() : "BOND")
        : `TRUST ${r.trust} · AFFECTION ${r.affection} · TENSION ${r.tension}`
          + (r.note ? ` · ${r.note}` : ""),
      asOf: r.kind === "bond" ? "" : `${r.trust}/${r.affection}/${r.tension}`,
      scene: r.scene.title,
    }));
  if (section === "standings")
    return standings.map((r) => ({
      key: r.id,
      // The same two glyphs the section above uses, and they mean the same
      // thing: a feeling runs one way, a bond runs both. A row that borrowed
      // the other section's arrow would be describing a different fact.
      mark: r.kind === "bond" ? "↔" : "→",
      what: `${r.a_name} ${r.kind === "bond" ? "↔" : "→"} ${r.b_name}`,
      // The new standing on the note line and the old one in the AS OF column:
      // the reader is here for what changed, and reading it as "now X, was Y"
      // puts the answer first. A reversal is badged, since it is the one row
      // that did not come from play — but REVERSED rather than UNDONE, because
      // undoing an undo is a redo and the store deliberately does not claim
      // which of the two this was (store/relationship_history.py). The row's
      // own two standings say which way it ran. Absorb is unbadged: a badge on
      // every row is no badge at all.
      note: joinNote(r.source === "undo" ? "REVERSED" : "", r.after || "NOTHING"),
      asOf: r.before || "—",
      scene: r.scene.title,
    }));
  if (section === "changes")
    return changes.map((c) => ({
      key: `${c.ref.kind}/${c.ref.id}`, mark: "✎", what: c.name,
      note: c.fields.map((f) => f.label).filter(Boolean).join(" · "),
      asOf: c.ref.kind.toUpperCase(), scene: c.scene.title,
    }));
  return ledger.chronicle.map((e) => ({
    key: e.id, mark: "·", what: e.one_line, asOf: e.date, scene: e.title,
  }));
}

/** What the ledger looks like with nothing in it yet. Named per section rather
 *  than one "nothing here": an empty room should still say where you are and
 *  what fills it (4g). */
const NOTHING: Record<SectionKey, string> = {
  facts: "No standing facts yet. Absorbing a scene records the truths it "
    + "established, dated in the campaign's own reckoning.",
  threads: "No open threads. A thread opens when a scene leaves something in "
    + "motion, and closes when a later one resolves it.",
  commitments: "Nothing owed. Promises, threats and foreshadowing land here as "
    + "scenes make them.",
  relationships: "No feelings or bonds recorded yet. They arrive with the "
    + "absorb pass, once two actors have shared a scene.",
  standings: "No relationship deltas yet. Every feeling and bond an absorb "
    + "applies is kept here, so the arc survives the standing that replaced it.",
  changes: "Nothing has moved yet. This lists what the last absorbs rewrote, "
    + "record by record.",
  timeline: "No scenes absorbed yet. The chronicle fills in as you end scenes.",
};

export default function LedgerView() {
  const { cid = "" } = useParams();
  const [section, setSection] = useState<SectionKey>("facts");
  const [showRetired, setShowRetired] = useState(false);
  const [name, setName] = useState("");
  // Held with the campaign the rows came FROM, the way the panel this replaced
  // held its own: the route is not keyed on `cid`, so a campaign switch keeps
  // this component mounted and a bare `Ledger | null` would go on showing one
  // game's facts under the other's name until the new request settled.
  const [loaded, setLoaded] = useState<{ cid: string; data: Ledger } | null>(null);
  const [changes, setChanges] = useState<{ cid: string; data: RecordChange[] } | null>(null);
  const [standings, setStandings] =
    useState<{ cid: string; data: RelationshipChange[] } | null>(null);
  /** Which pair the timeline is narrowed to, as the id of a `relationships`
   *  row (`a->b` or `a|b`) — "" for all of them. Held as the row's id rather
   *  than the token pair so the `<select>` has a value it can round-trip; the
   *  tokens come back off the row when the read is made. */
  const [pairId, setPairId] = useState("");

  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => setName(c.meta.name)).catch(() => setName(cid));
  }, [cid]);

  useEffect(() => {
    // Superseded responses are dropped rather than raced: two reads can be in
    // flight after a campaign switch, and without this whichever settles LAST
    // wins regardless of which was asked for.
    let live = true;
    api.campaignLedger(cid)
      .then((l) => { if (live) setLoaded({ cid, data: l }); })
      .catch(() => { if (live) setLoaded({ cid, data: EMPTY }); });
    // Its own read, and its own failure: the changes log is a different file
    // behind a different route, and a broken one must cost its section rather
    // than the five the ledger route answers for.
    api.campaignChanges(cid)
      .then((c) => { if (live) setChanges({ cid, data: c }); })
      .catch(() => { if (live) setChanges({ cid, data: [] }); });
    return () => { live = false; };
  }, [cid]);

  const ledger = loaded && loaded.cid === cid ? loaded.data : null;
  const changeRows = changes && changes.cid === cid ? changes.data : null;
  const standingRows = standings && standings.cid === cid ? standings.data : null;

  /** The pair the timeline is narrowed to, resolved from the ledger's own
   *  relationships rows — so a stale id (a campaign switch, a pair the ledger
   *  no longer carries) reads as no filter rather than as an empty one. */
  const pair = useMemo(() => {
    const row = ledger?.relationships.find((r) => r.id === pairId);
    return row ? { a: row.a, b: row.b } : null;
  }, [ledger, pairId]);

  useEffect(() => { setPairId(""); }, [cid]);

  // Its own read, its own failure, and — unlike the other two — its own
  // dependency, because the SERVER does the narrowing. It has to: the route
  // answers with the newest `RELATIONSHIP_HISTORY_PAGE` rows, so filtering the
  // page here would search what the cap has already thrown away, and a long
  // campaign's older arc for one pair would be unreachable in the app while the
  // store still held every row of it.
  useEffect(() => {
    let live = true;
    api.campaignRelationshipHistory(cid, pair ?? undefined)
      .then((h) => { if (live) setStandings({ cid, data: h }); })
      .catch(() => { if (live) setStandings({ cid, data: [] }); });
    return () => { live = false; };
    // `pair` itself rather than its two fields: it is memoized on the ledger
    // and the selected id, so its identity moves only when one of those does —
    // and null when nothing is selected, which is the same null across the
    // ledger's own load, so no read is repeated for it.
  }, [cid, pair]);

  /** How many rows each section stands for, counted the way the section will
   *  actually render — so the facts count follows SHOW RETIRED rather than
   *  disagreeing with the table under it by however many lapsed truths this
   *  campaign has. */
  const counts = useMemo(() => {
    if (!ledger) return null;
    const rows = (k: SectionKey) =>
      rowsFor(k, ledger, changeRows ?? [], standingRows ?? [], showRetired).length;
    return Object.fromEntries(SECTIONS.map((s) => [s.key, rows(s.key)])) as
      Record<SectionKey, number>;
  }, [ledger, changeRows, standingRows, showRetired]);

  /** What this page contributes to ⌘K: its seven sections, so "commitments" is
   *  a thing you can type from anywhere rather than a row you have to be here
   *  to click. */
  const paletteSource = useCallback((): PaletteItem[] =>
    SECTIONS.map((s) => ({
      id: `ledger:${s.key}`, group: "IN THIS CAMPAIGN", label: s.label,
      meta: `ledger · ${counts ? counts[s.key] : "…"}`,
      run: () => setSection(s.key),
    })), [counts]);
  usePaletteSource(paletteSource);

  const current = SECTIONS.find((s) => s.key === section) ?? SECTIONS[0];
  const rows = ledger
    ? rowsFor(section, ledger, changeRows ?? [], standingRows ?? [], showRetired) : [];

  const column = (
    <>
      <Link className="column-back" to={`/campaigns/${cid}`}>‹ {name || "The campaign"}</Link>
      <div className="ledger-ident">
        <div className="eyebrow">What this campaign owes</div>
        <h2 className="ledger-ident-name">{name || cid}</h2>
      </div>
      <ColumnSection label="The ledger">
        {SECTIONS.map((s) => (
          <button key={s.key}
                  className={"column-row" + (section === s.key ? " active" : "")}
                  onClick={() => setSection(s.key)}>
            <span className="column-row-label">{s.label}</span>
            {/* Undefined is "still reading" — a dash says so, where a 0 would
                claim the section is empty. */}
            <span className="column-row-count">{counts ? counts[s.key] : "—"}</span>
          </button>
        ))}
      </ColumnSection>
    </>
  );

  // Narrowing the timeline is a read, not a view filter, so it belongs beside
  // the section rather than inside the table: the options are the pairs the
  // ledger currently carries, which is the only list of them the client has.
  const footer = section === "standings" ? (
    <label className="ledger-toggle">
      <span>Pair</span>
      <select className="ledger-pair" value={pairId}
              onChange={(e) => setPairId(e.target.value)}
              aria-label="Narrow the timeline to one pair">
        <option value="">Everyone</option>
        {(ledger?.relationships ?? []).map((r) => (
          <option key={r.id} value={r.id}>
            {`${r.a_name} ${r.kind === "bond" ? "↔" : "→"} ${r.b_name}`}
          </option>
        ))}
      </select>
    </label>
  ) : (
    <label className="ledger-toggle">
      <input type="checkbox" checked={showRetired}
             onChange={(e) => setShowRetired(e.target.checked)} />
      <span>Show retired</span>
    </label>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="Ledger sections">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow">{current.eyebrow}</div>
            <h1 className="screen-title">{current.label}</h1>
          </div>
        </div>

        {ledger === null && <p className="column-empty">Reading the ledger…</p>}

        {ledger !== null && rows.length === 0 && (
          <p className="empty-state">
            {/* A narrowed timeline with nothing in it is a different sentence
                from a campaign that has recorded nothing: pointing the reader
                back to play would be answering a question they did not ask. */}
            <span className="empty-what">
              {section === "standings" && pair
                ? "Nothing has passed between these two yet. Pick Everyone to see "
                  + "the whole timeline."
                : NOTHING[section]}
            </span>{" "}
            {!(section === "standings" && pair)
              && <Link to={`/campaigns/${cid}`}>Back to play →</Link>}
          </p>
        )}

        {rows.length > 0 && (
          <div className="ledger-table-wrap">
            <table className="ledger-table">
              <colgroup>
                <col className="ledger-col-id" />
                <col />
                <col className="ledger-col-asof" />
                <col className="ledger-col-scene" />
              </colgroup>
              <thead>
                <tr>
                  {current.columns.map((c, i) => (
                    // The mark column's heading is empty for six of the seven
                    // sections, and an empty <th> is a column with no name for
                    // a screen reader rather than one it can skip.
                    <th key={i} scope="col">{c || <span className="sr-only">Row</span>}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={r.retired ? "retired" : undefined}>
                    <td className={"ledger-mark" + (r.alert ? " alert" : "")}>{r.mark}</td>
                    <td>
                      <div className="ledger-what">{r.what}</div>
                      {r.note && <div className="ledger-note">{r.note}</div>}
                    </td>
                    <td className="ledger-asof">{r.asOf}</td>
                    {/* A scene id is a filename; the title is what the reader
                        named it. Long ones are clipped rather than wrapped, so
                        the column stays a column — the full text is on hover. */}
                    <td className="ledger-scene" title={r.scene}>{r.scene}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {section === "facts" && rows.some((r) => r.retired) && (
          <p className="ledger-lead">
            A retired fact keeps its row, struck through and dated, with the fact that
            replaced it named — the supersession chain is the point of the ledger, so
            hiding it behind a toggle by default would hide the history it exists to keep.
          </p>
        )}
      </div>
    </PageShell>
  );
}
