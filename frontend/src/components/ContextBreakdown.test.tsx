import { render, screen } from "@testing-library/react";
import { ContextBreakdown } from "./ContextBreakdown";
import { type SceneContext } from "../api/client";

const ctx = (over: Partial<SceneContext> = {}): SceneContext => ({
  model: "gpt-4", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
  sections: [{ id: "world", label: "World info", text: "lore", tokens: 100,
               tier: "spotlight", dropped: false, trimmed: 0 }],
  ...over,
});
const models = [{ id: "gpt-4", name: "GPT-4", context: 200, prompt: null, completion: null }];

test("captured prompt-field warnings stay outside sent rows and token accounting", () => {
  const { rerender, container } = render(<ContextBreakdown models={models} ctx={ctx({
    prompt_field_statuses: { world_default_prompt: "unreadable", campaign_default_prompt: "ok", scene_focus: "unreadable" },
  })} />);
  expect(screen.getByText(/world default prompt.*could not be read/i)).toBeInTheDocument();
  expect(screen.getByText(/scene focus.*could not be read/i)).toBeInTheDocument();
  expect(container.querySelectorAll("details.ctx-section")).toHaveLength(1);
  expect(screen.getByText(/100.*200 tok/)).toBeInTheDocument();
  rerender(<ContextBreakdown models={models} ctx={ctx()} />);
  expect(screen.queryByText(/could not be read/i)).toBeNull();
});

test("a count made with the model's own tokenizer is shown plain", () => {
  render(<ContextBreakdown models={models}
                           ctx={ctx({ token_count: { tokenizer: "cl100k_base", native: true } })} />);
  expect(screen.getByText("100 / 200 tok")).toBeInTheDocument();
  expect(screen.queryByText(/Estimated/)).toBeNull();
});

test("another model's tokenizer marks the counts as estimates and says whose", () => {
  render(<ContextBreakdown models={models} ctx={ctx({
    model: "anthropic/claude-sonnet-4.5", token_count: { tokenizer: "cl100k_base", native: false },
  })} />);
  expect(screen.getByText("≈ 100 tok")).toBeInTheDocument();
  expect(screen.getByText(/counted with cl100k_base .* anthropic\/claude-sonnet-4\.5 uses its own/))
    .toBeInTheDocument();
});

test("the length heuristic says it is one", () => {
  render(<ContextBreakdown models={models}
                           ctx={ctx({ token_count: { tokenizer: "heuristic", native: false } })} />);
  expect(screen.getByText("≈ 100 / 200 tok")).toBeInTheDocument();
  expect(screen.getByText(/about four characters each/)).toBeInTheDocument();
});

test("a snapshot that names no tokenizer is an estimate, not exact", () => {
  render(<ContextBreakdown models={models} ctx={ctx()} />);
  expect(screen.getByText("≈ 100 / 200 tok")).toBeInTheDocument();
  expect(screen.getByText(/may not be gpt-4's own/)).toBeInTheDocument();
});

test("a compose that fell back on some strings says it is partly a length count", () => {
  render(<ContextBreakdown models={models}
                           ctx={ctx({ token_count: { tokenizer: "mixed", native: false } })} />);
  expect(screen.getByText(/partly counted with cl100k_base/)).toBeInTheDocument();
});

test("the inspector says which preset parameters a turn could not send", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sampling: {
    preset_id: "warm", preset_name: "Warm", scope: "connection", kind: "claude",
    applied: {}, dropped: [{ param: "temperature",
                             reason: "the Claude Agent SDK takes no sampling options" }],
    verified: true } })} />);
  expect(screen.getByText("Sampler: Warm (from the provider)")).toBeInTheDocument();
  expect(screen.getByText("Not sent: temperature")).toBeInTheDocument();
});

test("a snapshot frozen before presets existed renders no sampler line", () => {
  render(<ContextBreakdown models={[]} ctx={ctx()} />);
  expect(screen.queryByText(/Sampler:/)).toBeNull();
});

