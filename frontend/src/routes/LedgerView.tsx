import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { agingLabel } from "../aging";
import { errorText } from "../api/errors";
import {
  api, type Ledger, type RecordChange, type RelationshipChange, type RetiredFact,
  type StandingFact,
} from "../api/client";
import { LedgerRowEditor, type Draft } from "../components/ledger/LedgerRowEditor";
import {
  blankSpec, chronicleSpec, commitmentSpec, factSpec, relationshipSpec, threadSpec,
  type EditSpec,
} from "../components/ledger/specs";
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
  /** What this row is, when it is a record that can be changed by hand.
   *
   *  Absent on the two sections that are LOGS — the relationship history and
   *  the change list record what happened, and editing a log falsifies history
   *  rather than correcting state. Undo is how something in them is reversed,
   *  and it already exists. */
  edit?: EditSpec;
};

const SECTIONS: { key: SectionKey; label: string; eyebrow: string;
                  columns: [string, string, string, string] }[] = [
  // "NEVER EDITED" was the rule until the ledger became hand-editable, and it
  // was always a rule about the WRITER rather than the record: the absorb pass
  // retires and supersedes, and the person whose campaign it is may correct.
  { key: "facts", label: "Standing facts", eyebrow: "DATED TRUTHS · RETIRED BY PLAY, CORRECTED BY YOU",
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
      key: f.id, mark: f.id, retired, what: f.text, edit: factSpec(f, retired),
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
      key: t.id, mark: "▸", what: t.title, edit: threadSpec(t),
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
      what: c.title, edit: commitmentSpec(c),
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
      edit: relationshipSpec(r),
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
    edit: chronicleSpec(e.id, e.one_line, e.date),
  }));
}

/** Which sections can have a record written into them by hand, and what to
 *  call the thing being written. The timeline is absent because its rows belong
 *  to scenes; the two logs are absent because they record what happened. */
