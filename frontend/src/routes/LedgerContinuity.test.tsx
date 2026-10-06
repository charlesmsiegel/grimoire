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
  EMPTY_CANDIDATES, EMPTY_CONTINUITY, EMPTY_LEDGER, Here, installLedgerMocks, renderLedger,
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

// ---- the finding detail (Task 17, §12.2–§12.4, §28.9) ----------------------

/** Every scene, actor and event the detail fixtures mention, named. */
const NAMES = {
  "001--realm-road": "Realm road",
  "002--saltmarch-eve": "Saltmarch Eve",
  "characters:mara": "Mara",
  "event:the-coronation": "The coronation",
};
const SCENES = [
  { id: "002--saltmarch-eve", title: "Saltmarch Eve" },
  { id: "001--realm-road", title: "Realm road" },
];

// A model-written plot id holding a "/" (Review Focus 1).
const MAP_D = record("thread:mara/map", "Mara's map", {
  last_scene: { id: "001--realm-road", title: "Realm road" },
  latest_beat: "Mara unrolled the map.",
  beats: [{ scene: "001--realm-road", text: "Mara unrolled the map." }],
});
const CHART_D = record("thread:winifred-s-chart", "Winifred's chart", {
  beats: [{ scene: "002--saltmarch-eve", text: "Winifred marked the shoals." }],
});
const SER_OATH = record("commitment:seraphine-s-oath", "Seraphine's oath");
const OATH_DUE = record("commitment:mara-s-oath", "Mara's oath", { due: "Saltmarch Eve" });
const EVENT = record("event:the-coronation", "The coronation", { due: "Saltmarch Eve" });

const PAIR = finding("possible_duplicate-aaaaaaaaaaaaaaaa", "possible_duplicate", "overlaps",
                     [MAP_D, CHART_D], {
  signals: { title_exact: false, slug_equal: true, lexical: 0.7, cosine: 0.8123,
             shared_actors: ["characters:mara"], shared_scenes: ["001--realm-road"],
             shared_anchors: ["event:the-coronation"], via: "slug" },
  proposal: { decision: "duplicate", from: MAP_D.ref, to: CHART_D.ref, relation: "",
              status: "", reason: "Both follow the one chart.",
              evidence_scenes: ["002--saltmarch-eve"] },
});
const OATHS = finding("possible_duplicate-bbbbbbbbbbbbbbbb", "possible_duplicate", "overlaps",
                      [OATH_DUE, SER_OATH]);
const CROSS = finding("possible_relation-cccccccccccccccc", "possible_relation", "overlaps",
                      [OATH, MAP_D]);
const TEMPORAL = finding("possible_relation-dddddddddddddddd", "possible_relation",
                         "overlaps", [OATH, EVENT], {
  signals: { reason: "temporal", in_days: 4 },
  proposal: { decision: "before", from: OATH.ref, to: EVENT.ref, relation: "before",
              status: "", reason: "The oath is owed before the crowning.",
              evidence_scenes: [] },
});
const CLOSE_ME = finding("possible_thread_closure-eeeeeeeeeeeeeeee",
                         "possible_thread_closure", "closures", [CORONATION], {
  signals: { reason: "stale", days_since: 40 },
  proposal: { decision: "close", from: "", to: "", relation: "", status: "",
              reason: "The crown was placed.", evidence_scenes: ["001--realm-road"] },
});
const RESOLVE_ME = finding("possible_commitment_resolution-ffffffffffffffff",
                           "possible_commitment_resolution", "resolutions", [OATH]);

const DETAIL: ContinuityCandidates = {
  ...EMPTY_CANDIDATES, names: NAMES, scenes: SCENES,
  candidates: [PAIR, OATHS, CROSS, TEMPORAL, CLOSE_ME, RESOLVE_ME, OVERLAP],
};

const at = (c: ContinuityCandidate) =>
  `/campaigns/run/ledger/continuity/${c.group}/${c.id}`;
