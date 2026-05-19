import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { ContextDiff } from "../../../../api/inspector";
import { DiffView } from "../DiffView";

const diff: ContextDiff = {
  entities_added: [
    {
      source_id: "src_henry",
      owner_id: "library:.../henry",
      kind: "character",
      scope: "library",
      tier: "spotlight",
      library_version: null,
      inclusion_reasons: ["present_in_scene"],
      tokens: 800,
      summary: "Henry",
    },
  ],
  entities_removed: [],
  entities_changed_tier: [],
  budget_shifts: {
    "lock-in": 0,
    spotlight: 800,
    background: -200,
    archive: 0,
  },
  source_version_changes: [],
};

describe("DiffView", () => {
  it("renders empty state for null diff", () => {
    render(<DiffView diff={null} />);
    expect(screen.getByText(/No diff loaded/i)).toBeInTheDocument();
  });

  it("renders added/removed/tier sections + budget shifts", () => {
    render(<DiffView diff={diff} />);
    expect(screen.getByText(/Henry/)).toBeInTheDocument();
    expect(screen.getByText(/Added/)).toBeInTheDocument();
    expect(screen.getByText(/Removed/)).toBeInTheDocument();
    expect(screen.getByText(/Budget shifts/)).toBeInTheDocument();
    expect(screen.getByText(/\+800 tokens/)).toBeInTheDocument();
    expect(screen.getByText(/-200 tokens/)).toBeInTheDocument();
  });
});
