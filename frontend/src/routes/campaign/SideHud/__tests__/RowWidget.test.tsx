import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { WidgetSnapshot } from "../../../../api/hud";
import { RowWidget } from "../widgets/RowWidget";

function snap(overrides: Partial<WidgetSnapshot> = {}): WidgetSnapshot {
  return {
    id: "core.in-game-date",
    status: "ok",
    data: { date: "1894-10-13" },
    error: null,
    stale: false,
    title: "Date",
    render_hint: "row",
    ...overrides,
  };
}

describe("RowWidget", () => {
  it("renders title and primary scalar from data", () => {
    render(<RowWidget snapshot={snap()} />);
    expect(screen.getByText("Date")).toBeInTheDocument();
    expect(screen.getByText("1894-10-13")).toBeInTheDocument();
  });

  it("shows an em-dash placeholder when data has no recognized scalar", () => {
    render(<RowWidget snapshot={snap({ data: { meta: "x" } })} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders the error message when status is error", () => {
    render(
      <RowWidget
        snapshot={snap({ status: "error", error: "no fetcher registered", data: null })}
      />,
    );
    expect(screen.getByText(/no fetcher registered/i)).toBeInTheDocument();
    expect(screen.getByText(/^Error$/)).toBeInTheDocument();
  });

  it("returns null for hidden status", () => {
    const { container } = render(<RowWidget snapshot={snap({ status: "hidden" })} />);
    expect(container.firstChild).toBeNull();
  });

  it("offers a refresh affordance on error and invokes onRefresh", () => {
    const onRefresh = vi.fn();
    render(
      <RowWidget
        snapshot={snap({ status: "timeout", error: "owner endpoint timeout" })}
        onRefresh={onRefresh}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /refresh date/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("shows the stale badge when snapshot.stale is true", () => {
    render(<RowWidget snapshot={snap({ stale: true })} />);
    expect(screen.getByText(/^Stale$/)).toBeInTheDocument();
  });
});
