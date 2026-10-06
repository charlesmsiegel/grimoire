// The continuity review inside the Ledger (capstone §12, §28.9): the column
// section, the group lists, Refresh, and the reviewed and dismissed groups.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

vi.mock("../api/client", async () =>
  (await import("../testkit/ledgerMocks")).ledgerApiMock());
import { api, ApiError } from "../api/client";
import type {
  CandidateRecord, ContinuityCandidate, ContinuityCandidates, ContinuityState,
} from "../api/client";
import LedgerView from "./LedgerView";
import { DECISION_LABELS } from "../components/continuity/labels";
import {
  EMPTY_CANDIDATES, EMPTY_CONTINUITY, EMPTY_LEDGER, installLedgerMocks, renderLedger,
} from "../testkit/ledgerHarness";

const scene = (id: string, title: string) => ({ id, title, date: "" });

/** One record a finding names, as the candidates read joins it. */
function record(ref: string, title: string, extra: Partial<CandidateRecord> = {}):
    CandidateRecord {
  return {
    ref, kind: ref.split(":")[0], title, status: "open", latest_beat: "",
    last_scene: { id: "", title: "" }, pressure: null, aliases: [], due: "",
    beats: [], gone: false, ...extra,
  };
}

function finding(id: string, kind: ContinuityCandidate["kind"],
                 group: ContinuityCandidate["group"], records: CandidateRecord[],
                 extra: Partial<ContinuityCandidate> = {}): ContinuityCandidate {
  return {
    id, kind, group, refs: records.map((r) => r.ref), fingerprint: `fp-${id}`,
    stale: false, stale_reason: null, signals: {}, proposal: null,
    created: "2026-10-01T10:00:00Z", records, ...extra,
  };
}

const MAP = record("thread:mara-s-map", "Mara's map");
const CHART = record("thread:winifred-s-chart", "Winifred's chart");
const CORONATION = record("thread:the-coronation", "The coronation");
const OATH = record("commitment:mara-s-oath", "Mara's oath");

const OVERLAP = finding("possible_duplicate-0123456789abcdef", "possible_duplicate",
                        "overlaps", [MAP, CHART], {
  stale: true, stale_reason: "records",
  proposal: { decision: "duplicate", from: MAP.ref, to: CHART.ref, relation: "",
              status: "", reason: "Both follow the one chart.", evidence_scenes: [] },
});
const CLOSURE = finding("possible_thread_closure-1111111111111111",
                        "possible_thread_closure", "closures", [CORONATION],
                        { signals: { reason: "stale", days_since: 40 } });
const RESOLUTION = finding("possible_commitment_resolution-2222222222222222",
                           "possible_commitment_resolution", "resolutions", [OATH]);

const FINDINGS: ContinuityCandidates = {
  ...EMPTY_CANDIDATES, candidates: [OVERLAP, CLOSURE, RESOLUTION],
};

/** Two aliases (one dangling), one link, one broken raw link, and three
 *  dismissals of which two still name their records as they are. */
const REVIEWED: ContinuityState = {
  ...EMPTY_CONTINUITY,
  aliases: [
    { ref: MAP.ref, to: CHART.ref, canonical: CHART.ref, title: "Mara's map",
      to_title: "Winifred's chart", created: "", source: "review", note: "",
      dangling: false, reason: "" },
    { ref: "thread:saltmarch-road", to: "thread:realm-road", canonical: "thread:realm-road",
      title: "Saltmarch road", to_title: "thread:realm-road", created: "", source: "review",
      note: "", dangling: true, reason: "target missing" },
  ],
  links: [
    { id: "l1", relation: "continues", a: CORONATION.ref, b: CHART.ref,
      a_raw: CORONATION.ref, b_raw: CHART.ref, scene: "", note: "", created: "",
      a_title: "The coronation", b_title: "Winifred's chart" },
  ],
  raw_links: [
    { id: "l1", a: CORONATION.ref, b: CHART.ref, relation: "continues", scene: "",
      note: "", created: "", state: "ok", reason: "" },
    { id: "l2", a: MAP.ref, b: OATH.ref, relation: "pays_off", scene: "", note: "",
      created: "", state: "broken", reason: "endpoint missing" },
  ],
  suppressions: [
    { fingerprint: "s1", kind: "possible_duplicate", refs: [MAP.ref, CHART.ref],
      decision: "dismiss", created: "", live: true,
      titles: ["Mara's map", "Winifred's chart"] },
    { fingerprint: "s2", kind: "possible_thread_closure", refs: [CORONATION.ref],
      decision: "keep_open", created: "", live: true, titles: ["The coronation"] },
    { fingerprint: "s3", kind: "possible_commitment_resolution", refs: [OATH.ref],
      decision: "dismiss", created: "", live: false, titles: ["Mara's oath"] },
  ],
};

