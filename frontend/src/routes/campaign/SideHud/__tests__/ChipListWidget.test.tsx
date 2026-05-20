import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { WidgetSnapshot } from "../../../../api/hud";
import { ChipListWidget } from "../widgets/ChipListWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.present-cast",
    status: "ok",
    data: [
      {
        character_id: "winifred",
        name: "winifred",
        portrait_url: "/p/winifred.png",
        mood: { emoji: "😟", label: "anxious" },
        current_action: "scanning the dim hallway",
        internal_thought: "Something feels off here.",
        source: "library",
        drift: { score: 0.6, threshold: 0.5 },
      },
      {
        character_id: "alistair",
        name: "Alistair",
        mood: { emoji: "🙂", label: "calm" },
        source: "emergent",
      },
    ],
    error: null,
    stale: false,
    title: "Present cast",
    render_hint: "chip-list",
    ...overrides,
  };
}

describe("ChipListWidget — present cast", () => {
  it("renders one chip per present character with mood + action", () => {
    render(<ChipListWidget snapshot={snap()} />);
    const winifred = screen.getByLabelText(/present cast: winifred/i);
    expect(within(winifred).getByText(/anxious/i)).toBeInTheDocument();
    expect(within(winifred).getByText(/scanning the dim hallway/i)).toBeInTheDocument();
    expect(within(winifred).getByText(/something feels off/i)).toBeInTheDocument();
    const alistair = screen.getByLabelText(/present cast: alistair/i);
    expect(within(alistair).getByText(/calm/i)).toBeInTheDocument();
  });

  it("renders the empty state when no chips present", () => {
    render(<ChipListWidget snapshot={snap({ data: [] })} />);
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
