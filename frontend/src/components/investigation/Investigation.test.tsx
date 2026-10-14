// Roadmap 12's two reader-facing surfaces: the trace an investigation leaves
// (a finding's suggestion, an answer) and "Ask the history".
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { askHistory: vi.fn() } };
});
import { api } from "../../api/client";
import type { HistoryAnswer, InvestigationTrace as Trace } from "../../api/types";
import { AskHistory } from "./AskHistory";
import { InvestigationTrace, outcomeSentence } from "./InvestigationTrace";

const mocked = <T,>(fn: T) => fn as unknown as ReturnType<typeof vi.fn>;

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
    expect(screen.getByRole("link", { name: "001--docks" })).toBeInTheDocument();
    const usage = screen.getByText(/model turns/);
    expect(usage.textContent).toContain("not reported");
    expect(usage.textContent).toContain("modelled ≈ $0.0041");
    expect(usage.textContent).not.toContain("$0.00 ");
  });

  it("says a run that only finished read nothing", () => {
    renderTrace({ ...TRACE, steps: [TRACE.steps[2]], inspected: [] });
    expect(screen.getByText("It read nothing before it finished.")).toBeInTheDocument();
  });

  it("names the limit a stopped run ran into", () => {
    expect(outcomeSentence({ state: "budget_exhausted", limit: "context", reason: "",
                             error: "" })).toContain("context window");
    expect(outcomeSentence({ state: "refused", limit: "", reason: "unpriceable",
                             error: "" })).toContain("no price");
  });
});

describe("AskHistory", () => {
  beforeEach(() => vi.clearAllMocks());

  function ask(answer: HistoryAnswer) {
    mocked(api.askHistory).mockResolvedValue(answer);
    render(<MemoryRouter><AskHistory cid="run" onClose={() => {}} /></MemoryRouter>);
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
    expect(api.askHistory).toHaveBeenCalledWith("run", "Who keeps the harbour ledger?");
    expect(screen.getAllByRole("link", { name: "001--docks" }).length).toBeGreaterThan(0);
  });

  it("says so when the history could not answer", async () => {
    ask({ question: "Who keeps the harbour ledger?",
          outcome: { state: "insufficient_evidence", limit: "", reason: "", error: "",
                     result: null, trace: TRACE } });
    expect(await screen.findByText(/could not find enough/)).toBeInTheDocument();
  });

  it("asks nothing for an empty question", () => {
    render(<MemoryRouter><AskHistory cid="run" onClose={() => {}} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });
});
