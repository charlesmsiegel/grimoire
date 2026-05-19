import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { PreviewSummary } from "../../../../api/inspector";
import { TokenBars } from "../TokenBars";

const summary: PreviewSummary = {
  handle: "ph_abc",
  per_tier_tokens: {
    "lock-in": 1500,
    spotlight: 12000,
    background: 5000,
    archive: 0,
  },
  per_tier_budget: {
    "lock-in": 8000,
    spotlight: 40000,
    background: 30000,
    archive: 20000,
  },
  source_count: 7,
  messages_hash: "h",
};

describe("TokenBars", () => {
  it("renders empty state when no summary is given", () => {
    render(<TokenBars summary={null} />);
    expect(screen.getByText(/Type to preview/i)).toBeInTheDocument();
  });

  it("renders loading state when no summary but loading", () => {
    render(<TokenBars summary={null} loading />);
    expect(screen.getByText(/Computing preview/i)).toBeInTheDocument();
  });

  it("renders one row per tier with used/budget counts", () => {
    render(<TokenBars summary={summary} />);
    expect(screen.getByText(/Lock-in/i)).toBeInTheDocument();
    expect(screen.getByText("1,500 / 8,000")).toBeInTheDocument();
    expect(screen.getByText(/Spotlight/i)).toBeInTheDocument();
    expect(screen.getByText("12,000 / 40,000")).toBeInTheDocument();
  });
});
