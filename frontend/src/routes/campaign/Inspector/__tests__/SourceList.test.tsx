import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { ContextSourceExplanation } from "../../../../api/inspector";
import { SourceList } from "../SourceList";

const sources: ContextSourceExplanation[] = [
  {
    source_id: "src_pc",
    owner_id: "campaign:camp",
    kind: "character",
    scope: "campaign-local",
    tier: "lock-in",
    library_version: null,
    inclusion_reasons: ["pc_card"],
    tokens: 800,
    summary: "Active PC: Alistair",
    text: "PC card body",
  },
  {
    source_id: "src_florence",
    owner_id: "library:worlds/wod/characters/winifred",
    kind: "character",
    scope: "library",
    tier: "spotlight",
    library_version: 3,
    inclusion_reasons: ["present_in_scene", "commitment_open_to_pc"],
    tokens: 1200,
    summary: "library:worlds/wod/characters/winifred",
    text: "winifred body text",
  },
];

describe("SourceList", () => {
  it("renders one row per source ordered by tier", () => {
    render(<SourceList campaignId="camp" sources={sources} />);
    const rows = screen.getAllByRole("button", { name: /(lock-in|spotlight)/i });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(/lock-in/i);
    expect(rows[1]).toHaveTextContent(/spotlight/i);
  });

  it("expands a row to show inclusion reasons", () => {
    render(<SourceList campaignId="camp" sources={sources} />);
    const florenceRow = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("winifred"));
    expect(florenceRow).toBeDefined();
    fireEvent.click(florenceRow!);
    expect(screen.getByText(/Present in scene/i)).toBeInTheDocument();
    expect(screen.getByText(/Open commitment to PC/i)).toBeInTheDocument();
  });

  it("renders an empty state when no sources", () => {
    render(<SourceList campaignId="camp" sources={[]} />);
    expect(screen.getByText(/No sources in this preview/i)).toBeInTheDocument();
  });
});