test("a decision's outcome is its own row: never sent, so no token count", () => {
  // The speaker pick files what it decided as a last `decision` section of
  // zero tokens (Task 6). It is the call's answer, not a part of the prompt.
  const { container } = render(<ContextBreakdown models={models} ctx={ctx({
    sections: [
      { id: "world", label: "World info", text: "lore", tokens: 100, tier: "spotlight",
        dropped: false, trimmed: 0 },
      { id: "decision", label: "decision", text: '{"backend": "native"}', tokens: 0,
        tier: "lock-in", dropped: false, trimmed: 0 },
    ],
  })} />);
  const row = screen.getByText("decision").closest("summary")!;
  expect(row).toHaveTextContent("outcome · not sent");
  expect(row).not.toHaveTextContent(/\d/);
  expect(screen.getByText('{"backend": "native"}')).toBeInTheDocument();
  // No share of the prompt's bar: only the World info row draws one.
  expect(container.querySelectorAll(".ctx-mini")).toHaveLength(1);
  // A prompt section keeps its count.
  expect(screen.getByText("World info").closest("summary")).toHaveTextContent("100 · 50%");
});

// ---- 01i: the breakdown's own model window ----
const NATIVE = { tokenizer: "cl100k_base", native: true };

test("the breakdown's own model window is preferred over the catalog", () => {
  render(<ContextBreakdown models={models} ctx={ctx({
    token_count: NATIVE, model_window: { value: 400, source: "user" } })} />);
  expect(screen.getByText("100 / 400 tok")).toBeInTheDocument();
});

test("a window the backend resolved needs no catalog at all", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({
    token_count: NATIVE, model_window: { value: 1000, source: "catalog" } })} />);
  expect(screen.getByText("100 / 1,000 tok")).toBeInTheDocument();
});

test("an older snapshot falls back to the catalog lookup", () => {
  render(<ContextBreakdown models={models} ctx={ctx({ token_count: NATIVE })} />);
  expect(screen.getByText("100 / 200 tok")).toBeInTheDocument();
});

test("a window the backend says is unknown is not looked up", () => {
  render(<ContextBreakdown models={models} ctx={ctx({
    token_count: NATIVE, model_window: { value: null, source: "unknown" } })} />);
  expect(screen.getByText("100 tok")).toBeInTheDocument();
});

test("the smaller of budget and window still bounds the bar", () => {
  const { unmount } = render(<ContextBreakdown models={models} ctx={ctx({
    token_count: NATIVE, budget_tokens: 250, model_window: { value: 4000, source: "catalog" } })} />);
  expect(screen.getByText("100 / 250 tok")).toBeInTheDocument();
  unmount();
  render(<ContextBreakdown models={models} ctx={ctx({
    token_count: NATIVE, budget_tokens: 8000, model_window: { value: 500, source: "user" } })} />);
  expect(screen.getByText("100 / 500 tok")).toBeInTheDocument();
});


test("history inspector shows raw signals, rounds, excerpts, coverage and shed status", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sections: [{
    id: "history_recall", label: "Recalled history", text: "Mara kept the ledger.", tokens: 90,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: {
      items: [{ scene: "001--saltmarch", identity: null, key: "sid:001--saltmarch", title: "Saltmarch",
        tier: 1, admitted_by: ["structural", "lexical"],
        rounds: [{ structural: [{ relation: "relationship", refs: ["characters:mara"] }],
          lexical: { score: 4.2, matched: ["silver key"] }, semantic: { cosine: 0.61, query: 0 },
          rerank: { level: "high", position: 1, backend: "native" } }],
        ranks: [{ round: 0, signal: "lexical", rank: 3 }], excerpts: [[41, 43]],
        summary: "omitted:archive", tokens: 90, shed: true }],
      coverage: { tiers: [1], semantic: "skipped:preview", admitted: 5, agreement: 2, verdict: "sufficient" },
      ceiling: 1200, budget_from: ["config"],
    },
  }] })} />);
  for (const value of [/Ceiling: 1,200/, /sufficient/, /skipped:preview/, /relationship.*characters:mara/,
    /lexical.*rank 3/, /4.2.*silver key/, /cosine 0.61/, /high.*native/, /41.*43/,
    /omitted:archive/, /shed: budget/]) expect(screen.getByText(value)).toBeInTheDocument();
});