const RESULT = {
  sweep: "full", matching: "basic", embedding: "off", llm: "ok", reason: "",
  candidates: 0, adjudicated: 0, pairs_capped: false, superseded: false,
  continuity: "ok", follow_on: false,
};

/** A promise the test settles by hand. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const column = () => within(screen.getByRole("complementary", { name: "Ledger sections" }));
const main = () => within(screen.getByRole("main"));
const groupRow = (label: RegExp) => column().getByRole("button", { name: label });

beforeEach(() => {
  installLedgerMocks();
  (api.continuityCandidates as any).mockResolvedValue(FINDINGS);
  (api.getContinuity as any).mockResolvedValue(REVIEWED);
});

// ---- the column ------------------------------------------------------------

test("the section and its group counts render in the column", async () => {
  renderLedger();
  expect(await column().findByText("Continuity review")).toBeInTheDocument();
  await waitFor(() => expect(groupRow(/possible overlaps/i)).toHaveTextContent("1"));
  expect(groupRow(/possible closures/i)).toHaveTextContent("1");
  expect(groupRow(/possible commitment resolutions/i)).toHaveTextContent("1");
  // Two aliases (the dangling one too), one link, one broken raw link.
  expect(groupRow(/reviewed links \/ merges/i)).toHaveTextContent("4");
  // Live dismissals only.
  expect(groupRow(/dismissed findings/i)).toHaveTextContent("2");
});

test("a group's count is a dash while its read is pending", async () => {
  (api.continuityCandidates as any).mockReturnValue(new Promise(() => {}));
  renderLedger();
  await waitFor(() => expect(groupRow(/reviewed links/i)).toHaveTextContent("4"));
  expect(groupRow(/possible overlaps/i)).toHaveTextContent("—");
  expect(groupRow(/possible closures/i)).toHaveTextContent("—");
  expect(groupRow(/possible overlaps/i)).not.toHaveTextContent("0");
});

test("the matching line says Basic matching active", async () => {
  renderLedger();
  expect(await column().findByText(
    "Basic matching active — semantic matching not configured")).toBeInTheDocument();
});

test("the matching line says Semantic matching active when it is", async () => {
  (api.continuityCandidates as any).mockResolvedValue({ ...FINDINGS, matching: "semantic" });
  renderLedger();
  expect(await column().findByText("Semantic matching active")).toBeInTheDocument();
});

test("selecting a group lists its findings", async () => {
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: /possible closures/i }));
  expect(screen.getByTestId("here"))
    .toHaveTextContent("/campaigns/run/ledger/continuity/closures");
  expect(await main().findByRole("heading", { name: "Possible closures" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: /the coronation/i })).toBeInTheDocument();
  // Only this group's findings.
  expect(main().queryByRole("button", { name: /mara's map/i })).toBeNull();
  expect(main().queryByRole("button", { name: /mara's oath/i })).toBeNull();
});

test("a finding in the list addresses that finding", async () => {
  renderLedger("/campaigns/run/ledger/continuity/resolutions");
  fireEvent.click(await main().findByRole("button", { name: /mara's oath/i }));
  expect(screen.getByTestId("here")).toHaveTextContent(
    "/campaigns/run/ledger/continuity/resolutions/"
    + "possible_commitment_resolution-2222222222222222");
});

test("the dismissed group is collapsed until selected", async () => {
  renderLedger("/campaigns/run/ledger/continuity/overlaps");
  const row = await column().findByRole("button", { name: /dismissed findings/i });
  expect(row).toHaveAttribute("aria-expanded", "false");
  expect(main().queryByRole("button", { name: "Restore" })).toBeNull();
  fireEvent.click(row);
  expect(await main().findAllByRole("button", { name: /^Restore/ })).toHaveLength(2);
  expect(column().getByRole("button", { name: /dismissed findings/i }))
    .toHaveAttribute("aria-expanded", "true");
});

// ---- Refresh ---------------------------------------------------------------

test("a refresh shows progress and re-reads when it lands", async () => {
  const run = deferred<typeof RESULT>();
  (api.reconcileContinuity as any).mockReturnValue(run.promise);
  renderLedger();
  const button = await column().findByRole("button", { name: "Refresh continuity review" });
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(1));
  fireEvent.click(button);
  expect(await column().findByText("Refreshing…")).toBeInTheDocument();
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeDisabled();
  await act(async () => { run.resolve({ ...RESULT, sweep: "full" }); });
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
  expect(column().queryByText("Refreshing…")).toBeNull();
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeEnabled();
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(1);
  expect((api.reconcileContinuity as any).mock.calls[0][0]).toBe("run");
  expect((api.reconcileContinuity as any).mock.calls[0][1]).toBeInstanceOf(AbortSignal);
});

test("a refresh that adopted an incremental sweep runs once more", async () => {
  (api.reconcileContinuity as any)
    .mockResolvedValueOnce({ ...RESULT, sweep: "incremental" })
    .mockResolvedValue({ ...RESULT, sweep: "full" });
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: "Refresh continuity review" }));
  await waitFor(() => expect(api.reconcileContinuity).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(column().getByRole("button", {
    name: "Refresh continuity review" })).toBeEnabled());
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(2);
});

test("a refresh answered incremental twice still stops at two", async () => {
  (api.reconcileContinuity as any).mockResolvedValue({ ...RESULT, sweep: "incremental" });
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: "Refresh continuity review" }));
  await waitFor(() => expect(column().getByRole("button", {
    name: "Refresh continuity review" })).toBeEnabled());
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(2);
});

test("an adopted incremental sweep that failed also runs once more", async () => {
  (api.reconcileContinuity as any)
    .mockRejectedValueOnce(new ApiError(500, "the sweep failed", "run_failed",
                                        { kind: "run_failed", sweep: "incremental" }))
    .mockResolvedValue({ ...RESULT, sweep: "full" });
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: "Refresh continuity review" }));
  await waitFor(() => expect(api.reconcileContinuity).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(column().getByRole("button", {
    name: "Refresh continuity review" })).toBeEnabled());
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(2);
  // The full pass landed, so the failure of the adopted one is not the news.
  expect(column().queryByText(/did not finish/)).toBeNull();
});

test("a failed refresh still re-reads and explains", async () => {
  (api.reconcileContinuity as any).mockRejectedValue(new ApiError(
    409, "continuity.json is malformed; nothing this sweep found was saved", "malformed",
    { kind: "malformed", sweep: "full" }));
  renderLedger();
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(1));
  fireEvent.click(column().getByRole("button", { name: "Refresh continuity review" }));
  expect(await column().findByText(/The model check did not finish — basic findings are listed\./))
    .toHaveTextContent("continuity.json is malformed; nothing this sweep found was saved");
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
  // A failed full sweep is not adopted-incremental: one call, never a retry.
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(1);
});

test("refresh is enabled with no connection, and says what it did", async () => {
  (api.reconcileContinuity as any).mockResolvedValue({ ...RESULT, llm: "off" });
  renderLedger();
  const button = await column().findByRole("button", { name: "Refresh continuity review" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(await column().findByText(
    "No model connection — findings are listed without a suggested decision."))
    .toBeInTheDocument();
});

test("a sweep running on load is followed and its findings appear", async () => {
  const followed = deferred<unknown>();
  (api.continuityCandidates as any)
    .mockResolvedValueOnce({
      ...EMPTY_CANDIDATES,
      run: { id: "r1", attempt_id: null, state: "running", next_index: 0 },
    })
    .mockResolvedValue(FINDINGS);
  (api.awaitCampaignRun as any).mockReturnValue(followed.promise);
  renderLedger("/campaigns/run/ledger/continuity/overlaps");
  expect(await column().findByText("A continuity sweep is running.")).toBeInTheDocument();
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeDisabled();
  expect(api.awaitCampaignRun).toHaveBeenCalledWith(
    "run", expect.objectContaining({ id: "r1" }), expect.any(AbortSignal));
  expect(main().queryByRole("button", { name: /mara's map/i })).toBeNull();
  await act(async () => {
    followed.resolve({ id: "r1", attempt_id: null, state: "landed", next_index: 0 });
  });
  expect(await main().findByRole("button", { name: /mara's map/i })).toBeInTheDocument();
  expect(api.continuityCandidates).toHaveBeenCalledTimes(2);
  expect(column().queryByText("A continuity sweep is running.")).toBeNull();
  // Following is not refreshing: nothing was started.
  expect(api.reconcileContinuity).not.toHaveBeenCalled();
});

test("a followed sweep that fails still re-reads", async () => {
  (api.continuityCandidates as any)
    .mockResolvedValueOnce({
      ...EMPTY_CANDIDATES,
      run: { id: "r1", attempt_id: null, state: "running", next_index: 0 },
    })
    .mockResolvedValue(FINDINGS);
  (api.awaitCampaignRun as any).mockRejectedValue(new ApiError(500, "gone", "run_failed", {}));
  renderLedger("/campaigns/run/ledger/continuity/overlaps");
  expect(await main().findByRole("button", { name: /mara's map/i })).toBeInTheDocument();
  expect(api.continuityCandidates).toHaveBeenCalledTimes(2);
});

const RUNNING = (id: string) => ({ id, attempt_id: null, state: "running", next_index: 0 });

test("a refresh's own second pass, seen running by its re-read, is not followed", async () => {
  // After an adopted incremental pass the loop re-reads and starts pass 2 at
  // once, so that read can come back naming pass 2's own run. Following it
  // would take the latch from the Refresh, which would then hand it back on
  // landing while the follow is still polling -- room for a third reconcile.
  const second = deferred<typeof RESULT>();
  let inPass2 = false;
  (api.reconcileContinuity as any)
    .mockResolvedValueOnce({ ...RESULT, sweep: "incremental" })
    .mockImplementationOnce(() => { inPass2 = true; return second.promise; });
  (api.continuityCandidates as any).mockImplementation(async () =>
    (inPass2 ? { ...FINDINGS, run: RUNNING("r2") } : FINDINGS));
  (api.awaitCampaignRun as any).mockReturnValue(new Promise(() => {}));
  renderLedger();
  fireEvent.click(await column().findByRole("button", { name: "Refresh continuity review" }));
  await waitFor(() => expect(api.reconcileContinuity).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
  expect(api.awaitCampaignRun).not.toHaveBeenCalled();
  expect(column().getByText("Refreshing…")).toBeInTheDocument();
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeDisabled();
  inPass2 = false;
  await act(async () => { second.resolve({ ...RESULT, sweep: "full" }); });
  await waitFor(() => expect(column().getByRole("button", {
    name: "Refresh continuity review" })).toBeEnabled());
  expect(api.awaitCampaignRun).not.toHaveBeenCalled();
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(2);
});

test("re-reads while a sweep is followed do not follow it again", async () => {
  const followed = deferred<unknown>();
  let running = true;
  (api.continuityCandidates as any).mockImplementation(async () =>
    (running ? { ...EMPTY_CANDIDATES, run: RUNNING("r1") } : FINDINGS));
  (api.awaitCampaignRun as any).mockReturnValue(followed.promise);
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY_LEDGER,
    plot: [{ id: "the-coronation", title: "The coronation", status: "open",
             last_scene: "", latest_beat: "", scene: scene("", "") }],
  });
  renderLedger("/campaigns/run/ledger/threads");
  expect(await column().findByText("A continuity sweep is running.")).toBeInTheDocument();
  expect(api.awaitCampaignRun).toHaveBeenCalledTimes(1);
  // Two hand edits: two epoch bumps, each re-reading a candidates list that
  // still names the running sweep.
  fireEvent.click(await screen.findByRole("button", { name: /^close$/i }));
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
  fireEvent.click(await screen.findByRole("button", { name: /^close$/i }));
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(3));
  expect(api.awaitCampaignRun).toHaveBeenCalledTimes(1);
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeDisabled();
  running = false;
  await act(async () => {
    followed.resolve({ id: "r1", attempt_id: null, state: "landed", next_index: 0 });
  });
  await waitFor(() => expect(column().getByRole("button", {
    name: "Refresh continuity review" })).toBeEnabled());
  expect(column().queryByText("A continuity sweep is running.")).toBeNull();
  expect(api.awaitCampaignRun).toHaveBeenCalledTimes(1);
  expect(api.reconcileContinuity).not.toHaveBeenCalled();
});

/** The Ledger with a way to switch campaign while staying mounted -- the
 *  route is not keyed on `cid`, so this is what a switch really is. */
function SwitchTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(to)}>switch campaign</button>;
}

test("switching campaign mid-refresh starts no follow-up", async () => {
  const run = deferred<typeof RESULT>();
  (api.reconcileContinuity as any).mockReturnValueOnce(run.promise);
  (api.continuityCandidates as any).mockImplementation((cid: string) =>
    Promise.resolve(cid === "run" ? FINDINGS : EMPTY_CANDIDATES));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/ledger/continuity/overlaps"]}>
      <SwitchTo to="/campaigns/other/ledger/continuity/overlaps" />
      <Routes>
        <Route path="/campaigns/:cid/ledger/*" element={<LedgerView />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await main().findByRole("button", { name: /mara's map/i })).toBeInTheDocument();
  fireEvent.click(column().getByRole("button", { name: "Refresh continuity review" }));
  const signal = (api.reconcileContinuity as any).mock.calls[0][1] as AbortSignal;
  fireEvent.click(screen.getByRole("button", { name: "switch campaign" }));
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledWith("other"));
  expect(signal.aborted).toBe(true);
  const readsBefore = (api.continuityCandidates as any).mock.calls.length;
  await act(async () => { run.resolve({ ...RESULT, sweep: "incremental" }); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(api.reconcileContinuity).toHaveBeenCalledTimes(1);
  // No re-read for the campaign that is gone, and none of its findings shown.
  expect((api.continuityCandidates as any).mock.calls.length).toBe(readsBefore);
  expect(main().queryByRole("button", { name: /mara's map/i })).toBeNull();
  expect(column().getByRole("button", { name: "Refresh continuity review" })).toBeEnabled();
  expect(column().queryByText("Refreshing…")).toBeNull();
});

// ---- writes ----------------------------------------------------------------

test("a continuity write re-reads the ledger", async () => {
  // Unmerge brings the merged-away thread back as a row of its own.
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY_LEDGER,
    plot: [{ id: "winifred-s-chart", title: "Winifred's chart", status: "open",
             last_scene: "", latest_beat: "", scene: scene("", ""),
             aliases: [{ ref: MAP.ref, title: "Mara's map", status: "open" }] }],
  });
  renderLedger("/campaigns/run/ledger/continuity/reviewed");
  await waitFor(() => expect(column().getByRole("button", { name: /^threads/i }))
    .toHaveTextContent("1"));
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY_LEDGER,
    plot: [
      { id: "winifred-s-chart", title: "Winifred's chart", status: "open", last_scene: "",
        latest_beat: "", scene: scene("", ""), aliases: [] },
      { id: "mara-s-map", title: "Mara's map", status: "open", last_scene: "",
        latest_beat: "", scene: scene("", ""), aliases: [] },
    ],
  });
  const ledgerReads = (api.campaignLedger as any).mock.calls.length;
  fireEvent.click(await main().findByRole("button", { name: "Unmerge Mara's map" }));
  await waitFor(() => expect(api.removeAlias).toHaveBeenCalledWith("run", MAP.ref));
  await waitFor(() => expect((api.campaignLedger as any).mock.calls.length)
    .toBeGreaterThan(ledgerReads));
  await waitFor(() => expect(column().getByRole("button", { name: /^threads/i }))
    .toHaveTextContent("2"));
  // ... and the continuity reads with it: one epoch.
  expect((api.getContinuity as any).mock.calls.length).toBeGreaterThan(1);
});

test("a ledger hand edit re-reads the continuity section", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY_LEDGER,
    plot: [{ id: "the-coronation", title: "The coronation", status: "open",
             last_scene: "", latest_beat: "", scene: scene("", "") }],
  });
  renderLedger("/campaigns/run/ledger/threads");
  const close = await screen.findByRole("button", { name: /^close$/i });
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(1));
  fireEvent.click(close);
  await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
});

