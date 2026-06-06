import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WhatChangedPanel } from "../WhatChangedPanel";
import { observabilityApi, type TurnDeltaDiff } from "../../../api/observability";
import { ApiError } from "../../../api/client";

function diff(overrides: Partial<TurnDeltaDiff> = {}): TurnDeltaDiff {
  return {
    applied: [
      {
        id: "d1",
        kind: "fact_add",
        target_scope: "campaign_sqlite",
        target_table: null,
        target_path: null,
        target_id: "f_curfew",
        before: null,
        after: { name: "curfew" },
        confidence: 0.95,
        source: "extractor:wod-mechanics",
        strategy: "extractor:wod-mechanics",
        evidence: "The mayor announces a curfew.",
        extra: {},
        notes: "",
        applied_at: "2026-05-20T12:00:00Z",
        reversed_at: null,
        status: "auto",
      },
      {
        id: "d2",
        kind: "fact_add",
        target_scope: "campaign_sqlite",
        target_table: null,
        target_path: null,
        target_id: "f_low_conf",
        before: null,
        after: { name: "rumor" },
        confidence: 0.2,
        source: "extractor:wod-mechanics",
        strategy: "extractor:wod-mechanics",
        evidence: "Someone whispers about wolves.",
        extra: {},
        notes: "",
        applied_at: "2026-05-20T12:00:01Z",
        reversed_at: null,
        status: "auto",
      },
    ],
    queued: [
      {
        id: "d3",
        kind: "commitment_add",
        target_scope: "campaign_sqlite",
        target_table: null,
        target_path: null,
        target_id: "c_meet",
        before: null,
        after: { text: "meet later" },
        confidence: 0.4,
        source: "mechanics",
        strategy: "mechanics",
        evidence: "You promise to meet again.",
        extra: {},
        notes: "",
        applied_at: null,
        reversed_at: null,
        status: "queued",
        review_id: "r1",
        review_status: "pending",
      },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WhatChangedPanel", () => {
  it("shows an idle prompt when no turn id is selected", () => {
    render(<WhatChangedPanel turnId={null} />);
    expect(screen.getByText(/waiting for the first turn/i)).toBeInTheDocument();
  });

  it("renders applied + queued deltas grouped by kind", async () => {
    vi.spyOn(observabilityApi, "turnDeltas").mockResolvedValue(diff());
    render(<WhatChangedPanel turnId="t_abc" />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Auto-applied/i })).toBeInTheDocument();
    });
    // Both applied entries land under fact_add; the queued one lands
    // under commitment_add in its own section.
    expect(screen.getByText("The mayor announces a curfew.", { exact: false })).toBeInTheDocument();
    expect(
      screen.getByText("Someone whispers about wolves.", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Queued for review/i })).toBeInTheDocument();
    expect(screen.getByText("You promise to meet again.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("the confidence filter hides entries below the threshold", async () => {
    vi.spyOn(observabilityApi, "turnDeltas").mockResolvedValue(diff());
    render(<WhatChangedPanel turnId="t_abc" />);
    await waitFor(() =>
      expect(
        screen.getByText("The mayor announces a curfew.", { exact: false }),
      ).toBeInTheDocument(),
    );
    const slider = screen.getByLabelText(/Minimum confidence/i);
    fireEvent.change(slider, { target: { value: "0.5" } });
    // 0.95 stays, 0.2 (low-confidence applied) and 0.4 (queued) drop.
    expect(screen.getByText("The mayor announces a curfew.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("Someone whispers about wolves.", { exact: false })).toBeNull();
    expect(screen.queryByText("You promise to meet again.", { exact: false })).toBeNull();
  });

  it("the source filter narrows by strategy substring", async () => {
    vi.spyOn(observabilityApi, "turnDeltas").mockResolvedValue(diff());
    render(<WhatChangedPanel turnId="t_abc" />);
    await waitFor(() =>
      expect(
        screen.getByText("The mayor announces a curfew.", { exact: false }),
      ).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText(/Filter by source/i), {
      target: { value: "mechanics" },
    });
    // "mechanics" matches both the extractor:wod-mechanics applied rows
    // AND the queued "mechanics" row.
    expect(screen.getByText("The mayor announces a curfew.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("You promise to meet again.", { exact: false })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Filter by source/i), {
      target: { value: "nothing-matches" },
    });
    expect(screen.getByText(/No deltas matched/i)).toBeInTheDocument();
  });

  it("toggling the queued checkbox hides the queued section", async () => {
    vi.spyOn(observabilityApi, "turnDeltas").mockResolvedValue(diff());
    render(<WhatChangedPanel turnId="t_abc" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Queued for review/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.queryByRole("heading", { name: /Queued for review/i })).toBeNull();
  });

  it("renders a 'no audit yet' message when the endpoint 404s", async () => {
    vi.spyOn(observabilityApi, "turnDeltas").mockRejectedValue(new ApiError(404, null));
    render(<WhatChangedPanel turnId="t_missing" />);
    await waitFor(() => expect(screen.getByText(/No audit record yet/i)).toBeInTheDocument());
  });
});
