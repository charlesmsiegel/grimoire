import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { WidgetSnapshot } from "../../../../api/hud";
import { CompositeWidget } from "../widgets/CompositeWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.review-queue",
    status: "ok",
    data: {
      count: 3,
      label: "3 items awaiting review",
      items: [{ text: "winifred: missing fact" }],
    },
    error: null,
    stale: false,
    title: "Review queue",
    render_hint: "composite",
    ...overrides,
  };
}

describe("CompositeWidget", () => {
  it("renders count, label, and items", () => {
    render(<CompositeWidget snapshot={snap()} />);
    expect(screen.getByRole("heading", { name: "Review queue" })).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/3 items awaiting review/)).toBeInTheDocument();
    expect(screen.getByText(/winifred: missing fact/)).toBeInTheDocument();
  });

  it("falls back to a bare empty placeholder when payload is empty", () => {
    render(<CompositeWidget snapshot={snap({ data: {} })} />);
    expect(screen.getByText(/nothing to show/i)).toBeInTheDocument();
  });

  it("shows error message on error status", () => {
    render(
      <CompositeWidget snapshot={snap({ status: "error", error: "extractor down", data: null })} />,
    );
    expect(screen.getByText(/extractor down/i)).toBeInTheDocument();
  });
});
