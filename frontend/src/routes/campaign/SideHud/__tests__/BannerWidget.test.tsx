import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { WidgetSnapshot } from "../../../../api/hud";
import { BannerWidget } from "../widgets/BannerWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.drift-alerts",
    status: "ok",
    data: [
      {
        id: "a1",
        character_ref: "library:worlds/wod/characters/winifred",
        text: "winifred drifting",
      },
    ],
    error: null,
    stale: false,
    title: "Drift alerts",
    render_hint: "banner",
    ...overrides,
  };
}

describe("BannerWidget", () => {
  it("renders alert items as a banner", () => {
    render(<BannerWidget snapshot={snap()} />);
    expect(screen.getByRole("alert", { name: /drift alerts/i })).toBeInTheDocument();
    expect(screen.getByText("winifred drifting")).toBeInTheDocument();
  });

  it("collapses (renders nothing) when there are no alerts", () => {
    const { container } = render(<BannerWidget snapshot={snap({ data: [] })} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders an error banner when status is error", () => {
    render(
      <BannerWidget
        snapshot={snap({ status: "error", error: "drift service down", data: null })}
      />,
    );
    expect(screen.getByText(/drift service down/i)).toBeInTheDocument();
  });
});
