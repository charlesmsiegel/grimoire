import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: {
    getStats: vi.fn(),
    getErrorSummary: vi.fn(),
    getLogs: vi.fn(),
    getLogLevel: vi.fn(),
    streamLogTail: vi.fn(),
  },
}));
vi.mock("../api/errors", () => ({ errorText: (e: unknown) => String(e) }));

import { api } from "../api/client";
import StatsView from "./StatsView";

const bucket = (over: Record<string, unknown> = {}) => ({
  key: "chat", calls: 4, errors: 0, error_rate: 0, sampled: false,
  p50: 250, p90: 380, p99: 400, min: 100, max: 400, ...over,
});

const ERRORS = {
  since: "2026-07-23", until: "2026-08-21", days: 30,
  total: 3,
  modules: [{ module: "dossier", count: 2,
              kinds: [{ kind: "empty_reply", count: 2 }],
              last: "2026-08-21T10:00:00.000Z", last_detail: "Mara came back empty" },
            { module: "chat", count: 1,
              kinds: [{ kind: "rate_limit", count: 1 }],
              last: "2026-08-20T10:00:00.000Z", last_detail: "429 Too Many Requests" }],
  kinds: [{ kind: "empty_reply", count: 2 }, { kind: "rate_limit", count: 1 }],
  daily: [{ day: "2026-08-20", count: 1 }, { day: "2026-08-21", count: 2 }],
  rows: [{ ts: "2026-08-21T10:00:00.000Z", level: "error", module: "dossier",
           message: "Mara came back empty", kind: "empty_reply" }],
  truncated: false,
};

const STATS = {
  days: 30, since: "2026-07-23", until: "2026-08-21", campaign: "",
  generated_at: "2026-08-21T12:00:00Z",
  percentiles: [50, 90, 99],
  totals: bucket({ key: "", calls: 5, errors: 1, error_rate: 0.2 }),
  by_task: [bucket(), bucket({ key: "dossier", calls: 1, p50: 9000, p90: 9000, p99: 9000, max: 9000 })],
  by_model: [bucket({ key: "realm/opus" })],
  by_day: [bucket({ key: "2026-08-20", calls: 2, p50: 150 }),
           bucket({ key: "2026-08-21", calls: 3, p50: 9000 })],
  errors: ERRORS,
};

const PAGE = {
  rows: [
    { ts: "2026-08-21T10:00:00.000Z", level: "error", module: "dossier",
      message: "Mara came back empty", kind: "empty_reply" },
    { ts: "2026-08-21T09:00:00.000Z", level: "info", module: "runner",
      message: "started a turn" },
  ],
  modules: ["dossier", "runner"],
  counts: { debug: 0, info: 1, warning: 0, error: 1, critical: 0 },
  total: 2, truncated: false, level: "debug",
  since: "2026-07-23", until: "2026-08-21",
  levels: ["debug", "info", "warning", "error", "critical"],
};

const view = () => render(<MemoryRouter><StatsView /></MemoryRouter>);

beforeEach(() => {
  // Call counts are not reset between tests by this project's vitest config,
  // and half the assertions here are about how many times a stream was opened.
  vi.clearAllMocks();
  vi.mocked(api.getStats).mockResolvedValue(structuredClone(STATS) as never);
  vi.mocked(api.getLogs).mockResolvedValue(structuredClone(PAGE) as never);
  vi.mocked(api.getErrorSummary).mockResolvedValue(structuredClone(ERRORS) as never);
  vi.mocked(api.getLogLevel).mockResolvedValue(
    { level: "info", levels: ["debug", "info", "warning", "error"] } as never);
  vi.mocked(api.streamLogTail).mockReturnValue(new Promise(() => {}) as never);
});

