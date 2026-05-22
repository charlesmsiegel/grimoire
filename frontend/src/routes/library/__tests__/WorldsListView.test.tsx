import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { WorldsListView } from "../WorldsListView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listWorlds: vi.fn(),
      deleteWorld: vi.fn(),
    },
    fetchWorldDependents: vi.fn(),
  };
});

describe("WorldsListView delete", () => {
  it("opens the confirm dialog with dependents and deletes on confirm", async () => {
    vi.mocked(libraryModule.libraryApi.listWorlds).mockResolvedValue([
      {
        id: "sakura-high",
        name: "Sakura High",
        description: "",
        tags: [],
        genre: "",
        calendar: {},
        atmosphere: {},
        defaults: {},
        version: 1,
      },
    ]);
    vi.mocked(libraryModule.fetchWorldDependents).mockResolvedValue([
      { id: "camp1", name: "Camp One" },
    ]);
    vi.mocked(libraryModule.libraryApi.deleteWorld).mockResolvedValue(undefined);

    render(
      <MemoryRouter>
        <WorldsListView />
      </MemoryRouter>,
    );

    await waitFor(() => screen.getByText("Sakura High"));
    fireEvent.click(screen.getByRole("button", { name: /Delete world/i }));

    await waitFor(() => screen.getByText(/Camp One/));
    const typed = screen.getByLabelText(/type id/i);
    fireEvent.change(typed, { target: { value: "sakura-high" } });
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));

    await waitFor(() =>
      expect(libraryModule.libraryApi.deleteWorld).toHaveBeenCalledWith("sakura-high"),
    );
  });
});