test("a stale finding offers Refresh in main on a phone", async () => {
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 375, configurable: true, writable: true });
  try {
    renderLedger("/campaigns/run/ledger/continuity/overlaps");
    expect(await main().findByText("Records changed")).toBeInTheDocument();
    const refresh = main().getByRole("button", { name: "Refresh continuity review" });
    expect(refresh).toBeEnabled();
    fireEvent.click(refresh);
    await waitFor(() => expect(api.reconcileContinuity).toHaveBeenCalledTimes(1));
  } finally {
    Object.defineProperty(window, "innerWidth",
                          { value: width, configurable: true, writable: true });
  }
});

test("findings show hedged proposal labels and visible stale text", async () => {
  (api.continuityCandidates as any).mockResolvedValue({
    ...FINDINGS,
    candidates: [
      OVERLAP,
      finding("possible_relation-3333333333333333", "possible_relation", "overlaps",
              [CHART, OATH], { stale: true, stale_reason: "evidence" }),
    ],
  });
  renderLedger("/campaigns/run/ledger/continuity/overlaps");
  const row = await main().findByRole("button", { name: /mara's map \/ winifred's chart/i });
  expect(row).toHaveTextContent("Possible overlap");
  expect(row).toHaveTextContent("Suggested: same business (merge)");
  expect(row).toHaveTextContent("Records changed");
  const other = main().getByRole("button", { name: /winifred's chart \/ mara's oath/i });
  expect(other).toHaveTextContent("Evidence moved");
  expect(other).not.toHaveTextContent("Suggested");
  expect(screen.getByRole("main")).not.toHaveTextContent(/\bduplicate\b/i);
});

