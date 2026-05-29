import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { PreviewSummary } from "../../../../api/inspector";
import { TokenBars } from "../TokenBars";

const summary: PreviewSummary = {
  handle: "ph_abc",
  per_tier_tokens: { "lock-in": 1500, spotlight: 12000, background: 5000, archive: 0 },
  per_tier_budget: { "lock-in": 8000, spotlight: 40000, background: 30000, archive: 20000 },
  source_count: 7,
  messages_hash: "h",
};

describe("TokenBars (total)", () => {
  it("renders empty state when no summary is given", () => {
    render(<TokenBars summary={null} />);
    expect(screen.getByText(/Type to preview/i)).toBeInTheDocument();
  });

  it("renders the summed total used / budget", () => {
    render(<TokenBars summary={summary} />);
    // 1500+12000+5000+0 = 18500 ; 8000+40000+30000+20000 = 98000
    expect(screen.getByText("18,500 / 98,000")).toBeInTheDocument();
  });

  it("exposes the per-tier split for hover/expand", () => {
    render(<TokenBars summary={summary} />);
    const bar = screen.getByLabelText(/per-tier token usage/i);
    expect(bar.getAttribute("title")).toMatch(/lock-in 1,500/);
    expect(bar.getAttribute("title")).toMatch(/spotlight 12,000/);
  });
});