// ---- performance (#154) ----
it("lands on the performance readings", async () => {
  view();

  expect(await screen.findByRole("heading", { name: "Performance" })).toBeInTheDocument();
  const cards = screen.getByText("Median").closest("dl")!;
  expect(within(cards).getByText("Median").closest("div")).toHaveTextContent("250ms");
  // The tail is the whole point of a percentile, so p90 and p99 are headline
  // numbers rather than something behind a control.
  expect(within(cards).getByText("p90").closest("div")).toHaveTextContent("380ms");
  expect(within(cards).getByText("p99").closest("div")).toHaveTextContent("400ms");
});

it("breaks latency down by task and by model", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });

  const byTask = screen.getByText("By task").parentElement!;
  const byModel = screen.getByText("By model").parentElement!;
  expect(within(byTask).getByText("dossier")).toBeInTheDocument();
  expect(within(byModel).getByText("realm/opus")).toBeInTheDocument();
  // Seconds past a second, milliseconds under one: the trailing digits of
  // 9000ms mean nothing to a reader.
  expect(within(byTask).getAllByText("9.0s").length).toBeGreaterThan(0);
});

it("shows a failure rate against the calls that succeeded", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });

  expect(screen.getByText("Failed calls").closest("div")).toHaveTextContent("1 · 20.0%");
});

it("says so when the window held no calls at all, rather than showing bare zeroes", async () => {
  vi.mocked(api.getStats).mockResolvedValue({
    ...structuredClone(STATS), totals: bucket({ key: "", calls: 0, p50: 0, p90: 0, p99: 0, max: 0 }),
    by_task: [], by_model: [], by_day: [],
  } as never);
  view();

  expect(await screen.findByText(/No calls in this window yet/)).toBeInTheDocument();
});

it("re-reads the window when the day control moves", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  expect(api.getStats).toHaveBeenCalledWith(30);

  fireEvent.change(screen.getByLabelText("How many days to report on"),
                   { target: { value: "7" } });

  await waitFor(() => expect(api.getStats).toHaveBeenCalledWith(7));
});

// ---- errors (#156) ----
it("aggregates errors per module, with each module's own kinds", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });

  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));

  const byModule = (await screen.findByText("By module")).parentElement!;
  const row = within(byModule).getByText("dossier").closest("tr")!;
  expect(within(row).getByText("empty_reply 2")).toBeInTheDocument();
  expect(within(row).getByText("Mara came back empty")).toBeInTheDocument();
});

it("says nothing has gone wrong rather than drawing an empty table", async () => {
  vi.mocked(api.getErrorSummary).mockResolvedValue(
    { ...ERRORS, total: 0, modules: [], kinds: [], daily: [], rows: [] });
  vi.mocked(api.getStats).mockResolvedValue({
    ...structuredClone(STATS),
    errors: { ...ERRORS, total: 0, modules: [], kinds: [], daily: [], rows: [] },
  });
  view();
  await screen.findByRole("heading", { name: "Performance" });

  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));

  expect(await screen.findByText(/Nothing has gone wrong in this window/)).toBeInTheDocument();
});

it("filters the error report to one module, from its own read", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));
  await screen.findByText("By module");

  fireEvent.change(screen.getByLabelText("Module to report on"),
                   { target: { value: "dossier" } });

  await waitFor(() => expect(api.getErrorSummary).toHaveBeenLastCalledWith(
    30, { module: "dossier" }));
});

it("keeps every module in the picker after one of them is picked", async () => {
  vi.mocked(api.getErrorSummary).mockResolvedValue(
    { ...ERRORS, total: 2, modules: [ERRORS.modules[0]] } as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));

  // The options come from the unfiltered stats copy, so narrowing to one
  // module cannot strip the control of every way back out.
  const picker = await screen.findByLabelText("Module to report on");
  expect(within(picker).getByRole("option", { name: "chat" })).toBeInTheDocument();
});

