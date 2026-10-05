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
