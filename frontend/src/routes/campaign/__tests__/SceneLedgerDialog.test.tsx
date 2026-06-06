import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SceneLedgerDialog } from "../SceneLedgerDialog";
import { newSceneApi } from "../../../api/campaign/newScene";
import type { LedgerEntry } from "../../../api/campaign/types";

function entry(overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    id: "ledger-1",
    summary: "A quiet morning at the harbor.",
    source: "greeting",
    greeting_id: "gr-harbor",
    status: "active",
    ...overrides,
  } as LedgerEntry;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SceneLedgerDialog", () => {
  it("clicking 'Get greetings' backfills and reloads the ledger", async () => {
    const listSpy = vi
      .spyOn(newSceneApi, "listLedger")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([entry()]);
    const backfillSpy = vi.spyOn(newSceneApi, "backfillLedger").mockResolvedValue({ added: 1 });

    render(<SceneLedgerDialog campaignId="c1" open={true} onClose={() => {}} />);

    await waitFor(() => expect(screen.getByText(/No scene ideas yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Get greetings/i }));

    await waitFor(() =>
      expect(screen.getByText("A quiet morning at the harbor.")).toBeInTheDocument(),
    );
    expect(backfillSpy).toHaveBeenCalledWith("c1");
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Added 1 greeting\./i)).toBeInTheDocument();
  });

  it("reports when no new greetings were added", async () => {
    vi.spyOn(newSceneApi, "listLedger").mockResolvedValue([]);
    vi.spyOn(newSceneApi, "backfillLedger").mockResolvedValue({ added: 0 });

    render(<SceneLedgerDialog campaignId="c1" open={true} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No scene ideas yet/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Get greetings/i }));

    await waitFor(() => expect(screen.getByText(/No new greetings to add\./i)).toBeInTheDocument());
  });
});