const aside = () => within(screen.getByRole("complementary", { name: "Finding actions" }));
/** The sidebar, once the detail has rendered it. */
const sidebar = async () =>
  within(await screen.findByRole("complementary", { name: "Finding actions" }));
const applied = () => (api.applyCandidate as any).mock.calls;

describe("the finding detail", () => {
  beforeEach(() => {
    (api.continuityCandidates as any).mockResolvedValue(DETAIL);
  });

  test("a candidate opens a read-only detail", async () => {
    renderLedger("/campaigns/run/ledger/continuity/overlaps");
    fireEvent.click(await main().findByRole("button", {
      name: /^mara's map \/ winifred's chart.*same business \(merge\)$/i }));
    expect(screen.getByTestId("here")).toHaveTextContent(at(PAIR));
    expect(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.querySelector("textarea")).toBeNull();
    const m = main();
    expect(m.getByRole("heading", { level: 3 })).toBeInTheDocument();
    expect(m.getByRole("link", { name: "Mara's map" })).toBeInTheDocument();
    expect(m.getByRole("link", { name: "Winifred's chart" })).toBeInTheDocument();
    expect(m.getByText("Mara unrolled the map.")).toBeInTheDocument();
    expect(m.getByText("Same slug")).toBeInTheDocument();
    expect(m.queryByText("Same title")).toBeNull();
    expect(m.getByText("Shared characters: Mara")).toBeInTheDocument();
    expect(m.getByText("Shared dates: The coronation")).toBeInTheDocument();
    expect(m.getByText("Shared scenes: 1")).toBeInTheDocument();
    expect(m.getByText("Meaning overlap 0.81 — a discovery signal, not confidence"))
      .toBeInTheDocument();
    expect(m.getByText("Suggested: same business (merge)")).toBeInTheDocument();
    expect(m.getByText("Both follow the one chart.")).toBeInTheDocument();
    // Evidence scenes navigate to the scene.
    expect(m.getAllByRole("link", { name: "Saltmarch Eve" })[0])
      .toHaveAttribute("href", "/campaigns/run/scenes/002--saltmarch-eve");
    // The sidebar carries the metadata too.
    expect(aside().getByText("Possible overlap")).toBeInTheDocument();
    // "‹ All findings" goes back to the list.
    fireEvent.click(m.getByRole("button", { name: "‹ All findings" }));
    expect(screen.getByTestId("here"))
      .toHaveTextContent(/^\/campaigns\/run\/ledger\/continuity\/overlaps$/);
  });

  test("apply requires an explicit action", async () => {
    renderLedger(at(PAIR));
    expect(await (await sidebar()).findByRole("button", { name: "Keep Mara's map" })).toBeEnabled();
    expect(api.applyCandidate).not.toHaveBeenCalled();
    expect(api.dismissCandidate).not.toHaveBeenCalled();
  });

  test("the canonical side is selectable", async () => {
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0]).toEqual(["run", PAIR.id, {
      op: "alias", canonical: "thread:winifred-s-chart", expect_fingerprint: PAIR.fingerprint,
    }]);
    renderLedger(at(PAIR));
    const sides = await screen.findAllByRole("button", { name: "Keep Mara's map" });
    fireEvent.click(sides[sides.length - 1]);
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(2));
    expect(applied()[1][2]).toEqual({
      op: "alias", canonical: "thread:mara/map", expect_fingerprint: PAIR.fingerprint,
    });
  });

  test("thread pairs offer continues and subthread both ways", async () => {
    renderLedger(at(PAIR));
    const a = await (await sidebar()).findByRole("button", { name: "Mara's map continues Winifred's chart" });
    expect(aside().getByRole("button", { name: "Winifred's chart continues Mara's map" }))
      .toBeInTheDocument();
    expect(aside().getByRole("button", { name: "Mara's map is a subthread of Winifred's chart" }))
      .toBeInTheDocument();
    expect(aside().getByRole("button", { name: "Winifred's chart is a subthread of Mara's map" }))
      .toBeInTheDocument();
    fireEvent.click(a);
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({
      op: "link", from: MAP_D.ref, to: CHART_D.ref, relation: "continues",
      expect_fingerprint: PAIR.fingerprint,
    });
  });

  test("a liveness mismatch confirmation resubmits with the flag", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(new ApiError(
      409, "Mara's map is open but Winifred's chart is closed", "liveness_mismatch", {
        kind: "liveness_mismatch", detail: "Mara's map is open but Winifred's chart is closed",
        source: { status: "open", kind: "", due: "" },
        canonical: { ref: CHART_D.ref, status: "closed", kind: "", due: "" },
      }));
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    expect(await screen.findByText(/Merging will hide an open thread behind a closed one/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Merge anyway" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(2));
    expect(applied()[1][2]).toEqual({
      op: "alias", canonical: CHART_D.ref, expect_fingerprint: PAIR.fingerprint,
      accept_status_change: true,
    });
  });

  test("the due copy is sent when ticked", async () => {
    renderLedger(at(OATHS));
    const box = await (await sidebar()).findByRole("checkbox", {
      name: "Also copy the due date “Saltmarch Eve” to Seraphine's oath" });
    fireEvent.click(box);
    fireEvent.click(aside().getByRole("button", { name: "Keep Seraphine's oath" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({
      op: "alias", canonical: SER_OATH.ref, copy_due: true,
      expect_fingerprint: OATHS.fingerprint,
    });
  });

  test("the due copy is not sent when the kept record is the one with the due", async () => {
    renderLedger(at(OATHS));
    fireEvent.click(await (await sidebar()).findByRole("checkbox", { name: /Also copy the due date/ }));
    fireEvent.click(aside().getByRole("button", { name: "Keep Mara's oath" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).not.toHaveProperty("copy_due");
  });

  test("a closure reveals its form and sends the beat with its evidence scene", async () => {
    renderLedger(at(CLOSE_ME));
    const close = await (await sidebar()).findByRole("button", { name: "Close thread" });
    expect(screen.queryByRole("textbox", { name: "Closing beat" })).toBeNull();
    fireEvent.click(close);
    const beat = screen.getByRole("textbox", { name: "Closing beat" });
    const select = screen.getByRole("combobox", { name: "Evidence scene" });
    // Defaulted to the proposal's first evidence scene; options newest first.
    expect(select).toHaveValue("001--realm-road");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options.filter((t) => t !== "No scene")).toEqual(["Saltmarch Eve", "Realm road"]);
    expect(api.applyCandidate).not.toHaveBeenCalled();
    fireEvent.change(beat, { target: { value: "The crown is placed." } });
    fireEvent.change(select, { target: { value: "002--saltmarch-eve" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({
      op: "close", beat: "The crown is placed.", scene: "002--saltmarch-eve",
      expect_fingerprint: CLOSE_ME.fingerprint,
    });
  });

  test("a closure with no beat sends no scene, and a beat needs a scene", async () => {
    (api.continuityCandidates as any).mockResolvedValue({
      ...DETAIL, candidates: [{ ...CLOSE_ME, proposal: null }] });
    renderLedger(at(CLOSE_ME));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Close thread" }));
    const select = screen.getByRole("combobox", { name: "Evidence scene" });
    expect((select as HTMLSelectElement).value).toBe("");
    fireEvent.change(screen.getByRole("textbox", { name: "Closing beat" }),
                     { target: { value: "The crown is placed." } });
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Closing beat" }),
                     { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({ op: "close", expect_fingerprint: CLOSE_ME.fingerprint });
  });

  test("a resolution sends its status, and Cancel closes the form", async () => {
    renderLedger(at(RESOLVE_ME));
    for (const name of ["Fulfilled", "Broken", "Expired", "Keep open", "Dismiss finding"]) {
      expect(await (await sidebar()).findByRole("button", { name })).toBeInTheDocument();
    }
    fireEvent.click(aside().getByRole("button", { name: "Broken" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Closing beat" })).toBeNull();
    fireEvent.click(aside().getByRole("button", { name: "Broken" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({
      op: "resolve", status: "broken", expect_fingerprint: RESOLVE_ME.fingerprint,
    });
  });

  test("keep open and dismiss set the finding aside", async () => {
    renderLedger(at(CLOSE_ME));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep open" }));
    await waitFor(() => expect(api.dismissCandidate).toHaveBeenCalledWith(
      "run", CLOSE_ME.id, "keep_open", CLOSE_ME.fingerprint));
    renderLedger(at(PAIR));
    const dismiss = await screen.findAllByRole("button", { name: "Dismiss" });
    fireEvent.click(dismiss[dismiss.length - 1]);
    await waitFor(() => expect(api.dismissCandidate).toHaveBeenCalledWith(
      "run", PAIR.id, "dismiss", PAIR.fingerprint));
    expect(api.applyCandidate).not.toHaveBeenCalled();
  });

  test("a temporal pair accepts with a relation defaulted to the proposal", async () => {
    renderLedger(at(TEMPORAL));
    const accept = await (await sidebar()).findByRole("button", { name: "Accept" });
    expect(aside().getByRole("button", { name: "Related" })).toBeInTheDocument();
    expect(aside().queryByRole("button", { name: /^Keep|Pays off/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Relation" })).toBeNull();
    fireEvent.click(accept);
    const relation = screen.getByRole("combobox", { name: "Relation" });
    expect(relation).toHaveValue("before");
    fireEvent.change(relation, { target: { value: "by" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    expect(applied()[0][2]).toEqual({
      op: "link", from: OATH.ref, to: EVENT.ref, relation: "by",
      expect_fingerprint: TEMPORAL.fingerprint,
    });
  });

  test("a stale 409 re-renders current records and does not start a run", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(new ApiError(
      409, "Records have changed since this was found.", "stale_candidate", {
        kind: "stale_candidate", detail: "Records have changed since this was found.",
        reason: "records",
        current: { fingerprint: "fp-now",
                   records: [{ ...MAP_D, title: "Mara's sea map" }, CHART_D] },
      }));
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    expect(await main().findByRole("link", { name: "Mara's sea map" })).toBeInTheDocument();
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
    expect(api.reconcileContinuity).not.toHaveBeenCalled();
    const again = aside().getByRole("button", { name: "Keep Winifred's chart" });
    expect(again).toBeEnabled();
    expect(aside().getByRole("button", { name: "Keep Mara's sea map" })).toBeEnabled();
    fireEvent.click(again);
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(2));
    expect(applied()[1][2]).toEqual({
      op: "alias", canonical: CHART_D.ref, expect_fingerprint: "fp-now",
    });
  });

  test("an evidence 409 keeps the actions disabled and offers Refresh", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(new ApiError(
      409, "Records have changed since this was found.", "stale_candidate", {
        kind: "stale_candidate", reason: "evidence",
        current: { fingerprint: PAIR.fingerprint, records: [MAP_D, CHART_D] },
      }));
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    expect(await (await sidebar()).findByText(
      "An evidence scene has been renamed or removed since this was found."))
      .toBeInTheDocument();
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
    expect(aside().getByRole("button", { name: "Keep Winifred's chart" })).toBeDisabled();
    expect(aside().getByRole("button", { name: "Dismiss" })).toBeDisabled();
    expect(api.reconcileContinuity).not.toHaveBeenCalled();
    const refresh = aside().getByRole("button", { name: "Refresh" });
    expect(refresh).toBeEnabled();
    fireEvent.click(refresh);
    await waitFor(() => expect(api.reconcileContinuity).toHaveBeenCalledTimes(1));
  });

  test("a 409 with a malformed current body falls back to a re-read", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(new ApiError(
      409, "Records have changed since this was found.", "stale_candidate", {
        kind: "stale_candidate", reason: "records", current: { fingerprint: 7 },
      }));
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
    expect(api.reconcileContinuity).not.toHaveBeenCalled();
    // Nothing was laid over the read: the finding is as the re-read says.
    expect(main().getByRole("link", { name: "Mara's map" })).toBeInTheDocument();
    fireEvent.click(aside().getByRole("button", { name: "Keep Winifred's chart" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(2));
    expect(applied()[1][2]).toHaveProperty("expect_fingerprint", PAIR.fingerprint);
  });

  test("a successful apply returns to the group list", async () => {
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    await waitFor(() => expect(screen.getByTestId("here"))
      .toHaveTextContent(/^\/campaigns\/run\/ledger\/continuity\/overlaps$/));
    expect(await main().findByText("Merged Mara's map into Winifred's chart."))
      .toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Finding actions" })).toBeNull();
    // A write re-reads the ledger and the review (one epoch).
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
    // Opening another finding clears the confirmation.
    fireEvent.click(main().getByRole("button", { name: /mara's oath \/ seraphine's oath/i }));
    expect(main().queryByText("Merged Mara's map into Winifred's chart.")).toBeNull();
  });

  test("a successful dismiss returns to the group list", async () => {
    renderLedger(at(CLOSE_ME));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Dismiss finding" }));
    await waitFor(() => expect(screen.getByTestId("here"))
      .toHaveTextContent(/^\/campaigns\/run\/ledger\/continuity\/closures$/));
    expect(await main().findByText(/^Dismissed/)).toBeInTheDocument();
  });

  test("a deep link does not claim not-pending while reading", async () => {
    (api.continuityCandidates as any).mockReturnValue(new Promise(() => {}));
    renderLedger(at(PAIR));
    expect(await main().findByText("Reading the findings…")).toBeInTheDocument();
    expect(screen.queryByText("This finding is no longer pending.")).toBeNull();
  });

  test("an address to a finding that is not pending shows its group", async () => {
    renderLedger("/campaigns/run/ledger/continuity/overlaps/possible_duplicate-9999999999999999");
    expect(await main().findByText("This finding is no longer pending.")).toBeInTheDocument();
    expect(main().getByRole("button", { name: /^mara's map \/ winifred's chart.*same business \(merge\)$/i }))
      .toBeInTheDocument();
  });

  test("commitment pairs never offer continues", async () => {
    renderLedger(at(OATHS));
    expect(await (await sidebar()).findByRole("button", { name: "Keep Mara's oath" })).toBeInTheDocument();
    expect(aside().getByRole("button", { name: "Keep Seraphine's oath" })).toBeInTheDocument();
    expect(aside().getByRole("button", { name: "Related" })).toBeInTheDocument();
    expect(aside().queryByRole("button", { name: /continues|subthread/ })).toBeNull();
  });

  test("record titles link to their ledger rows, and no raw id is shown", async () => {
    renderLedger(at(PAIR));
    const chart = await main().findByRole("link", { name: "Winifred's chart" });
    expect(chart).toHaveAttribute("href", "/campaigns/run/ledger/threads/winifred-s-chart");
    expect(chart).toHaveClass("chip");
    expect(main().getByRole("link", { name: "Mara's map" }))
      .toHaveAttribute("href", "/campaigns/run/ledger/threads/mara%2Fmap");
    for (const link of main().getAllByRole("link", { name: "Realm road" })) {
      expect(link).toHaveAttribute("href", "/campaigns/run/scenes/001--realm-road");
    }
    expect(main().queryAllByText(/^\d{3}--/)).toHaveLength(0);
    expect(main().queryAllByText(/^characters:/)).toHaveLength(0);
    // A ref is `<type>:<id>`, with no space after the colon; the "Shared
    // characters: Mara" label is not one.
    expect(screen.getByRole("main").textContent).not.toMatch(/\d{3}--|characters:\S|event:\S/);
    fireEvent.click(chart);
    expect(screen.getByTestId("here"))
      .toHaveTextContent("/campaigns/run/ledger/threads/winifred-s-chart");
  });

  test("deep links open the right group and candidate", async () => {
    renderLedger(at(CLOSE_ME));
    expect(await (await sidebar()).findByRole("button", { name: "Close thread" })).toBeInTheDocument();
    expect(main().getByRole("heading", { name: "Possible closures" })).toBeInTheDocument();
    expect(main().getByRole("link", { name: "The coronation" }))
      .toHaveAttribute("href", "/campaigns/run/ledger/threads/the-coronation");
    expect(groupRow(/possible closures/i)).toHaveClass("active");
  });

  test("an apply answered not_found shows the finding is no longer pending", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(
      new ApiError(404, "This finding is no longer pending.", "not_found",
                   { kind: "not_found" }));
    (api.continuityCandidates as any)
      .mockResolvedValueOnce(DETAIL)
      .mockResolvedValue({ ...DETAIL, candidates: DETAIL.candidates.filter((c) => c !== PAIR) });
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    expect(await main().findByText("This finding is no longer pending.")).toBeInTheDocument();
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("complementary", { name: "Finding actions" })).toBeNull();
    expect(main().getByRole("button", { name: /mara's oath \/ seraphine's oath/i }))
      .toBeInTheDocument();
    expect(api.reconcileContinuity).not.toHaveBeenCalled();
  });

  test("any other refusal shows its text", async () => {
    (api.applyCandidate as any).mockRejectedValueOnce(
      new ApiError(500, "the finding was only partly applied", "partial_apply", {}));
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    expect(await (await sidebar()).findByText("the finding was only partly applied"))
      .toBeInTheDocument();
    expect(screen.getByTestId("here")).toHaveTextContent(at(PAIR));
  });

  test("cross-type pairs never offer a merge", async () => {
    renderLedger(at(CROSS));
    const pays = await (await sidebar()).findByRole("button", { name: "Pays off" });
    expect(aside().queryByRole("button", { name: /^Keep/ })).toBeNull();
    expect(aside().queryByRole("button", { name: /continues|subthread/ })).toBeNull();
    expect(aside().getByRole("button", { name: "Related" })).toBeInTheDocument();
    expect(aside().getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    fireEvent.click(pays);
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    // Thread → commitment, whichever side the finding listed first (§5.3).
    expect(applied()[0][2]).toEqual({
      op: "link", from: MAP_D.ref, to: OATH.ref, relation: "pays_off",
      expect_fingerprint: CROSS.fingerprint,
    });
  });

  test("stale findings disable every action", async () => {
    renderLedger(at(OVERLAP));
    expect(await (await sidebar()).findByText("Records have changed since this was found."))
      .toBeInTheDocument();
    const buttons = aside().getAllByRole("button");
    const actions = buttons.filter((b) => b.textContent !== "Refresh");
    expect(actions.length).toBeGreaterThanOrEqual(8);
    for (const b of actions) expect(b).toBeDisabled();
    expect(aside().getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(api.applyCandidate).not.toHaveBeenCalled();
  });
  // ---- review fixes (Task 17) ----------------------------------------------

  test("a closure apply re-reads the ledger, and the Threads count drops", async () => {
    const thread = (status: string) => ({
      ...EMPTY_LEDGER,
      plot: [{ id: "the-coronation", title: "The coronation", status, last_scene: "",
               latest_beat: "", scene: scene("", ""), aliases: [] }],
    });
    (api.campaignLedger as any).mockResolvedValue(thread("open"));
    renderLedger(at(CLOSE_ME));
    await waitFor(() => expect(column().getByRole("button", { name: /^threads/i }))
      .toHaveTextContent("1"));
    (api.campaignLedger as any).mockResolvedValue(thread("closed"));
    const ledgerReads = (api.campaignLedger as any).mock.calls.length;
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Close thread" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((api.campaignLedger as any).mock.calls.length)
      .toBeGreaterThan(ledgerReads));
    await waitFor(() => expect(column().getByRole("button", { name: /^threads/i }))
      .toHaveTextContent("0"));
  });

  /** The Ledger with a way to switch campaign while staying mounted. */
  const renderSwitchable = (path: string, to: string) => render(
    <MemoryRouter initialEntries={[path]}>
      <SwitchTo to={to} />
      <Here />
      <Routes>
        <Route path="/campaigns/:cid/ledger/*" element={<LedgerView />} />
      </Routes>
    </MemoryRouter>,
  );
  const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  test("an apply that lands after a campaign switch leaves the reader where they are",
       async () => {
    const write = deferred<{ ok: true }>();
    (api.applyCandidate as any).mockReturnValueOnce(write.promise);
    renderSwitchable(at(PAIR), "/campaigns/other/ledger/continuity/overlaps");
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    fireEvent.click(screen.getByRole("button", { name: "switch campaign" }));
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledWith("other"));
    await act(async () => { write.resolve({ ok: true }); });
    await settle();
    expect(screen.getByTestId("here"))
      .toHaveTextContent(/^\/campaigns\/other\/ledger\/continuity\/overlaps$/);
    expect(main().queryByText(/^Merged/)).toBeNull();
  });

  test("a refusal that lands after a campaign switch lays nothing over the new campaign",
       async () => {
    // A fork shares candidate ids, so the same id may be open on the new one.
    const write = deferred<never>();
    (api.applyCandidate as any).mockReturnValueOnce(write.promise);
    renderSwitchable(at(PAIR), `/campaigns/other/ledger/continuity/overlaps/${PAIR.id}`);
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    fireEvent.click(screen.getByRole("button", { name: "switch campaign" }));
    await waitFor(() => expect(api.continuityCandidates).toHaveBeenCalledWith("other"));
    await act(async () => {
      write.reject(new ApiError(
        409, "Records have changed since this was found.", "stale_candidate", {
          kind: "stale_candidate", reason: "records",
          current: { fingerprint: "fp-now",
                     records: [{ ...MAP_D, title: "Mara's sea map" }, CHART_D] },
        }));
    });
    await settle();
    expect(screen.getByTestId("here")).toHaveTextContent(
      `/campaigns/other/ledger/continuity/overlaps/${PAIR.id}`);
    expect(await main().findByRole("link", { name: "Mara's map" })).toBeInTheDocument();
    expect(main().queryByRole("link", { name: "Mara's sea map" })).toBeNull();
    expect(screen.queryByText(/Records have changed since this was found/)).toBeNull();
  });

  test("a refusal that lands after another finding is opened stays off it", async () => {
    const write = deferred<never>();
    (api.applyCandidate as any).mockReturnValueOnce(write.promise);
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    fireEvent.click(main().getByRole("button", { name: "‹ All findings" }));
    fireEvent.click(await main().findByRole("button", { name: /mara's oath \/ seraphine's oath/i }));
    expect(await (await sidebar()).findByRole("button", { name: "Keep Mara's oath" }))
      .toBeInTheDocument();
    await act(async () => {
      write.reject(new ApiError(
        409, "Mara's map is open but Winifred's chart is closed", "liveness_mismatch", {
          kind: "liveness_mismatch", detail: "Mara's map is open but Winifred's chart is closed",
          source: { status: "open", kind: "", due: "" },
          canonical: { ref: CHART_D.ref, status: "closed", kind: "", due: "" },
        }));
    });
    await settle();
    expect(screen.getByTestId("here")).toHaveTextContent(at(OATHS));
    expect(screen.queryByRole("button", { name: "Merge anyway" })).toBeNull();
    expect(screen.queryByText(/Merging will hide/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.applyCandidate).toHaveBeenCalledTimes(1);
  });

  test("an apply that lands after another finding is opened leaves the reader on it",
       async () => {
    const write = deferred<{ ok: true }>();
    (api.applyCandidate as any).mockReturnValueOnce(write.promise);
    renderLedger(at(PAIR));
    fireEvent.click(await (await sidebar()).findByRole("button", { name: "Keep Winifred's chart" }));
    fireEvent.click(main().getByRole("button", { name: "‹ All findings" }));
    fireEvent.click(await main().findByRole("button", { name: /mara's oath \/ seraphine's oath/i }));
    await sidebar();
    const ledgerReads = (api.campaignLedger as any).mock.calls.length;
    await act(async () => { write.resolve({ ok: true }); });
    await settle();
    expect(screen.getByTestId("here")).toHaveTextContent(at(OATHS));
    expect(await (await sidebar()).findByRole("button", { name: "Keep Mara's oath" }))
      .toBeInTheDocument();
    // The write landed on this campaign all the same: the ledger re-reads.
    await waitFor(() => expect((api.campaignLedger as any).mock.calls.length)
      .toBeGreaterThan(ledgerReads));
  });

  test("a same-title pair tells its two sides apart", async () => {
    const twin = record("thread:mara-s-map-2", "Mara's map");
    const twins = finding("possible_duplicate-9898989898989898", "possible_duplicate",
                          "overlaps", [MAP_D, twin], { signals: { title_exact: true } });
    (api.continuityCandidates as any).mockResolvedValue({
      ...DETAIL, candidates: [...DETAIL.candidates, twins] });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderLedger(at(twins));
      const first = await (await sidebar()).findByRole("button", { name: "Keep Mara's map (first)" });
      const second = aside().getByRole("button", { name: "Keep Mara's map (second)" });
      expect(first).not.toBe(second);
      expect(main().getByRole("link", { name: "Mara's map (first)" }))
        .toHaveAttribute("href", "/campaigns/run/ledger/threads/mara%2Fmap");
      expect(main().getByRole("link", { name: "Mara's map (second)" }))
        .toHaveAttribute("href", "/campaigns/run/ledger/threads/mara-s-map-2");
      expect(aside().getByRole("button", {
        name: "Mara's map (second) continues Mara's map (first)" })).toBeInTheDocument();
      fireEvent.click(second);
      await waitFor(() => expect(api.applyCandidate).toHaveBeenCalledTimes(1));
      expect(applied()[0][2]).toEqual({
        op: "alias", canonical: twin.ref, expect_fingerprint: twins.fingerprint });
      expect(errors.mock.calls.filter((c) => /same key/.test(String(c[0])))).toHaveLength(0);
    } finally {
      errors.mockRestore();
    }
  });

  test("a scene the read could not name is left out; an untitled one says so", async () => {
    const gone = "003--the-dock";
    const untitled = "004--untitled";
    const coronation = record("thread:the-coronation", "The coronation", {
      last_scene: { id: gone, title: "" },
      beats: [{ scene: gone, text: "The crown was carried in." }],
    });
    const quiet = finding("possible_thread_closure-7777777777777777",
                          "possible_thread_closure", "closures", [coronation], {
      stale: true, stale_reason: "evidence",
      proposal: { decision: "close", from: "", to: "", relation: "", status: "",
                  reason: "The crown was placed.", evidence_scenes: [gone, untitled] },
    });
    (api.continuityCandidates as any).mockResolvedValue({
      ...DETAIL, scenes: [...SCENES, { id: untitled, title: "" }],
      candidates: [...DETAIL.candidates, quiet] });
    renderLedger(at(quiet));
    await sidebar();
    expect(main().getByText("The crown was carried in.")).toBeInTheDocument();
    expect(screen.getByRole("main").textContent).not.toMatch(/\d{3}--/);
    for (const link of main().getAllByRole("link")) {
      expect(link.getAttribute("href")).not.toContain(gone);
    }
    expect(main().getByRole("link", { name: "Untitled scene" }))
      .toHaveAttribute("href", `/campaigns/run/scenes/${untitled}`);
  });
});
