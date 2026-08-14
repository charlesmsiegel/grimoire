import { render, screen } from "@testing-library/react";
import { ContextBudgetBar } from "./ContextBudgetBar";
import { type SceneContext } from "../api/client";

const section = (tier: string, tokens: number, dropped = false) => ({
  label: tier, text: "", tokens, tier, dropped, trimmed: 0,
}) as unknown as SceneContext["sections"][number];

const ctx = (over: Partial<SceneContext> = {}): SceneContext => ({
  model: "m", total_tokens: 1_000, dropped_tokens: 0, budget_tokens: 4_000,
  sections: [section("lock-in", 400), section("history", 600)],
  ...over,
});

test("measures the prompt against the budget it was packed to", () => {
  render(<ContextBudgetBar ctx={ctx()} label="LAST TURN" />);
  expect(screen.getByText("1,000 / 4,000 · 25%")).toBeInTheDocument();
  expect(screen.getByText("NOTHING DROPPED")).toBeInTheDocument();
});

test("says so rather than inventing a denominator when nothing bounds the prompt", () => {
  // budget_tokens 0 is the default install: no ceiling, so nothing is ever
  // dropped and there is no percentage to report.
  render(<ContextBudgetBar ctx={ctx({ budget_tokens: 0 })} label="LAST TURN" />);
  expect(screen.getByText("1,000 TOKENS · NO CEILING")).toBeInTheDocument();
});

test("leaves dropped sections out of the stack and reports them as the verdict", () => {
  render(<ContextBudgetBar label="LAST TURN" ctx={ctx({
    dropped_tokens: 300,
    sections: [section("lock-in", 400), section("archive", 300, true)],
  })} />);
  // The archive section was rendered but not sent, so the bar does not draw it.
  expect(screen.queryByText(/^RECALLED/)).toBeNull();
  expect(screen.getByText("300 TOKENS DROPPED")).toBeInTheDocument();
});

test("a tier the client's union has not caught up with still lands in the bar", () => {
  // `recalled` is a real backend tier that `ContextSection["tier"]` is missing.
  // A prompt carrying one must not go partly undrawn by a bar whose claim is
  // that it accounts for the whole thing.
  render(<ContextBudgetBar label="LAST TURN" ctx={ctx({
    sections: [section("recalled", 250), section("someday-a-sixth-tier", 100)],
  })} />);
  expect(screen.getByText(/RECALLED 250/)).toBeInTheDocument();
  expect(screen.getByText(/STANDING FRAME 100/)).toBeInTheDocument();
});