test("an empty history row still says why, rerank status included", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sections: [{
    id: "history_recall", label: "Recalled history", text: "", tokens: 0,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: {
      items: [],
      coverage: { tiers: [1], semantic: "failed:network", admitted: 0, agreement: 0,
                  verdict: "error", rerank: "skipped:unanswered" },
      ceiling: 1200, budget_from: ["config"],
    },
  }] })} />);
  for (const value of [/Coverage: error/, /failed:network/, /Rerank: skipped:unanswered/])
    expect(screen.getByText(value)).toBeInTheDocument();
});

test("history inspector lists what was found but not selected", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sections: [{
    id: "history_recall", label: "Recalled history", text: "", tokens: 0,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: {
      items: [],
      coverage: { tiers: [1], semantic: "ok", admitted: 3, agreement: 0, verdict: "sufficient" },
      ceiling: 1200, budget_from: ["config"], unselected: 3,
      candidates: [{ scene: "004--saltmarch", key: "sid:004--saltmarch", tier: 2,
                     admitted_by: ["semantic"], rounds: [],
                     ranks: [{ round: 0, signal: "semantic", rank: 7 }] }],
    },
  }] })} />);
  for (const value of [/Found, not selected: 3/, /004--saltmarch; tier 2; admitted by semantic/,
                       /semantic rank 7/])
    expect(screen.getByText(value)).toBeInTheDocument();
});

test("history inspector shows who has each part, with the basis, for its reader", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sections: [{
    id: "history_known", label: "Recalled history · what the speaker knows", text: "", tokens: 0,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: {
      items: [],
      coverage: { tiers: [1], semantic: "off", admitted: 1, agreement: 0, verdict: "thin" },
      ceiling: 1200, budget_from: ["config"],
      epistemic: { perspective: "actor:characters:winifred", decided: 0, items: [{
        key: "a".repeat(32), scene: "", admitted: true, state: "unattributed", header: {},
        known_to: [], suspected_by: [],
        slices: [{ part: "posts", posts: [0], access: "unknown", basis: ["not_recorded_present"] },
                 { part: "posts", posts: [1], access: "witnessed", basis: ["presence_interval"] }],
      }] },
    },
  }] })} />);
  for (const value of [/Access, for characters:winifred/, /Posts 0: unknown \(not_recorded_present\)/,
    /Posts 1: witnessed \(presence_interval\)/]) expect(screen.getByText(value)).toBeInTheDocument();
});

test("the narrator's access rows name who knows and who suspects", () => {
  render(<ContextBreakdown models={[]} ctx={ctx({ sections: [{
    id: "history_recall", label: "Recalled history", text: "x", tokens: 1,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: {
      items: [],
      coverage: { tiers: [1], semantic: "off", admitted: 1, agreement: 0, verdict: "thin" },
      ceiling: 1200, budget_from: ["config"],
      epistemic: { perspective: "narrator", decided: 0, items: [{
        key: "k", scene: "001--pier", admitted: true, state: "attributed",
        header: { title: "The Pier" }, known_to: ["characters:mara"],
        suspected_by: ["characters:winifred"],
        slices: [{ part: "summary", posts: [], access: "known", basis: ["narrator"] }],
      }] },
    },
  }] })} />);
  for (const value of [/Access, for the narrator/, /Known to: characters:mara/,
    /Suspected by: characters:winifred/, /Summary: known \(narrator\)/])
    expect(screen.getByText(value)).toBeInTheDocument();
});