test("a decision word no table holds is never shown raw", async () => {
  (api.continuityCandidates as any).mockResolvedValue({
    ...FINDINGS,
    candidates: [{ ...CLOSURE, proposal: { ...OVERLAP.proposal!, decision: "obliterate" } }],
  });
  renderLedger("/campaigns/run/ledger/continuity/closures");
  const row = await main().findByRole("button", { name: /the coronation/i });
  expect(row).toHaveTextContent("May be finished");
  expect(row).not.toHaveTextContent(/obliterate|Suggested/);
});

test("the client's proposal labels are the hedged table", () => {
  expect(DECISION_LABELS).toEqual({
    duplicate: "Suggested: same business (merge)",
    continuation: "Suggested: continuation of",
    subthread: "Suggested: subthread of",
    related: "Suggested: related",
    pays_off: "Suggested: pays off",
    distinct: "Suggested: different business",
    close: "Suggested: may be finished",
    fulfilled: "Suggested: fulfilled",
    broken: "Suggested: broken",
    expired: "Suggested: expired",
    keep_open: "Suggested: keep open",
    before: "Suggested: before",
    on: "Suggested: on",
    after: "Suggested: after",
    by: "Suggested: by",
    unrelated: "No clear suggestion",
    uncertain: "No clear suggestion",
  });
});