it("never shows whole-library numbers under a filtered heading", async () => {
  let release: (v: unknown) => void = () => {};
  vi.mocked(api.getErrorSummary).mockImplementation((() =>
    new Promise((res) => { release = res; })) as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));
  await screen.findByLabelText("Module to report on");

  fireEvent.change(screen.getByLabelText("Module to report on"),
                   { target: { value: "dossier" } });

  // The filtered read has not landed. The unfiltered copy from /stats must
  // not stand in for it — a moment of "reading…" is the honest answer.
  await screen.findByText(/Reading the log/);
  expect(screen.queryByText("By module")).not.toBeInTheDocument();

  release({ ...ERRORS, total: 2, modules: [ERRORS.modules[0]] });
  expect(await screen.findByText("By module")).toBeInTheDocument();
});

it("says which module is empty, and offers the way back", async () => {
  vi.mocked(api.getErrorSummary).mockResolvedValue(
    { ...ERRORS, total: 0, modules: [], kinds: [], daily: [], rows: [] });
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));
  await screen.findByText(/Nothing has gone wrong/);

  fireEvent.change(screen.getByLabelText("Module to report on"),
                   { target: { value: "dossier" } });

  expect(await screen.findByText(/Nothing has gone wrong in dossier/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "every module" })).toBeInTheDocument();
});

// ---- the log (#155) ----
it("reads the log only once its section is opened", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  expect(api.getLogs).not.toHaveBeenCalled();
  // ...and says nothing about its size rather than showing the dash that means
  // "still reading", which would claim a read nobody started.
  expect(screen.getByRole("button", { name: /Debug log/ })).not.toHaveTextContent("—");

  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));

  await waitFor(() => expect(api.getLogs).toHaveBeenCalled());
  expect(await screen.findByText("started a turn")).toBeInTheDocument();
});

it("applies the page's Window control to the log too", async () => {
  // The footer said "Last 90 days" while the log read thirty, and the rail
  // printed that thirty-day total beside it.
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await waitFor(() => expect(api.getLogs).toHaveBeenLastCalledWith(
    expect.objectContaining({ days: 30 })));

  fireEvent.change(screen.getByLabelText("How many days to report on"),
                   { target: { value: "90" } });

  await waitFor(() => expect(api.getLogs).toHaveBeenLastCalledWith(
    expect.objectContaining({ days: 90 })));
});

it("counts the whole window in the rail, never the module filter", async () => {
  // A rail row labelled just "Errors" showing one module's total is a number
  // that does not say what it is counting.
  vi.mocked(api.getErrorSummary).mockResolvedValue(
    { ...ERRORS, total: 2, modules: [ERRORS.modules[0]] } as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));
  await screen.findByText("By module");

  fireEvent.change(screen.getByLabelText("Module to report on"),
                   { target: { value: "dossier" } });

  await waitFor(() => expect(api.getErrorSummary).toHaveBeenLastCalledWith(
    30, { module: "dossier" }));
  // 3 is the window's total from /stats; 2 is the filtered read.
  expect(screen.getByRole("button", { name: /Errors/ })).toHaveTextContent("3");
});

it("builds its filter dropdowns from the window, not from the page", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));

  const modules = await screen.findByLabelText("Module to show");
  expect(within(modules).getByRole("option", { name: "dossier" })).toBeInTheDocument();
  expect(within(modules).getByRole("option", { name: "runner" })).toBeInTheDocument();
});

it("re-reads with the filters a user picked", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");

  fireEvent.change(screen.getByLabelText("Quietest level to show"),
                   { target: { value: "warning" } });
  fireEvent.change(screen.getByLabelText("Module to show"),
                   { target: { value: "runner" } });
  fireEvent.change(screen.getByLabelText("Filter by text"),
                   { target: { value: "turn" } });

  await waitFor(() => expect(api.getLogs).toHaveBeenLastCalledWith(
    expect.objectContaining({ level: "warning", module: "runner", q: "turn" })));
});

