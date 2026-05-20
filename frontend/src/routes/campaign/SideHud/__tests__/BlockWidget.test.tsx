import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { WidgetSnapshot } from "../../../../api/hud";
import { BlockWidget } from "../widgets/BlockWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.recent-events",
    status: "ok",
    data: [{ id: "f1", text: "Door slammed shut." }, { id: "f2", text: "Light flickered." }],
    error: null,
    stale: false,
    title: "Recent events",
    render_hint: "block",
    ...overrides,
  };
}

describe("BlockWidget", () => {
  it("renders a heading and an item per entry", () => {
    render(<BlockWidget snapshot={snap()} />);
    expect(screen.getByRole("heading", { name: "Recent events" })).toBeInTheDocument();
    expect(screen.getByText("Door slammed shut.")).toBeInTheDocument();
    expect(screen.getByText("Light flickered.")).toBeInTheDocument();
  });

  it("renders a scalar payload as text when no list shape is available", () => {
    render(<BlockWidget snapshot={snap({ data: { summary: "Quiet night so far." } })} />);
    expect(screen.getByText("Quiet night so far.")).toBeInTheDocument();
  });

  it("renders an empty state for an empty list", () => {
    render(<BlockWidget snapshot={snap({ data: [] })} />);
    expect(screen.getByText(/nothing yet/i)).toBeInTheDocument();
  });

  it("renders an error body when status is error", () => {
    render(
      <BlockWidget
        snapshot={snap({ status: "error", error: "owner returned 500", data: null })}
      />,
    );
    expect(screen.getByText(/owner returned 500/i)).toBeInTheDocument();
  });
});
