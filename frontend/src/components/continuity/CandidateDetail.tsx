/** One finding, read-only, with its explicit actions (capstone §12.2–§12.4).
 *
 *  The list/detail pattern: `.detail-main` shows what was found -- the records
 *  as they are now, their beats, the signals and the hedged proposal -- and
 *  `.detail-sidebar` holds the actions and the finding's metadata. Nothing is
 *  applied by opening it: every write is a button, and an action that needs
 *  input (a closing beat, a temporal relation) reveals its form only once it
 *  is chosen.
 *
 *  NEVER A RAW ID. A record is named by its title and links to its Ledger row;
 *  a scene, an actor or a dated event is named from the read's `names` (or its
 *  scene list), so a scene filename or `characters:mara` never reaches the
 *  reader. One the read could not name is left out rather than shown raw; a
 *  scene that exists but has no title is "Untitled scene".
 *
 *  TWO SIDES ARE NEVER ONE NAME. A same-title pair is the commonest duplicate
 *  there is, so when both records carry one title each is told apart by its
 *  place ("(first)", "(second)") -- in its heading in main and in every action
 *  that names it -- since a Keep the reader cannot attribute is a merge they
 *  cannot choose.
 *
 *  A stale finding has every action disabled: its records moved since the
 *  sweep found it, so acting on it would be acting on a meaning nobody has
 *  looked at. A Refresh beside the sentence is the way forward.
 */
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type {
  CandidateRecord, ContinuityApply, ContinuityCandidate,
} from "../../api/types";
import { ledgerHref } from "../../ledgerPaths";
import { encodeSegment } from "../../urlSegment";
import { KIND_PHRASES, proposalLabel, STALE_SENTENCES } from "./labels";

/** A refusal shown in the sidebar, with the one way forward it may offer
 *  ("Merge anyway"). */
export type DetailError = { text: string; action?: { label: string; run: () => void } };

/** The four temporal relations a commitment can take to a dated event. */
const TEMPORAL = ["before", "on", "after", "by"] as const;
const RESOLUTIONS = [
  { status: "fulfilled", label: "Fulfilled" },
  { status: "broken", label: "Broken" },
  { status: "expired", label: "Expired" },
] as const;

/** What an action needs before it can be applied. */
type Form =
  | { kind: "close" }
  | { kind: "resolve"; status: string; label: string }
  | { kind: "temporal" };

const typeOf = (ref: string) => ref.slice(0, Math.max(ref.indexOf(":"), 0));
const physicalId = (ref: string) => ref.slice(ref.indexOf(":") + 1);

/** A record's title, or what it is when it has none -- never its ref. */
function titleOf(r: CandidateRecord | undefined, fallback: string): string {
  return r?.title || fallback;
}

const ORDINALS = ["first", "second", "third"];

/** Each record's title as this finding shows it: told apart by its place when
 *  another record in the finding has the same one. */
function toldApart(records: CandidateRecord[]): CandidateRecord[] {
  return records.map((r, i) =>
    r.title && records.some((o, j) => j !== i && o.title === r.title)
      ? { ...r, title: `${r.title} (${ORDINALS[i] ?? String(i + 1)})` }
      : r);
}

/** What the action form's state was chosen against, as a key for the detail.
 *
 *  The form (which action is open, the beat, the evidence scene, the temporal
 *  relation, the due copy) is the reader's input against ONE reading of the
 *  finding. Its defaults come from the proposal (its decision, relation and
 *  evidence scenes) and the due copy from the records' dates, which the
 *  fingerprint covers. A refresh that moves any of them -- the same id back
 *  with a new fingerprint or a re-asked proposal -- opens the form fresh, so a
 *  choice made against the old finding is never applied to the new one. A
 *  re-read that changes none of them keeps a half-filled form. */
export function formKey(candidate: ContinuityCandidate): string {
  const p = candidate.proposal;
  return JSON.stringify([candidate.id, candidate.fingerprint, p?.decision ?? null,
                         p?.relation ?? null, p?.evidence_scenes ?? []]);
}

type Props = {
  cid: string;
  candidate: ContinuityCandidate;
  names: Record<string, string>;
  scenes: { id: string; title: string }[];
  busy: boolean;
  /** A sweep is in hand, so the Refresh beside a stale finding is off. */
  refreshing?: boolean;
  error: DetailError | null;
  onApply: (body: ContinuityApply) => void;
  onDismiss: (decision: "dismiss" | "keep_open") => void;
  onBack: () => void;
  onRefresh: () => void;
};

