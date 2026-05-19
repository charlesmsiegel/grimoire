import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ExtrasTable } from "../routes/library/ExtrasTable";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ExtrasTable", () => {
  it("renders extras rows with source badges", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        extras: {
          favorite_drink: {
            value: "Glenfarclas 25",
            set_at: "2026-05-19T00:00:00+00:00",
            set_by: "user",
            source_evidence: null,
            scope: "library",
          },
          scars: {
            value: ["above brow"],
            set_at: "2026-05-19T00:00:00+00:00",
            set_by: "user",
            source_evidence: null,
            scope: "library",
          },
        },
      }),
    );
    render(<ExtrasTable worldId="wod" kind="character" entityId="winifred" />);
    await waitFor(() => {
      expect(screen.getByText("favorite_drink")).toBeInTheDocument();
    });
    expect(screen.getByText("Glenfarclas 25")).toBeInTheDocument();
    expect(screen.getByText("above brow")).toBeInTheDocument();
  });

  it("shows empty state when there are no extras", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ extras: {} }));
    render(<ExtrasTable worldId="wod" kind="character" entityId="winifred" />);
    await waitFor(() => {
      expect(screen.getByText(/No extras yet/i)).toBeInTheDocument();
    });
  });

  it("opens an add-row when '+ Add field' is clicked and POSTs on save", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ extras: {} }));
    render(<ExtrasTable worldId="wod" kind="character" entityId="winifred" />);
    await waitFor(() => {
      expect(screen.getByText(/No extras yet/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    fireEvent.change(screen.getByPlaceholderText(/key \(snake_case\)/i), {
      target: { value: "smokes" },
    });
    fireEvent.change(screen.getByPlaceholderText(/value \(newline-separated/i), {
      target: { value: "Sobranies" },
    });

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        extra: {
          value: "Sobranies",
          set_at: "2026-05-19T00:00:00+00:00",
          set_by: "user",
          source_evidence: null,
          scope: "library",
        },
        warnings: [],
      }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        extras: {
          smokes: {
            value: "Sobranies",
            set_at: "2026-05-19T00:00:00+00:00",
            set_by: "user",
            source_evidence: null,
            scope: "library",
          },
        },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.getByText("smokes")).toBeInTheDocument();
    });

    const calls = fetchMock.mock.calls.map((c) => c[0] as string);
    expect(calls.some((url) => url.endsWith("/extras/smokes"))).toBe(true);
  });

  it("surfaces a 422 error message", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "reserved prefix on extras key" }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<ExtrasTable worldId="wod" kind="character" entityId="winifred" />);
    await waitFor(() => {
      expect(screen.getByText(/Error:/)).toBeInTheDocument();
    });
  });
});
