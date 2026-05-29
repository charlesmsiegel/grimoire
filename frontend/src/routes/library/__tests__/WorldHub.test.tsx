import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { WorldHub } from "../WorldHub";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return { ...actual, libraryApi: { ...actual.libraryApi, worldSummary: vi.fn() } };
});

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/library/worlds/w1"]}>
      <Routes>
        <Route path="/library/worlds/:worldId" element={<WorldHub />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorldHub", () => {
  it("shows per-kind counts and an add affordance for empty kinds", async () => {
    vi.mocked(libraryModule.libraryApi.worldSummary).mockResolvedValue({
      counts: {
        characters: 3,
        locations: 0,
        items: 1,
        lore: 2,
        factions: 0,
        monsters: 0,
        greetings: 1,
      },
      has_description: true,
      has_genre: true,
    });
    renderHub();
    await waitFor(() => expect(screen.getByText("Characters")).toBeInTheDocument());
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getAllByText(/Add a location/i).length).toBeGreaterThan(0);
  });

  it("reflects setup progress from flags + counts", async () => {
    vi.mocked(libraryModule.libraryApi.worldSummary).mockResolvedValue({
      counts: {
        characters: 0,
        locations: 0,
        items: 0,
        lore: 0,
        factions: 0,
        monsters: 0,
        greetings: 0,
      },
      has_description: false,
      has_genre: false,
    });
    renderHub();
    await waitFor(() => expect(screen.getByText(/World setup/i)).toBeInTheDocument());
    expect(screen.getByText(/0%/)).toBeInTheDocument();
  });
});
