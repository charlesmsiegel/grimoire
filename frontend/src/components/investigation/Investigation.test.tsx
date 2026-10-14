// Roadmap 12's two reader-facing surfaces: the trace an investigation leaves
// (a finding's suggestion, an answer) and "Ask the history".
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual,
           api: { askHistory: vi.fn(), findHistoryQuestion: vi.fn(),
                  followHistoryQuestion: vi.fn(), listScenes: vi.fn(),
                  getCampaignBudget: vi.fn() } };
});
import { api, ApiError, RunEndedError } from "../../api/client";
import type { HistoryAnswer, InvestigationTrace as Trace } from "../../api/types";
import { AskHistory } from "./AskHistory";
import { InvestigationTrace, outcomeSentence } from "./InvestigationTrace";

const mocked = <T,>(fn: T) => fn as unknown as ReturnType<typeof vi.fn>;

/** Where AskHistory remembers a question: per library and campaign. */
const QUESTION_KEY = 'grimoire.askHistory:["/lib","run"]';

const TRACE: Trace = {
  version: 1, run: "r1", mode: "question", task: "investigation-question",
  model: "openrouter/vendor/reader", started: "2026-10-14T00:00:00Z", outcome: "completed",
  limit: "",
  steps: [
    { turn: 1, tool: "search_history", args: { query: "harbour ledger", max: 3 },
      result: { ok: true, refs: ["scene:001--docks"], chars: 400, truncated: false } },
    { turn: 2, tool: "get_scene_excerpt", args: { scene: "scene:001--docks", query: "ledger" },
      result: { ok: true, refs: ["scene:001--docks"], chars: 900, truncated: true } },
    { turn: 3, tool: "answer", args: { citations: ["scene:001--docks"] },
      result: { ok: true, refs: [], chars: 0, truncated: false } },
  ],
  inspected: ["scene:001--docks"],
  final: null,
  usage: { model_turns: 3, tool_calls: 2, decisions: 0, seconds: 4.2,
           cost: { cost_usd: null, estimated_usd: null, modelled_usd: 0.0041 },
           unpriced_calls: 0 },
};

function renderTrace(trace: Trace) {
  return render(<MemoryRouter>
    <InvestigationTrace trace={trace} sceneLink={(sid) => <a href={`#${sid}`}>{sid}</a>} />
  </MemoryRouter>);
}

describe("InvestigationTrace", () => {
  it("says what was read, in steps, and never a zero nobody reported", () => {
    renderTrace(TRACE);
    expect(screen.getByText("Searched the history")).toBeInTheDocument();
    expect(screen.getByText(/harbour ledger/)).toBeInTheDocument();
    expect(screen.getByText("(cut short)")).toBeInTheDocument();
    // The call it ended on is the last step.
    expect(screen.getByText("Answered")).toBeInTheDocument();
    // The excerpt's scene is its link, and the scenes-read line names it too.
    expect(screen.getAllByRole("link", { name: "001--docks" })).toHaveLength(2);
    const usage = screen.getByText(/model turns/);
    expect(usage.textContent).toContain("not reported");
    expect(usage.textContent).toContain("modelled ≈ $0.0041");
    expect(usage.textContent).not.toContain("$0.00 ");
  });

  it("says a run that only finished read nothing", () => {
    renderTrace({ ...TRACE, steps: [TRACE.steps[2]], inspected: [] });
    expect(screen.getByText("It read nothing before it finished.")).toBeInTheDocument();
  });

  it("never shows a record's raw ref", () => {
    renderTrace({ ...TRACE, steps: [
      { turn: 1, tool: "get_record", args: { ref: "thread:find-the-harbour-ledger" },
        result: { ok: true, refs: ["thread:find-the-harbour-ledger"], chars: 300,
                  truncated: false } },
      ...TRACE.steps] });
    expect(screen.getByText("Looked up a record")).toBeInTheDocument();
    expect(screen.queryByText(/thread:find-the-harbour-ledger/)).toBeNull();
    expect(screen.queryByText(/scene:001--docks/)).toBeNull();
  });

  it("names the limit a stopped run ran into", () => {
    expect(outcomeSentence({ state: "budget_exhausted", limit: "context", reason: "",
                             error: "" })).toContain("context window");
    expect(outcomeSentence({ state: "refused", limit: "", reason: "unpriceable",
                             error: "" })).toContain("no price");
  });
});

