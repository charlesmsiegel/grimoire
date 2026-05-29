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
      createWorld: vi.fn(),
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
        pc_role_tags: [],
        genre: "",
        calendar: {},
        calendar_ids: [],
        holiday_set_ids: [],
        display_calendar_id: null,
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

describe("WorldsListView rich create", () => {
  it("creates a world with genre/description via the rich form", async () => {
    vi.mocked(libraryModule.libraryApi.listWorlds).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.createWorld).mockResolvedValue({ id: "ravenmark" } as never);

    render(
      <MemoryRouter>
        <WorldsListView />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /New world/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ravenmark" } });
    fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Grimdark fantasy" } });
    fireEvent.click(screen.getByRole("button", { name: /^Create/ }));

    await waitFor(() =>
      expect(libraryModule.libraryApi.createWorld).toHaveBeenCalledWith(
        "ravenmark",
        expect.objectContaining({ name: "Ravenmark", genre: "Grimdark fantasy" }),
      ),
    );
  });
});