it("heads each day's rows with its date", async () => {
  // Rows are ordered by full timestamp but show only a clock, so a window
  // spanning two days reads as scrambled without this: 09:17 today sits above
  // 14:22 yesterday and looks misplaced.
  vi.mocked(api.getLogs).mockResolvedValue({
    ...structuredClone(PAGE),
    rows: [
      { ts: "2026-08-21T09:17:00.000Z", level: "info", module: "runner", message: "today" },
      { ts: "2026-08-20T14:22:01.900Z", level: "info", module: "runner", message: "yesterday" },
    ],
  } as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));

  const rows = (await screen.findByText("today")).closest("ol")!;
  const text = within(rows).getAllByRole("listitem").map((li) => li.textContent);
  expect(text[0]).toBe("2026-08-21");
  expect(text[1]).toContain("today");
  expect(text[2]).toBe("2026-08-20");
  expect(text[3]).toContain("yesterday");
});

it("shows the traceback the bridge captured, collapsed", async () => {
  vi.mocked(api.getLogs).mockResolvedValue({
    ...structuredClone(PAGE),
    rows: [{ ts: "2026-08-21T10:00:00.000Z", level: "error", module: "store.fork",
             message: "could not fork", kind: "ValueError",
             trace: "Traceback...\nValueError: bad frontmatter" }],
  } as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));

  // Recorded since the first version and rendered by nothing until now: the
  // most useful half of an error row was write-only.
  const trace = await screen.findByText("traceback");
  expect(trace.closest("details")).not.toHaveAttribute("open");
  expect(within(trace.closest("details")!).getByText(/bad frontmatter/)).toBeInTheDocument();
});

it("stops reporting a failure that has stopped happening", async () => {
  vi.mocked(api.getLogs).mockRejectedValueOnce(new Error("the log is unreadable"));
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  expect(await screen.findByText("Error: the log is unreadable")).toBeInTheDocument();

  // A read that now works must take the banner down. An error message that
  // outlives its error is a page lying about the present.
  fireEvent.change(screen.getByLabelText("Quietest level to show"),
                   { target: { value: "warning" } });

  await waitFor(() =>
    expect(screen.queryByText("Error: the log is unreadable")).not.toBeInTheDocument());
});

it("reopens the live tail once per typed word, not once per letter", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    view();
    await screen.findByRole("heading", { name: "Performance" });
    fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
    await screen.findByLabelText("Module to show");
    fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));
    await waitFor(() => expect(api.streamLogTail).toHaveBeenCalledTimes(1));

    for (const q of ["r", "ra", "rat", "rate"]) {
      fireEvent.change(screen.getByLabelText("Filter by text"), { target: { value: q } });
    }
    vi.advanceTimersByTime(400);

    await waitFor(() => expect(api.streamLogTail).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "rate" }), expect.anything(), expect.anything()));
    // One reconnect for the word, not one per keystroke.
    expect(api.streamLogTail).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("warns that a quieter line was never written, which is why it is not here", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));

  expect(await screen.findByText(/Recording at/)).toHaveTextContent("info");
});

it("says nothing about the threshold when everything is being recorded", async () => {
  vi.mocked(api.getLogLevel).mockResolvedValue(
    { level: "debug", levels: PAGE.levels } as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");

  expect(screen.queryByText(/Recording at/)).not.toBeInTheDocument();
});

// ---- the live tail ----
it("opens no stream until Live is switched on", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");
  expect(api.streamLogTail).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));

  await waitFor(() => expect(api.streamLogTail).toHaveBeenCalled());
});

it("appends tailed rows newest first, like the page they sit above", async () => {
  vi.mocked(api.streamLogTail).mockImplementation(((_opts: unknown, onEvent: (e: unknown) => void) => {
    onEvent({ cursor: "2026-08.jsonl:10", rows: [
      { ts: "2026-08-21T11:00:00.000Z", level: "info", module: "runner", message: "older" },
      { ts: "2026-08-21T11:00:01.000Z", level: "warning", module: "runner", message: "newer" },
    ] });
    return new Promise(() => {});
  }) as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");

  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));

  const live = (await screen.findByText("Live", { selector: "h2" })).parentElement!;
  // Skipping the day heading the list puts at the top of each day's block.
  const rows = within(live).getAllByRole("listitem")
    .map((li) => li.textContent ?? "").filter((t) => !/^\d{4}-\d{2}-\d{2}$/.test(t));
  expect(rows[0]).toContain("newer");
  expect(rows[1]).toContain("older");
});