test("reviewed links offer Unmerge and Remove link, and mark broken ones", async () => {
  renderLedger("/campaigns/run/ledger/continuity/reviewed");
  const merged = await main().findByRole("listitem", { name: /mara's map/i });
  expect(merged).toHaveTextContent("Merged into Winifred's chart");
  expect(within(merged).queryByText("Broken")).toBeNull();

  const dangling = main().getByRole("listitem", { name: /saltmarch road/i });
  expect(within(dangling).getByText("Broken")).toBeInTheDocument();
  fireEvent.click(within(dangling).getByRole("button", { name: "Unmerge Saltmarch road" }));
  await waitFor(() => expect(api.removeAlias)
    .toHaveBeenCalledWith("run", "thread:saltmarch-road"));

  const link = main().getByRole("listitem", { name: /the coronation/i });
  expect(link).toHaveTextContent("Continuation of");
  expect(within(link).queryByText("Broken")).toBeNull();
  fireEvent.click(within(link).getByRole("button", { name: /^Remove link/ }));
  await waitFor(() => expect(api.removeLink).toHaveBeenCalledWith("run", "l1"));

  const broken = main().getAllByRole("listitem").find((li) =>
    li.textContent?.includes("endpoint missing")) as HTMLElement;
  expect(within(broken).getByText("Broken")).toBeInTheDocument();
  fireEvent.click(within(broken).getByRole("button", { name: /^Remove link/ }));
  await waitFor(() => expect(api.removeLink).toHaveBeenCalledWith("run", "l2"));
  // The ok raw link is the effective one, listed once.
  expect(main().getAllByRole("button", { name: /^Remove link/ })).toHaveLength(2);
});

test("a refused remove shows why and keeps the row", async () => {
  (api.removeAlias as any).mockRejectedValue(new ApiError(409, "that merge changed", "conflict"));
  renderLedger("/campaigns/run/ledger/continuity/reviewed");
  fireEvent.click(await main().findByRole("button", { name: "Unmerge Mara's map" }));
  expect(await main().findByText("that merge changed")).toBeInTheDocument();
  expect(main().getByRole("listitem", { name: /mara's map/i })).toBeInTheDocument();
});

test("a dismissed finding is restorable", async () => {
  renderLedger("/campaigns/run/ledger/continuity/dismissed");
  const dismissed = await main().findByRole("listitem", { name: /mara's map \/ winifred's chart/i });
  expect(dismissed).toHaveTextContent("Dismissed");
  const kept = main().getByRole("listitem", { name: /the coronation/i });
  expect(kept).toHaveTextContent("Kept open");
  // A dismissal whose records have since changed suppresses nothing: not shown.
  expect(main().queryByRole("listitem", { name: /mara's oath/i })).toBeNull();
  const reads = (api.getContinuity as any).mock.calls.length;
  fireEvent.click(within(dismissed).getByRole("button", { name: /^Restore/ }));
  await waitFor(() => expect(api.restoreSuppression).toHaveBeenCalledWith("run", "s1"));
  await waitFor(() => expect((api.getContinuity as any).mock.calls.length)
    .toBeGreaterThan(reads));
});

test("the candidates read failing costs only its section", async () => {
  (api.continuityCandidates as any).mockRejectedValue(new Error("boom"));
  (api.campaignLedger as any).mockResolvedValue({
    ...EMPTY_LEDGER,
    plot: [{ id: "the-coronation", title: "The coronation", status: "open",
             last_scene: "", latest_beat: "", scene: scene("", "") }],
  });
  renderLedger("/campaigns/run/ledger/continuity/overlaps");
  expect(await main().findByText(/continuity review could not be read/i)).toBeInTheDocument();
  await waitFor(() => expect(column().getByRole("button", { name: /^threads/i }))
    .toHaveTextContent("1"));
  // Its groups say nothing rather than a zero; the other read's still count.
  expect(groupRow(/possible overlaps/i)).toHaveTextContent("—");
  expect(groupRow(/reviewed links/i)).toHaveTextContent("4");
  // And the reviewed list, a different read, still renders.
  fireEvent.click(groupRow(/reviewed links/i));
  expect(await main().findByRole("listitem", { name: /mara's map/i })).toBeInTheDocument();
});

test("the continuity read failing costs only its groups", async () => {
  (api.getContinuity as any).mockRejectedValue(new Error("boom"));
  renderLedger("/campaigns/run/ledger/continuity/reviewed");
  expect(await main().findByText(/could not be read/i)).toBeInTheDocument();
  await waitFor(() => expect(groupRow(/possible overlaps/i)).toHaveTextContent("1"));
  expect(groupRow(/reviewed links/i)).toHaveTextContent("—");
  expect(groupRow(/dismissed findings/i)).toHaveTextContent("—");
});

test("an empty group says so", async () => {
  (api.continuityCandidates as any).mockResolvedValue(EMPTY_CANDIDATES);
  (api.getContinuity as any).mockResolvedValue(EMPTY_CONTINUITY);
  renderLedger("/campaigns/run/ledger/continuity/closures");
  expect(await main().findByText(/nothing here may be finished/i)).toBeInTheDocument();
});
