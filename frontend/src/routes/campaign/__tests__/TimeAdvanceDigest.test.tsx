import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TimeAdvanceDigest } from "../TimeAdvanceDigest";
import type { TimeAdvanceResult } from "../../../api/campaign";

function makeResult(overrides: Partial<TimeAdvanceResult> = {}): TimeAdvanceResult {
  return {
    from_time: { moment: "2026-05-19T12:00:00" },
    to_time: { moment: "2026-05-20T12:00:00" },
    duration: { iso8601: "P1D" },
    digest: "A day passed. winifred rested by the fire.",
    npc_summaries: {},
    scheduled_events_triggered: [],
    weather_changes: [],
    drift_warnings: [],
    scheduled_events_upcoming: [],
    ...overrides,
  };
}

describe("TimeAdvanceDigest", () => {
  it("renders nothing when result is null", () => {
    const { container } = render(<TimeAdvanceDigest result={null} onDismiss={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it("displays the digest text in a modal dialog", () => {
    render(<TimeAdvanceDigest result={makeResult()} onDismiss={() => {}} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/A day passed\. winifred rested by the fire\./)).toBeInTheDocument();
  });

  it("falls back to a placeholder when digest text is empty", () => {
    render(<TimeAdvanceDigest result={makeResult({ digest: "" })} onDismiss={() => {}} />);
    expect(screen.getByText(/no notable events/i)).toBeInTheDocument();
  });

  it("shows the from/to times and duration", () => {
    render(<TimeAdvanceDigest result={makeResult()} onDismiss={() => {}} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("2026-05-19T12:00:00");
    expect(dialog).toHaveTextContent("2026-05-20T12:00:00");
    expect(dialog).toHaveTextContent("P1D");
  });

  it("lists NPC activities from npc_summaries", () => {
    const result = makeResult({
      npc_summaries: {
        char_florence: {
          character_id: "char_florence",
          duration: { iso8601: "P1D" },
          state_at_end: {},
          activities: ["practiced the lyre", "walked the cliffs"],
        },
      },
    });
    render(<TimeAdvanceDigest result={result} onDismiss={() => {}} />);
    expect(screen.getByText("char_florence")).toBeInTheDocument();
    expect(screen.getByText(/practiced the lyre/)).toBeInTheDocument();
    expect(screen.getByText(/walked the cliffs/)).toBeInTheDocument();
  });

  it("lists scheduled events triggered during the advance", () => {
    const result = makeResult({
      scheduled_events_triggered: [
        {
          id: "ev_market",
          at: { moment: "2026-05-20T08:00:00" },
          label: "Market day opens",
          kind: "recurring",
          triggered: true,
        },
      ],
    });
    render(<TimeAdvanceDigest result={result} onDismiss={() => {}} />);
    expect(screen.getByText(/Market day opens/)).toBeInTheDocument();
  });

  it("lists weather changes", () => {
    const result = makeResult({
      weather_changes: [
        {
          location_ref: "loc_harbor",
          at: { moment: "2026-05-19T18:00:00" },
          summary: "Storm rolls in from the sea",
        },
      ],
    });
    render(<TimeAdvanceDigest result={result} onDismiss={() => {}} />);
    expect(screen.getByText(/Storm rolls in from the sea/)).toBeInTheDocument();
  });

  it("calls onDismiss when Continue is clicked", () => {
    const onDismiss = vi.fn();
    render(<TimeAdvanceDigest result={makeResult()} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("hides empty optional sections", () => {
    render(<TimeAdvanceDigest result={makeResult()} onDismiss={() => {}} />);
    expect(screen.queryByRole("heading", { name: /Characters/i })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Scheduled events/i })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Weather/i })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Drift warnings/i })).toBeNull();
  });
});