describe("AskHistory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocked(api.listScenes).mockResolvedValue([{ id: "001--docks", title: "The docks", model: "",
                                                 created: "", updated: "", date: "" }]);
    mocked(api.getCampaignBudget).mockResolvedValue(
      { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 });
  });

  function ask(answer: HistoryAnswer) {
    mocked(api.askHistory).mockResolvedValue(answer);
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  }

  it("shows the answer with the scenes it read", async () => {
    ask({ question: "Who keeps the harbour ledger?",
          outcome: { state: "completed", limit: "", reason: "", error: "",
                     result: { text: "Mara keeps it.", citations: ["scene:001--docks"] },
                     trace: TRACE } });
    expect(await screen.findByText("Mara keeps it.")).toBeInTheDocument();
    expect(api.askHistory).toHaveBeenCalledWith("run", "Who keeps the harbour ledger?",
                                                expect.any(AbortSignal), expect.any(String));
    // Cited and read scenes are named by their titles, never their ids.
    expect((await screen.findAllByRole("link", { name: "The docks" })).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "001--docks" })).toBeNull();
  });

  it("says so when the history could not answer", async () => {
    ask({ question: "Who keeps the harbour ledger?",
          outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                     result: null, trace: TRACE } });
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
  });

  it("will not close while its question is being answered", async () => {
    let answer: (a: HistoryAnswer) => void = () => {};
    mocked(api.askHistory).mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const onClose = vi.fn();
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={onClose} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    answer({ question: "Who keeps the harbour ledger?",
             outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                        result: null, trace: TRACE } });
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("re-attaches to a question still running after it was unmounted", async () => {
    mocked(api.askHistory).mockReturnValue(new Promise(() => {}));
    const first = render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    const [, , , attempt] = mocked(api.askHistory).mock.calls[0];
    expect(typeof attempt).toBe("string");
    first.unmount();

    const held = { id: "r1", state: "running" };
    mocked(api.findHistoryQuestion).mockResolvedValue(held);
    mocked(api.followHistoryQuestion).mockResolvedValue({
      question: "Who keeps the harbour ledger?",
      outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                 result: null, trace: TRACE } });
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
    expect(api.findHistoryQuestion).toHaveBeenCalledWith("run", attempt, expect.any(AbortSignal));
    // The held run, followed as it stands: no second POST, no second question.
    expect(api.followHistoryQuestion).toHaveBeenCalledWith("run", held, attempt,
                                                           expect.any(AbortSignal));
    expect(api.askHistory).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Your question")).toHaveValue("Who keeps the harbour ledger?");
    expect(localStorage.getItem(QUESTION_KEY)).toBeNull();
  });

  it("never asks again for a question the server no longer holds", async () => {
    localStorage.setItem(QUESTION_KEY,
                         JSON.stringify({ attempt: "a-old", question: "Who keeps the ledger?" }));
    mocked(api.findHistoryQuestion).mockResolvedValue(null);
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/no longer held/)).toBeInTheDocument();
    expect(api.askHistory).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Your question")).toHaveValue("Who keeps the ledger?");
    expect(localStorage.getItem(QUESTION_KEY)).toBeNull();
  });

  it("keeps a question whose failure carried no reply, and re-asks it as the same run", async () => {
    mocked(api.askHistory).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/Failed to fetch|reach/)).toBeInTheDocument();
    const [, , , attempt] = mocked(api.askHistory).mock.calls[0];
    // Nothing proved the run absent, so it is still remembered...
    expect(JSON.parse(localStorage.getItem(QUESTION_KEY) ?? "null"))
      .toEqual({ attempt, question: "Who keeps the harbour ledger?" });
    // ...and asking it again goes out under the same attempt, which the
    // server adopts rather than starting (and paying for) a second run.
    mocked(api.askHistory).mockResolvedValueOnce({
      question: "Who keeps the harbour ledger?",
      outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                 result: null, trace: TRACE } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
    expect(mocked(api.askHistory).mock.calls[1][3]).toBe(attempt);
    expect(localStorage.getItem(QUESTION_KEY)).toBeNull();
  });

  it("forgets a question the server answered with a refusal", async () => {
    mocked(api.askHistory).mockRejectedValueOnce(
      new ApiError(409, "the question switch is off", "off"));
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/switch is off/)).toBeInTheDocument();
    expect(localStorage.getItem(QUESTION_KEY)).toBeNull();
  });

  it("shows the campaign's budget level where a question is asked", async () => {
    mocked(api.getCampaignBudget).mockResolvedValue(
      { limit_usd: 5, period: "monthly", level: "over", warn_fraction: 0.8, spent_usd: 6,
        unpriced_calls: 2 });
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(
      "Campaign budget: $6.00 of $5.00 spent this month, not counting 2 unpriced calls. "
      + "Over budget: an investigation still runs, and adds to it.")).toBeInTheDocument();
  });

  it("follows a running question with the switch off, and starts none", async () => {
    localStorage.setItem(QUESTION_KEY,
                         JSON.stringify({ attempt: "a-old", question: "Who keeps the ledger?" }));
    mocked(api.findHistoryQuestion).mockResolvedValue({ id: "r1", state: "running" });
    mocked(api.followHistoryQuestion).mockResolvedValue({
      question: "Who keeps the ledger?",
      outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                 result: null, trace: TRACE } });
    render(<MemoryRouter><AskHistory cid="run" root="/lib" canAsk={false} onClose={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
    expect(screen.getByText("Asking is switched off in Settings.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("never shows another library's remembered question", () => {
    localStorage.setItem(QUESTION_KEY,
                         JSON.stringify({ attempt: "a-old", question: "Who keeps the ledger?" }));
    render(<MemoryRouter><AskHistory cid="run" root="/elsewhere" onClose={() => {}} />
      </MemoryRouter>);
    expect(screen.getByLabelText("Your question")).toHaveValue("");
    expect(api.findHistoryQuestion).not.toHaveBeenCalled();
  });

  it("starts no other question over one still unresolved, until it is forgotten", async () => {
    mocked(api.askHistory).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/Failed to fetch|reach/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who sold the ferry?" } });
    expect(screen.getByText(/An earlier question may still be being answered/))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Forget it" }));
    expect(localStorage.getItem(QUESTION_KEY)).toBeNull();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(api.askHistory).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["a 5xx once the client's retries ran out", new ApiError(503, "unavailable"), true],
    ["the run seen to end failed, whatever its status", new RunEndedError(502, "upstream", "network"),
     false],
  ])("keeps the question only while nothing proved it over: %s", async (_label, err, kept) => {
    mocked(api.askHistory).mockRejectedValueOnce(err);
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("Your question"),
                     { target: { value: "Who keeps the harbour ledger?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(localStorage.getItem(QUESTION_KEY) !== null).toBe(kept);
  });

  it("asks nothing for an empty question", () => {
    render(<MemoryRouter><AskHistory cid="run" root="/lib" onClose={() => {}} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });
});
