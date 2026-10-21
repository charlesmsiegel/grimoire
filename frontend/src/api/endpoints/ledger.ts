/** The campaign's history: changes, journal, provenance, the ledger and its
 *  hand edits, knowledge overrides, and the continuity review. Spread into
 *  `api` by `client.ts`. */
import { notifyShell, notifyShellOnLanded } from "../notify";
import { awaitRunAt, draftBase, draftRun, findDraftRun, followDraft } from "../runs";
import type { RunHandle } from "../stream";
import { ApiError, RefreshRefused, request } from "../transport";
import type { CandidateInvestigated, CandidateRecord, ChronicleLineSave, CommitmentSave,
  ContinuityApplied, ContinuityApply, ContinuityCandidates, ContinuityState, DriversSnapshot,
  FactRecord, FactSave, HistoryAnswer, JournalEntry, KnowledgeEdit, KnowledgeList,
  KnowledgeSave, KnowledgeScene, Ledger, Provenance, ReconcileResult, RecordChange,
  RelationshipChange, RelationshipSave, StoryGraph, ThreadSave, Timeline } from "../types";
import { encodeSegment } from "../../urlSegment";

export const ledgerApi = {
  // `fresh` for the caller re-reading *because* an undo just repointed one of
  // these deltas: handed a promise started before that write, it would conclude
  // the reversal never happened.
  campaignChanges: (cid: string, fresh = false) =>
    request<RecordChange[]>("GET", `/api/campaigns/${cid}/changes`, undefined, { fresh }),
  // `fresh`, for the reason the ledger below opts out of sharing: the history
  // is re-read precisely when it has just grown — an absorb save, or an undo
  // this same panel performed — and a shared in-flight promise would answer
  // that with the list as it was before the row appeared.
  campaignJournal: (cid: string) =>
    request<JournalEntry[]>("GET", `/api/campaigns/${cid}/journal`, undefined, { fresh: true }),
  undoJournalEntry: (cid: string, jid: string) =>
    request<{ ok: boolean; entry: JournalEntry }>(
      "POST", `/api/campaigns/${cid}/journal/${jid}/undo`),
  // Never shared with an in-flight read of the same path: the ledger is re-read
  // precisely when the records behind it have moved (an absorb save, a scene
  // rename), and the dedupe would answer that with the pre-change response.
  // It has one consumer, so the sharing it opts out of was buying nothing.
  // Not `fresh`: this is a rolling log of citations for values that are already
  // on screen, so a copy one absorb old is stale about a line the reader has
  // not been shown yet either.
  campaignProvenance: (cid: string) =>
    request<Provenance>("GET", `/api/campaigns/${cid}/provenance`),
  campaignLedger: (cid: string) =>
    request<Ledger>("GET", `/api/campaigns/${cid}/ledger`, undefined, { fresh: true }),

  /** Hand edits to the ledger (`routes/ledger.py`).
   *
   *  Every one of these is journalled as a MANUAL edit and is reversible
   *  through the ordinary undo route (`campaignUndo`), which is why nothing
   *  here needs an undo of its own: the row appears in the play view's Changes
   *  panel under History, labelled "edited by hand".
   *
   *  Only records, never logs. There is deliberately nothing here that writes
   *  the relationship history or the change log: those record what happened,
   *  and editing them would falsify history rather than correct state.
   */
  ledgerCreateThread: (cid: string, body: ThreadSave) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/ledger/threads`, body),
  ledgerSaveThread: (cid: string, pid: string, body: ThreadSave) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/threads/${pid}`, body),
  /** `force` deletes a record that has records merged into it, un-merging
   *  them (§5.7); without it the route refuses with `has_merged_records`. */
  ledgerDeleteThread: (cid: string, pid: string, force = false) =>
    request<{ ok: boolean }>(
      "DELETE", `/api/campaigns/${cid}/ledger/threads/${pid}${force ? "?force=true" : ""}`),

  ledgerCreateCommitment: (cid: string, body: CommitmentSave) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/ledger/commitments`, body),
  ledgerSaveCommitment: (cid: string, mid: string, body: CommitmentSave) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/commitments/${mid}`, body),
  ledgerDeleteCommitment: (cid: string, mid: string, force = false) =>
    request<{ ok: boolean }>(
      "DELETE", `/api/campaigns/${cid}/ledger/commitments/${mid}${force ? "?force=true" : ""}`),

  ledgerRecordFact: (cid: string, body: FactRecord) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/ledger/facts`, body),
  /** The user's correction, and the only caller of `facts.set_text` in the app:
   *  grimoire never edits a fact, the person whose campaign it is may. Each
   *  field is omitted to leave it alone. */
  ledgerSaveFact: (cid: string, fid: string, body: FactSave) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/facts/${fid}`, body),
  /** Ends a fact with nothing replacing it. 409 when it is already retired,
   *  which the caller shows rather than swallowing. */
  ledgerRetireFact: (cid: string, fid: string, scene = "") =>
    request<{ ok: boolean }>("POST", `/api/campaigns/${cid}/ledger/facts/${fid}/retire`,
                             { scene }),
  ledgerDeleteFact: (cid: string, fid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/ledger/facts/${fid}`),

  ledgerSaveRelationship: (cid: string, body: RelationshipSave) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/relationships`, body),
  /** `bond` addresses the undirected record; without it this removes the
   *  feeling `a` holds toward `b` and leaves the other direction alone. */
  ledgerDeleteRelationship: (cid: string, a: string, b: string, bond = false) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/api/campaigns/${cid}/ledger/relationships`
      + `?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&bond=${bond}`),

  ledgerSaveChronicleLine: (cid: string, sid: string, body: ChronicleLineSave) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/chronicle/${sid}`, body),

  /** Knowledge overrides (roadmap 11). `fresh`, like the ledger: re-read right
   *  after a write, and a shared read could be older than the write. */
  ledgerKnowledge: (cid: string) =>
    request<KnowledgeList>("GET", `/api/campaigns/${cid}/ledger/knowledge`, undefined,
                           { fresh: true }),
  ledgerKnowledgeScene: (cid: string, sid: string) =>
    request<KnowledgeScene>("GET", `/api/campaigns/${cid}/ledger/knowledge/scenes/${sid}`,
                            undefined, { fresh: true }),
  ledgerCreateKnowledge: (cid: string, body: KnowledgeSave) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/ledger/knowledge`, body),
  ledgerSaveKnowledge: (cid: string, kid: string, body: KnowledgeEdit) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/ledger/knowledge/${kid}`, body),
  ledgerDeleteKnowledge: (cid: string, kid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/ledger/knowledge/${kid}`),

  /** The continuity review (capstone §12, §21).
   *
   *  Both reads are `fresh`: each is re-read right after a write or a sweep
   *  that changed it, and a shared in-flight GET from before the write would
   *  hand back the findings it just settled. */
  getContinuity: (cid: string) =>
    request<ContinuityState>("GET", `/api/campaigns/${encodeSegment(cid)}/continuity`,
                             undefined, { fresh: true }),
  /** The drivers and anchor options the suggestion chooser offers (§16.2).
   *  `fresh`: the chooser reads again after a `stale_drivers` refusal, and a
   *  shared read could be the very answer it was refused against. */
  continuityDrivers: (cid: string) =>
    request<DriversSnapshot>("GET", `/api/campaigns/${encodeSegment(cid)}/continuity/drivers`,
                             undefined, { fresh: true }),
  /** The story graph (§19). `fresh`: a lens change never re-reads, so the one
   *  read a mount makes must be its own rather than a shared, older one. */
  continuityGraph: (cid: string) =>
    request<StoryGraph>("GET", `/api/campaigns/${encodeSegment(cid)}/continuity/graph`,
                        undefined, { fresh: true }),
  continuityCandidates: (cid: string) =>
    request<ContinuityCandidates>(
      "GET", `/api/campaigns/${encodeSegment(cid)}/continuity/candidates`,
      undefined, { fresh: true }),
  /** Start a full reconciliation sweep and wait for it. One live per campaign:
   *  a Refresh pressed while End Scene's sweep runs adopts that run, under
   *  this attempt id, so a lost 202 can still be found. */
  reconcileContinuity: (cid: string, signal?: AbortSignal) =>
    draftRun<ReconcileResult>({ at: "campaign", id: cid }, (attempt) =>
      request<{ run: RunHandle }>(
        "POST", `/api/campaigns/${encodeSegment(cid)}/continuity/reconcile`, undefined,
        { attempt, signal })
        // A 4xx from the POST itself started nothing. Marked here, where it is
        // known, because what reaches the caller from the poll can look the
        // same (`RefreshRefused`).
        .catch((err: unknown) => {
          throw err instanceof ApiError && err.status < 500
            ? new RefreshRefused(err.status, err.detail, err.kind, err.body)
            : err;
        }), { signal }),
  /** Investigate one finding (roadmap 12, E2) and wait for it: a background
   *  run, one live per campaign, whose one write is the finding's proposal.
   *  `notifyShell`, since a written proposal moves what the review shows. */
  /** `attempt`, when given, re-attaches: the server adopts a repeat of the
   *  attempt that started a campaign's investigation rather than running
   *  another. */
  investigateCandidate: (cid: string, id: string, signal?: AbortSignal, attempt?: string) =>
    draftRun<CandidateInvestigated>({ at: "campaign", id: cid }, (a) =>
      request<{ run: RunHandle }>(
        "POST",
        `/api/campaigns/${encodeSegment(cid)}/continuity/candidates/${encodeSegment(id)}/investigate`,
        undefined, { attempt: a, signal }), { signal, attempt }).then(notifyShell),
  /** The run an investigation's attempt id named, or null once the server
   *  holds it no longer: what a remount checks before following it. */
  findCandidateInvestigation: (cid: string, attempt: string, signal?: AbortSignal) =>
    findDraftRun({ at: "campaign", id: cid }, attempt, signal),
  /** Follow a held investigation to its outcome WITHOUT the POST, which would
   *  face the start's checks again over an outcome the run already holds. */
  followCandidateInvestigation: (cid: string, handle: RunHandle, attempt: string,
                                 signal?: AbortSignal) =>
    followDraft<CandidateInvestigated>({ at: "campaign", id: cid }, handle, attempt, signal)
      .then(notifyShell),
  /** The run a history question's attempt id named, or null once the server
   *  holds it no longer (reaped, or a restart): what a re-attach checks
   *  before repeating the POST, which would otherwise ask again. */
  findHistoryQuestion: (cid: string, attempt: string, signal?: AbortSignal) =>
    findDraftRun({ at: "campaign", id: cid }, attempt, signal),
  /** Follow a held history question's run to its answer WITHOUT the POST: a
   *  re-attach must not pass the start's own checks again (the switch, the
   *  model) to collect an answer the run already holds. */
  followHistoryQuestion: (cid: string, handle: RunHandle, attempt: string,
                          signal?: AbortSignal) =>
    followDraft<HistoryAnswer>({ at: "campaign", id: cid }, handle, attempt, signal),
  /** Ask the campaign's history a question (roadmap 12, E3): a draft whose
   *  answer is held on the run and never stored. `attempt`, when given,
   *  re-attaches: the server adopts a repeat of a question's attempt id
   *  rather than asking again. */
  askHistory: (cid: string, question: string, signal?: AbortSignal, attempt?: string) =>
    draftRun<HistoryAnswer>({ at: "campaign", id: cid }, (a) =>
      request<{ run: RunHandle }>(
        "POST", `/api/campaigns/${encodeSegment(cid)}/history/question`, { question },
        { attempt: a, signal }), { signal, attempt }),
  /** Follow a campaign run this client did not start -- the sweep End Scene
   *  began, found on the candidates read (Decision 24). It has no attempt id
   *  of ours, so the last word before giving up on it is the subject's run
   *  list, read by the run's own id. */
  awaitCampaignRun: (cid: string, handle: RunHandle, signal?: AbortSignal) => {
    const base = draftBase({ at: "campaign", id: cid });
    return awaitRunAt(base, handle, () =>
      request<{ runs: RunHandle[] }>("GET", base, undefined, { fresh: true, signal })
        .then((found) => found.runs.find((r) => r.id === handle.id) ?? null)
        .catch(() => null), signal);
  },
  /** The current records a `409 stale_candidate` carries (§12.9), or null when
   *  the error is anything else or its `current` is not that shape. Checked
   *  rather than cast: the detail re-renders from it and binds its actions to
   *  its fingerprint, so a malformed body must fall back to a re-read. */
  staleCurrent: (err: unknown): { fingerprint: string; records: CandidateRecord[] } | null => {
    if (!(err instanceof ApiError)) return null;
    const current = err.body?.current;
    if (typeof current !== "object" || current === null) return null;
    const { fingerprint, records } = current as Record<string, unknown>;
    if (typeof fingerprint !== "string" || !Array.isArray(records)) return null;
    const wellFormed = records.every((r: unknown) =>
      typeof r === "object" && r !== null && typeof (r as { ref?: unknown }).ref === "string");
    return wellFormed ? { fingerprint, records: records as CandidateRecord[] } : null;
  },
  /** Act on one finding. `notifyShell` because Todo's continuity chores and
   *  the rail count what is pending -- on a `partial_apply` 500 too, whose
   *  landed parts move them all the same. */
  applyCandidate: (cid: string, id: string, body: ContinuityApply) =>
    request<ContinuityApplied>(
      "POST",
      `/api/campaigns/${encodeSegment(cid)}/continuity/candidates/${encodeSegment(id)}/apply`,
      body).then(notifyShell, notifyShellOnLanded(["partial_apply"])),
  /** `expectFingerprint` is the current fingerprint a reader saw on a 409
   *  `stale_candidate` and chose to dismiss against (Decision 17). A
   *  `partial_dismiss` 500 set the finding aside, so it tells the shell. */
  dismissCandidate: (cid: string, id: string, decision: "dismiss" | "keep_open",
                     expectFingerprint?: string) =>
    request<{ ok: boolean; fingerprint: string }>(
      "POST",
      `/api/campaigns/${encodeSegment(cid)}/continuity/candidates/${encodeSegment(id)}/dismiss`,
      { decision, ...(expectFingerprint ? { expect_fingerprint: expectFingerprint } : {}) },
    ).then(notifyShell, notifyShellOnLanded(["partial_dismiss"])),
  restoreSuppression: (cid: string, fp: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/api/campaigns/${encodeSegment(cid)}/continuity/suppressions/${encodeSegment(fp)}`,
    ).then(notifyShell),
  /** Unmerge. By query, not path: a ref holds `:` and a plot id may hold `/`. */
  removeAlias: (cid: string, ref: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/api/campaigns/${encodeSegment(cid)}/continuity/aliases?ref=${encodeURIComponent(ref)}`)
      // An unmerge changes the rail's Ledger count.
      .then(notifyShell),
  removeLink: (cid: string, lid: string) =>
    request<{ ok: boolean }>(
      "DELETE", `/api/campaigns/${encodeSegment(cid)}/continuity/links/${encodeSegment(lid)}`)
      // An unmerge changes the rail's Ledger count.
      .then(notifyShell),
  /** The relationship timeline (#63), optionally narrowed to one pair — both
   *  actor tokens or neither, since half a pair names no pair. `fresh` for the
   *  ledger's reason: this is re-read precisely when an absorb or an undo has
   *  just added a row, and a shared in-flight promise would answer with the
   *  list from before it. */
  campaignRelationshipHistory: (cid: string, pair?: { a: string; b: string }) =>
    request<RelationshipChange[]>(
      "GET",
      `/api/campaigns/${cid}/relationships/history`
        + (pair ? `?a=${encodeURIComponent(pair.a)}&b=${encodeURIComponent(pair.b)}` : ""),
      undefined, { fresh: true }),
  // `fresh`, for the reason the ledger opts out of sharing: the timeline is
  // re-read precisely when the records behind it have moved — a scene ended, a
  // beat recorded, a scene renamed — and the dedupe would answer that with the
  // response from before the absorb landed.
  campaignTimeline: (cid: string) =>
    request<Timeline>("GET", `/api/campaigns/${cid}/timeline`, undefined, { fresh: true }),
};