it("reports a tail that cannot read the log without tearing it down", async () => {
  vi.mocked(api.streamLogTail).mockImplementation(((_o: unknown,
                                                    onEvent: (e: unknown) => void) => {
    onEvent({ cursor: "c", error: { detail: "the log went away", kind: "log_unreadable" } });
    onEvent({ cursor: "c2", rows: [
      { ts: "2026-08-21T11:00:00.000Z", level: "info", module: "runner", message: "back" }] });
    return new Promise(() => {});
  }) as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");

  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));

  // Rows that arrive after it recovers clear the report; the stream never
  // stopped, so neither should the panel.
  expect(await screen.findByText("back")).toBeInTheDocument();
  expect(screen.queryByText("the log went away")).not.toBeInTheDocument();
});

it("takes the failure banner down when the log comes back, even if it is quiet", async () => {
  // Rows only arrive when there are rows, so a recovered-but-silent log used
  // to leave the banner standing until something happened to log.
  vi.mocked(api.streamLogTail).mockImplementation(((_o: unknown,
                                                    onEvent: (e: unknown) => void) => {
    onEvent({ cursor: "c", error: { detail: "the log went away", kind: "log_unreadable" } });
    onEvent({ cursor: "c2" });                       // recovery: a bare cursor
    return new Promise(() => {});
  }) as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");

  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));

  await waitFor(() =>
    expect(screen.queryByText("the log went away")).not.toBeInTheDocument());
});

it("keeps a chosen module selectable when the window holds nothing for it", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Errors/ }));
  await screen.findByLabelText("Module to report on");
  vi.mocked(api.getErrorSummary).mockResolvedValue(
    { ...ERRORS, total: 0, modules: [], kinds: [], daily: [], rows: [] });

  fireEvent.change(screen.getByLabelText("Module to report on"),
                   { target: { value: "dossier" } });

  // Without this the select falls back to showing "every module" while it is
  // still filtering by one.
  const picker = await screen.findByLabelText("Module to report on");
  expect(picker).toHaveValue("dossier");
});

it("reopens the tail against the new filter rather than keeping the old one", async () => {
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");
  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));
  await waitFor(() => expect(api.streamLogTail).toHaveBeenCalledTimes(1));

  fireEvent.change(screen.getByLabelText("Quietest level to show"),
                   { target: { value: "error" } });

  await waitFor(() => expect(api.streamLogTail).toHaveBeenLastCalledWith(
    expect.objectContaining({ level: "error" }), expect.anything(), expect.anything()));
});

it("does not report the abort it caused by switching Live off", async () => {
  vi.mocked(api.streamLogTail).mockImplementation(((_o: unknown, _e: unknown, signal: AbortSignal) =>
    new Promise((_res, rej) => {
      signal.addEventListener("abort", () => rej(new Error("aborted")));
    })) as never);
  view();
  await screen.findByRole("heading", { name: "Performance" });
  fireEvent.click(screen.getByRole("button", { name: /Debug log/ }));
  await screen.findByLabelText("Module to show");
  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));
  await waitFor(() => expect(api.streamLogTail).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("checkbox", { name: "Live" }));

  // The rejection is this page's own cleanup arriving; reporting it would put
  // an error on screen every time a user stopped watching.
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Live" }))
    .not.toBeChecked());
  expect(screen.queryByText(/aborted/)).not.toBeInTheDocument();
});

// ---- failure ----
it("reports a stats read that failed instead of spinning forever", async () => {
  vi.mocked(api.getStats).mockRejectedValue(new Error("the ledger is unreadable"));
  view();

  expect(await screen.findByText("Error: the ledger is unreadable")).toBeInTheDocument();
});