const NEW_IN: Partial<Record<SectionKey, "thread" | "commitment" | "fact" | "relationship">> = {
  facts: "fact", threads: "thread", commitments: "commitment", relationships: "relationship",
};
const NEW_LABEL: Partial<Record<SectionKey, string>> = {
  facts: "fact", threads: "thread", commitments: "commitment", relationships: "standing",
};

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
  /** The row whose editor is open, by `<kind>:<id>`. One at a time, and the
   *  same reason the character page holds one field open: these writes are
   *  whole-record, so two editors is one of them losing. */
  const [openRow, setOpenRow] = useState<string | null>(null);
  /** A `+ New …` in progress, or null. Kept apart from `openRow` so opening a
   *  create does not look like editing whichever row shares its id ("" for a
   *  record that has none yet). */
  const [creating, setCreating] = useState<EditSpec | null>(null);
  const [busy, setBusy] = useState(false);
  /** A refusal from the server, shown inside the editor it belongs to — a 409
   *  on a fact somebody else already retired is about that row, and at the top
   *  of the page it would be about nothing in particular. */
  const [writeError, setWriteError] = useState<string | null>(null);
  /** Bumped after every hand edit so the ledger, the change log and the
   *  relationship timeline are all re-read: one edit can move rows in three of
   *  them at once (a retired fact leaves `facts` and joins `retired`, and every
   *  edit adds a row to Recent changes). */
  const [epoch, setEpoch] = useState(0);

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
  }, [cid, epoch]);

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
  // An editor belongs to one row of one section: carried across either it
  // would be aimed at whatever happens to share its id.
  useEffect(() => {
    setOpenRow(null);
    setCreating(null);
    setWriteError(null);
  }, [cid, section]);

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
  }, [cid, pair, epoch]);

  /** Run one hand edit, then re-read everything it could have moved.
   *
   *  Every write here is journalled server-side as a MANUAL edit and is
   *  reversible through the ordinary undo route, which is why nothing in this
   *  component keeps an undo of its own — the reversal lives in the play
   *  view's Changes panel, under History, where every journalled change
   *  already does.
   *
   *  Note that this is NOT the ledger's own Recent-changes section: that one
   *  reads `store/changes.py`, the rolling per-record view of what the last
   *  absorb wrote. A hand edit does not belong in it, because that log is
   *  about what the pass extracted.
   */
  async function run(write: () => Promise<unknown>, thenClose = true) {
    // Single-flight: `busy` disables every control, but a keyboard submit that
    // beat the re-render would otherwise start a second write.
    if (busy) return;
    setBusy(true);
    setWriteError(null);
    try {
      await write();
      if (thenClose) { setOpenRow(null); setCreating(null); }
      setEpoch((n) => n + 1);
    } catch (err: unknown) {
      setWriteError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const num = (d: Draft, k: string) => {
    // A meter is typed, so it can hold anything; the store clamps, and NaN
    // must not reach it as `null`.
    const n = Number(d[k]);
    return Number.isFinite(n) ? Math.max(0, Math.min(5, Math.round(n))) : 0;
  };

  /** Save one record, whichever kind it is. `spec.id` empty means a create.
   *
   *  `d` carries ONLY the fields the reader changed (`LedgerRowEditor` filters
   *  it), and that is what makes a save safe against an absorb landing while
   *  the editor was open: every store mutator behind these reads an absent
   *  field as "keep what is stored", so a title fix cannot revert a status the
   *  pass advanced in the meantime. A field the reader emptied is present and
   *  blank, which is how a deadline is cleared.
   *
   *  A create is the exception and passes everything, because there is nothing
   *  stored to keep.
   */
  function save(spec: EditSpec, d: Draft) {
    const isNew = !spec.id;
    const full = isNew ? { ...spec.initial, ...d } : d;
    void run(() => {
      if (spec.kind === "thread") {
        return isNew
          ? api.ledgerCreateThread(cid, {
            title: full.title ?? "", status: full.status ?? "",
            beat: full.beat ?? "", scene: full.scene ?? "" })
          : api.ledgerSaveThread(cid, spec.id, d);
      }
      if (spec.kind === "commitment") {
        return isNew
          ? api.ledgerCreateCommitment(cid, {
            title: full.title ?? "", kind: full.kind ?? "", status: full.status ?? "",
            due: full.due ?? "", beat: full.beat ?? "", scene: full.scene ?? "" })
          : api.ledgerSaveCommitment(cid, spec.id, d);
      }
      if (spec.kind === "fact") {
        return isNew
          ? api.ledgerRecordFact(cid, { text: full.text ?? "", date: full.date ?? "",
                                        scene: full.scene ?? "" })
          // Only what moved. The lifecycle is not here either way: retiring is
          // its own action, and says something different.
          : api.ledgerSaveFact(cid, spec.id, d);
      }
      if (spec.kind === "chronicle") {
        return api.ledgerSaveChronicleLine(cid, spec.id, d);
      }
      // A relationship, either shape. On a create the two actors come out of
      // the form; on an edit they are the row's own, since the pair is the
      // record's identity and is not editable into somebody else. The meters
      // are sent only when they moved — the route merges the rest from the
      // stored record rather than defaulting them to zero.
      const a = isNew ? (full.a ?? "") : (spec.pair?.a ?? "");
      const b = isNew ? (full.b ?? "") : (spec.pair?.b ?? "");
      const bond = (full.bond ?? "").trim();
      if (bond || spec.kind === "bond") {
        return api.ledgerSaveRelationship(cid, { a, b, bond, scene: full.scene ?? "" });
      }
      const meters: Record<string, number> = {};
      for (const k of ["trust", "affection", "tension"]) {
        if (isNew || k in d) meters[k] = num(full, k);
      }
      return api.ledgerSaveRelationship(cid, {
        a, b, ...meters, ...(isNew || "note" in d ? { note: full.note ?? "" } : {}),
      });
    });
  }

  function remove(spec: EditSpec) {
    void run(() => {
      if (spec.kind === "thread") return api.ledgerDeleteThread(cid, spec.id);
      if (spec.kind === "commitment") return api.ledgerDeleteCommitment(cid, spec.id);
      if (spec.kind === "fact") return api.ledgerDeleteFact(cid, spec.id);
      return api.ledgerDeleteRelationship(cid, spec.pair?.a ?? "", spec.pair?.b ?? "",
                                          spec.kind === "bond");
    });
  }

  /** The one-click ending for a kind of record: close a thread, mark a
   *  commitment done, retire a fact. On the row rather than inside the editor
   *  because it is the common case and it is the thing the reader is looking
   *  at — opening a form to change one word is a form too many. */
  function quick(spec: EditSpec) {
    void run(() => {
      if (spec.kind === "thread") return api.ledgerSaveThread(cid, spec.id, { status: "closed" });
      if (spec.kind === "commitment") {
        return api.ledgerSaveCommitment(cid, spec.id, { status: "fulfilled" });
      }
      return api.ledgerRetireFact(cid, spec.id);
    }, false);
  }

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
  // The two logs get no actions column at all rather than an empty one: a
  // column of blanks reads as a feature that failed to load.
  const editable = section !== "standings" && section !== "changes";
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
          {/* Only the four sections that hold records somebody can write. The
              timeline's rows belong to scenes, and the two logs record what
              happened — there is nothing to add to either by hand. */}
          {NEW_IN[section] && (
            <button className="subtle" type="button" disabled={busy || !!creating}
                    onClick={() => {
                      setOpenRow(null);
                      setWriteError(null);
                      setCreating(blankSpec(NEW_IN[section]!));
                    }}>
              + New {NEW_LABEL[section]}
            </button>
          )}
        </div>

        {creating && (
          <div className="ledger-create">
            <div className="eyebrow">New {NEW_LABEL[section]}</div>
            <LedgerRowEditor fields={creating.fields} initial={creating.initial}
                             busy={busy} error={writeError}
                             onSave={(d) => save(creating, d)}
                             onCancel={() => { setCreating(null); setWriteError(null); }} />
          </div>
        )}

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
                {editable && <col className="ledger-col-do" />}
              </colgroup>
              <thead>
                <tr>
                  {current.columns.map((c, i) => (
                    // The mark column's heading is empty for six of the seven
                    // sections, and an empty <th> is a column with no name for
                    // a screen reader rather than one it can skip.
                    <th key={i} scope="col">{c || <span className="sr-only">Row</span>}</th>
                  ))}
                  {editable && <th scope="col"><span className="sr-only">Actions</span></th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const key = r.edit ? `${r.edit.kind}:${r.edit.id}` : "";
                  const open = !!r.edit && openRow === key;
                  return (
                    <Fragment key={r.key}>
                      <tr className={(r.retired ? "retired" : "") + (open ? " editing" : "")}>
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
                        {editable && (
                          <td className="ledger-do">
                            {r.edit?.quick && (
                              <button className="row-do" type="button" disabled={busy}
                                      title={r.edit.quick.title}
                                      onClick={() => quick(r.edit!)}>
                                {r.edit.quick.label}
                              </button>
                            )}
                            {r.edit && (
                              /* The label names the row, because a table of
                                 identical "Edit" buttons is a list of identical
                                 buttons to a screen reader. The visible word
                                 does NOT change when the editor is open —
                                 "Close" would collide with a thread's own Close
                                 action two buttons away, and `aria-expanded`
                                 already says which state it is in. */
                              <button className="row-do" type="button" disabled={busy}
                                      aria-expanded={open}
                                      aria-label={`Edit ${typeof r.what === "string" ? r.what : r.key}`}
                                      onClick={() => {
                                        setCreating(null);
                                        setWriteError(null);
                                        setOpenRow(open ? null : key);
                                      }}>
                                Edit
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                      {open && r.edit && (
                        <tr className="ledger-editor-row">
                          <td colSpan={5}>
                            <LedgerRowEditor
                              fields={r.edit.fields} initial={r.edit.initial}
                              busy={busy} error={writeError}
                              onSave={(d) => save(r.edit!, d)}
                              onCancel={() => { setOpenRow(null); setWriteError(null); }}
                              onDelete={r.edit.deletable ? () => remove(r.edit!) : undefined} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
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
