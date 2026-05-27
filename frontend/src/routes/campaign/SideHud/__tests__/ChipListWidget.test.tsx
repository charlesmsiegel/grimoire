import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { WidgetSnapshot } from "../../../../api/hud";
import { ChipListWidget } from "../widgets/ChipListWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.present-cast",
    status: "ok",
    data: {
      chips: [
        {
          character_id: "winifred",
          name: "winifred",
          portrait_url: "/p/winifred.png",
          source: "library",
        },
        {
          character_id: "alistair",
          name: "Alistair",
          source: "emergent",
        },
      ],
    },
    error: null,
    stale: false,
    title: "Cast",
    render_hint: "chip-list",
    ...overrides,
  };
}

describe("ChipListWidget — present cast", () => {
  it("renders one chip per present character as a link", () => {
    render(
      <MemoryRouter>
        <ChipListWidget snapshot={snap()} campaignId="test-campaign" />
      </MemoryRouter>,
    );
    const winifred = screen.getByText("winifred");
    expect(winifred.closest("a")).toHaveAttribute(
      "href",
      "/campaigns/test-campaign/cast?character=winifred",
    );
    const alistair = screen.getByText("Alistair");
    expect(alistair.closest("a")).toHaveAttribute(
      "href",
      "/campaigns/test-campaign/cast?character=alistair",
    );
  });

  it("renders the empty state when no chips present", () => {
    render(
      <MemoryRouter>
        <ChipListWidget snapshot={snap({ data: { chips: [] } })} campaignId="test-campaign" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/no present characters/i)).toBeInTheDocument();
  });

  it("renders generic chips for non-present-cast widgets", () => {
    render(
      <ChipListWidget
        snapshot={snap({
          id: "wod-mechanics.disciplines",
          title: "Disciplines",
          data: [{ label: "Auspex" }, { label: "Celerity", tone: "physical" }],
        })}
      />,
    );
    expect(screen.getByText("Auspex")).toBeInTheDocument();
    expect(screen.getByText("Celerity")).toBeInTheDocument();
  });
});