export function CandidateDetail(props: Props) {
  const { cid, names, scenes, busy, refreshing, error } = props;
  const candidate = { ...props.candidate, records: toldApart(props.candidate.records) };
  const [form, setForm] = useState<Form | null>(null);
  const [beat, setBeat] = useState("");
  const firstEvidence = (candidate.proposal?.evidence_scenes ?? [])
    .find((sid) => scenes.some((s) => s.id === sid)) ?? "";
  const [scene, setScene] = useState(firstEvidence);
  const proposed = [candidate.proposal?.relation, candidate.proposal?.decision]
    .find((w): w is (typeof TEMPORAL)[number] =>
      (TEMPORAL as readonly (string | undefined)[]).includes(w));
  const [relation, setRelation] = useState<string>(proposed ?? "before");
  const [copyDue, setCopyDue] = useState(false);

  const stale = candidate.stale;
  const off = busy || stale;
  /** A scene's display name, or null for one nobody can name. */
  const sceneName = (sid: string): string | null => {
    if (names[sid]) return names[sid];
    const listed = scenes.find((s) => s.id === sid);
    return listed ? listed.title || "Untitled scene" : null;
  };
  const sceneLink = (sid: string) => {
    const label = sceneName(sid);
    return label
      ? <Link key={sid} to={`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(sid)}`}>
          {label}
        </Link>
      : null;
  };

  const expect = { expect_fingerprint: candidate.fingerprint };
  const [A, B] = candidate.records;
  const nameA = titleOf(A, "the first record");
  const nameB = titleOf(B, "the second record");

  /** The explicit due copy (§5.1): offered only when one side of a merge has
   *  a due and the other has none, and sent only with the Keep that keeps the
   *  side without it. */
  const due = (() => {
    if (candidate.kind !== "possible_duplicate" || !A || !B) return null;
    if (A.due && !B.due) return { target: B, value: A.due, name: nameB };
    if (B.due && !A.due) return { target: A, value: B.due, name: nameA };
    return null;
  })();

  /** What kind the merged commitment keeps (§5.1, §7.1): the canonical's
   *  kind overrides the source's, so where the two differ the reader is told
   *  which one each Keep leaves in the play prompt. */
  const kinds = candidate.kind === "possible_duplicate" && A?.commitment_kind
    && B?.commitment_kind && A.commitment_kind !== B.commitment_kind
    ? `The merged commitment takes the kept one's kind: ${A.commitment_kind} if you keep `
      + `${nameA}, ${B.commitment_kind} if you keep ${nameB}.`
    : null;

  /** The finding's actions, by its shape (§12.3, §12.4). Each is keyed by
   *  what it does, not by its label: a label is for the reader. */
  function actions(): { key: string; label: string; run: () => void }[] {
    const dismiss = { key: "dismiss", label: "Dismiss", run: () => props.onDismiss("dismiss") };
    const link = (from: string, to: string, rel: string, label: string) => ({
      key: `link\u0000${from}\u0000${to}\u0000${rel}`, label,
      run: () => props.onApply({ op: "link", from, to, relation: rel, ...expect }),
    });
    const keepOpen = { key: "keep_open", label: "Keep open",
                       run: () => props.onDismiss("keep_open") };
    const dismissFinding = { key: "dismiss", label: "Dismiss finding",
                             run: () => props.onDismiss("dismiss") };
    if (candidate.kind === "possible_thread_closure") {
      return [
        { key: "close", label: "Close thread", run: () => setForm({ kind: "close" }) },
        keepOpen, dismissFinding,
      ];
    }
    if (candidate.kind === "possible_commitment_resolution") {
      return [
        ...RESOLUTIONS.map(({ status, label }) => ({
          key: `resolve\u0000${status}`, label,
          run: () => setForm({ kind: "resolve", status, label }),
        })),
        keepOpen, dismissFinding,
      ];
    }
    if (!A || !B) return [dismiss];
    const related = link(A.ref, B.ref, "related_to", "Related");
    if (candidate.kind === "possible_relation") {
      const event = candidate.records.find((r) => typeOf(r.ref) === "event");
      if (event || candidate.signals.reason === "temporal") {
        return [{ key: "temporal", label: "Accept", run: () => setForm({ kind: "temporal" }) },
                related, dismiss];
      }
      // Thread → commitment, whichever side the finding listed first (§5.3).
      const thread = typeOf(A.ref) === "thread" ? A : B;
      const owed = thread === A ? B : A;
      return [link(thread.ref, owed.ref, "pays_off", "Pays off"), related, dismiss];
    }
    const keep = (kept: CandidateRecord, label: string) => ({
      key: `alias\u0000${kept.ref}`,
      label: `Keep ${label}`,
      run: () => props.onApply({
        op: "alias", canonical: kept.ref,
        ...(copyDue && due && due.target === kept ? { copy_due: true } : {}),
        ...expect,
      }),
    });
    const merges = [keep(A, nameA), keep(B, nameB)];
    // `continues` and `subthread_of` join threads only (§5.3).
    const threads = typeOf(A.ref) === "thread" && typeOf(B.ref) === "thread" ? [
      link(A.ref, B.ref, "continues", `${nameA} continues ${nameB}`),
      link(B.ref, A.ref, "continues", `${nameB} continues ${nameA}`),
      link(A.ref, B.ref, "subthread_of", `${nameA} is a subthread of ${nameB}`),
      link(B.ref, A.ref, "subthread_of", `${nameB} is a subthread of ${nameA}`),
    ] : [];
    return [...merges, ...threads, related, dismiss];
  }

  function applyForm() {
    if (!form) return;
    if (form.kind === "temporal") {
      const event = candidate.records.find((r) => typeOf(r.ref) === "event");
      const owed = candidate.records.find((r) => typeOf(r.ref) === "commitment");
      if (!event || !owed) return;
      props.onApply({ op: "link", from: owed.ref, to: event.ref, relation, ...expect });
      return;
    }
    const written = beat.trim() ? { beat: beat.trim(), scene } : {};
    props.onApply(form.kind === "close"
      ? { op: "close", ...written, ...expect }
      : { op: "resolve", status: form.status, ...written, ...expect });
  }

  const sig = candidate.signals;
  const named = (refs: string[] | undefined) =>
    (refs ?? []).map((r) => names[r]).filter((n): n is string => !!n);
  const actors = named(sig.shared_actors);
  const anchors = named(sig.shared_anchors);
  const signals = [
    sig.title_exact && "Same title",
    sig.slug_equal && "Same slug",
    actors.length > 0 && `Shared characters: ${actors.join(", ")}`,
    anchors.length > 0 && `Shared dates: ${anchors.join(", ")}`,
    (sig.shared_scenes?.length ?? 0) > 0 && `Shared scenes: ${sig.shared_scenes!.length}`,
    typeof sig.cosine === "number"
      && `Meaning overlap ${sig.cosine.toFixed(2)} — a discovery signal, not confidence`,
    sig.reason === "stale" && (typeof sig.days_since === "number"
      ? `Quiet for ${sig.days_since} days` : "Quiet for a while"),
    sig.reason === "overdue" && "Past its due date",
    sig.reason === "touched" && "Moved by a recent scene",
    sig.reason === "temporal" && (typeof sig.in_days === "number"
      ? `A dated event in ${sig.in_days} days` : "A dated event is near"),
  ].filter((s): s is string => typeof s === "string");

  const label = proposalLabel(candidate.proposal?.decision);
  const evidence = (candidate.proposal?.evidence_scenes ?? [])
    .filter((sid, i, all) => all.indexOf(sid) === i && sceneName(sid) !== null);
  const pressures = candidate.records
    .filter((r) => r.pressure?.friendly)
    .map((r) => `${titleOf(r, "This record")}: ${r.pressure!.friendly}`);
  const needsScene = !!beat.trim() && !scene;

  return (
    <div className="detail-view continuity-detail">
      <div className="detail-main">
        <button type="button" className="subtle continuity-back" onClick={props.onBack}>
          ‹ All findings
        </button>
        <h3>{candidate.records.map((r) => titleOf(r, "Untitled")).join(" / ")}</h3>

        {candidate.records.map((r) => (
          <RecordView key={r.ref} cid={cid} record={r} sceneLink={sceneLink} />
        ))}

        {signals.length > 0 && (
          <div className="continuity-signals">
            <h4>Why it was found</h4>
            <ul>{signals.map((s) => <li key={s}>{s}</li>)}</ul>
          </div>
        )}

        {(label || candidate.proposal?.reason) && (
          <div className="continuity-proposal">
            {label && <p className="continuity-proposal-label">{label}</p>}
            {candidate.proposal?.reason && (
              <p className="field-hint">{candidate.proposal.reason}</p>
            )}
            {evidence.length > 0 && (
              <p className="continuity-evidence">
                <span>Evidence: </span>
                {evidence.map((sid, i) => <span key={sid}>{i > 0 && ", "}{sceneLink(sid)}</span>)}
              </p>
            )}
          </div>
        )}
      </div>

      <aside className="detail-sidebar" aria-label="Finding actions">
        {stale && (
          <div className="continuity-stale-note">
            <p>{STALE_SENTENCES[candidate.stale_reason ?? "records"]}</p>
            <button type="button" className="subtle" disabled={refreshing}
                    onClick={props.onRefresh}>Refresh</button>
          </div>
        )}
        <div className="form-actions continuity-actions">
          {actions().map((a) => (
            <button key={a.key} type="button" disabled={off} onClick={a.run}>{a.label}</button>
          ))}
        </div>
        {kinds && <p className="field-hint continuity-kinds">{kinds}</p>}
        {due && (
          <label className="continuity-due">
            <input type="checkbox" checked={copyDue} disabled={off}
                   onChange={(e) => setCopyDue(e.target.checked)} />
            <span>Also copy the due date “{due.value}” to {due.name}</span>
          </label>
        )}
        {form && !stale && (
          <div className="continuity-form">
            {form.kind === "temporal" ? (
              <label>
                <span>{owedName(candidate)} is due</span>
                <select aria-label="Relation" value={relation} disabled={busy}
                        onChange={(e) => setRelation(e.target.value)}>
                  {TEMPORAL.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
            ) : (
              <>
                <div className="field-hint">
                  {form.kind === "close" ? "Close thread" : form.label}: a closing beat is
                  optional, and is never written unless you write it.
                </div>
                <textarea aria-label="Closing beat" rows={3} value={beat} disabled={busy}
                          onChange={(e) => setBeat(e.target.value)} />
                <select aria-label="Evidence scene" value={scene} disabled={busy}
                        onChange={(e) => setScene(e.target.value)}>
                  <option value="">No scene</option>
                  {scenes.map((s) => <option key={s.id} value={s.id}>{s.title || "Untitled scene"}</option>)}
                </select>
                {needsScene && <p className="field-hint">A closing beat needs its scene.</p>}
              </>
            )}
            <div className="form-actions">
              <button type="button" disabled={busy || needsScene} onClick={applyForm}>Apply</button>
              <button type="button" className="subtle" disabled={busy}
                      onClick={() => setForm(null)}>Cancel</button>
            </div>
          </div>
        )}
        {error && (
          <div className="continuity-error" role="alert">
            <p>{error.text}</p>
            {error.action && (
              <button type="button" disabled={busy} onClick={error.action.run}>
                {error.action.label}
              </button>
            )}
          </div>
        )}
        <div className="side-section">
          <h4>Finding</h4>
          <span className="chip on">{KIND_PHRASES[candidate.kind]}</span>
        </div>
        {pressures.length > 0 && (
          <div className="side-section">
            <h4>Pressure</h4>
            {pressures.map((p) => <p key={p} className="field-hint">{p}</p>)}
          </div>
        )}
        {candidate.created && (
          <div className="side-section">
            <h4>Found</h4>
            <p className="field-hint">{candidate.created.slice(0, 10)}</p>
          </div>
        )}
      </aside>
    </div>
  );
}

/** The commitment a temporal pairing is about, by title. */
function owedName(candidate: ContinuityCandidate): string {
  const owed = candidate.records.find((r) => typeOf(r.ref) === "commitment");
  return titleOf(owed, "The commitment");
}

const TYPE_LABELS: Record<string, string> = {
  thread: "Plot thread", commitment: "Commitment", event: "Dated event",
};

/** One record as it is now: its title (a link to its Ledger row), a
 *  commitment's own kind, status, dates and beats. */
function RecordView(
  { cid, record, sceneLink }: {
    cid: string; record: CandidateRecord;
    sceneLink: (sid: string) => ReactNode;
  },
) {
  const type = typeOf(record.ref);
  const section = type === "thread" ? "threads" : type === "commitment" ? "commitments" : null;
  const title = titleOf(record, `Untitled ${(TYPE_LABELS[type] ?? "record").toLowerCase()}`);
  const beats = record.beats.length
    ? record.beats
    : record.latest_beat ? [{ scene: "", text: record.latest_beat }] : [];
  const last = record.last_scene?.id ? sceneLink(record.last_scene.id) : null;
  return (
    <section className="continuity-record">
      <div className="continuity-record-head">
        {section && !record.gone
          ? <Link className="chip" to={ledgerHref(cid, { section, row: physicalId(record.ref) })}>
              {title}
            </Link>
          : <span className="chip on">{title}</span>}
        <span className="continuity-item-meta">
          {TYPE_LABELS[type] ?? "Record"}
          {record.commitment_kind && ` (${record.commitment_kind})`}
          {record.status && ` · ${record.status}`}
        </span>
      </div>
      {record.gone && <p className="field-hint">No longer a current record.</p>}
      {record.due && (
        <p className="field-hint">{type === "event" ? "On" : "Due"} {record.due}</p>
      )}
      {last && <p className="field-hint">Last scene: {last}</p>}
      {beats.length > 0 && (
        <ul className="continuity-beats">
          {beats.map((b) => (
            <li key={`${b.scene}\u0000${b.text}`}>
              <span>{b.text}</span>
              {b.scene && sceneLink(b.scene) && <> — {sceneLink(b.scene)}</>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default CandidateDetail;
