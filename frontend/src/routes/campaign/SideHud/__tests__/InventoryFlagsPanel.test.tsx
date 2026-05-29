import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { InventoryFlagsList } from "../widgets/InventoryFlagsPanel";

describe("InventoryFlagsList", () => {
  it("renders flag reasons and a resolve button", () => {
    const flags = [
      {
        id: "f1",
        turn_id: "t1",
        op_json: '{"action":"drop","item":"dagger"}',
        flag_reason: "reconciled_missing_item",
        resolved: 0,
        created_at: "2026-05-28T00:00:00Z",
      },
    ];
    render(<InventoryFlagsList flags={flags} onResolve={vi.fn()} />);
    expect(screen.getByText(/reconciled_missing_item/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resolve/i })).toBeInTheDocument();
  });

  it("calls onResolve with the flag id", () => {
    const onResolve = vi.fn();
    const flags = [
      {
        id: "f1",
        turn_id: null,
        op_json: "{}",
        flag_reason: "low_confidence",
        resolved: 0,
        created_at: "2026-05-28T00:00:00Z",
      },
    ];
    render(<InventoryFlagsList flags={flags} onResolve={onResolve} />);
    screen.getByRole("button", { name: /resolve/i }).click();
    expect(onResolve).toHaveBeenCalledWith("f1");
  });

  it("renders nothing when there are no flags", () => {
    const { container } = render(<InventoryFlagsList flags={[]} onResolve={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});
