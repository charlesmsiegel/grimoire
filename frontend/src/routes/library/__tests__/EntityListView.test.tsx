import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { EntityListView } from "../EntityListView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listEntities: vi.fn(),
      deleteEntity: vi.fn(),
      dependents: vi.fn(),
      createEntity: vi.fn(),
    },
  };
});

describe("EntityListView delete", () => {
  it("opens dialog with dependents and deletes on confirm", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
      {
        id: "worlds/w/characters/ochaco",
        world_id: "w",
        kind: "character",
        asset_id: "ochaco",
        name: "Ochaco",
        path: "x.md",
        frontmatter: { name: "Ochaco" },
        body: "",
        tags: [],
      } as never,
    ]);
    vi.mocked(libraryModule.libraryApi.dependents).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.deleteEntity).mockResolvedValue(undefined);

    render(
      <MemoryRouter initialEntries={["/library/worlds/w/characters"]}>
        <Routes>
          <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => screen.getByText("Ochaco"));
    fireEvent.click(screen.getByRole("button", { name: /^Delete$/ }));
    await waitFor(() => screen.getByText(/permanently removes/i));
    // No dependents loaded ⇒ confirm enabled. The dialog's Delete button is
    // the second one in the DOM (the card-level Delete is the first).
    const buttons = screen.getAllByRole("button", { name: /^Delete$/ });
    const dialogConfirm = buttons[buttons.length - 1]!;
    await waitFor(() => expect(dialogConfirm).toBeEnabled());
    fireEvent.click(dialogConfirm);
    await waitFor(() =>
      expect(libraryModule.libraryApi.deleteEntity).toHaveBeenCalledWith(
        "w",
        "characters",
        "ochaco",
      ),
    );
  });
});

describe("EntityListView token badges", () => {
  it("shows a token badge on each entity card", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
      {
        id: "worlds/w1/characters/alistair",
        asset_id: "alistair",
        world_id: "w1",
        kind: "character",
        name: "Alistair",
        path: "x.md",
        frontmatter: { name: "Alistair" },
        body: "a".repeat(40),
        tags: [],
      } as never,
    ]);
    vi.mocked(libraryModule.libraryApi.dependents).mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={["/library/worlds/w1/characters"]}>
        <Routes>
          <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Alistair")).toBeInTheDocument());
    expect(screen.getByText(/tokens/)).toBeInTheDocument();
  });
});

describe("EntityListView rich create", () => {
  it("creates a character with auto-id and role via the rich create form", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.dependents).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.createEntity).mockResolvedValue({
      asset_id: "alistair",
    } as never);

    render(
      <MemoryRouter initialEntries={["/library/worlds/w1/characters"]}>
        <Routes>
          <Route path="/library/worlds/:worldId/:kind" element={<EntityListView />} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /New character/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Alistair" } });
    fireEvent.click(screen.getByRole("button", { name: /^Create/ }));
    await waitFor(() =>
      expect(libraryModule.libraryApi.createEntity).toHaveBeenCalledWith(
        "w1",
        "characters",
        expect.objectContaining({
          id: "alistair",
          frontmatter: expect.objectContaining({ name: "Alistair" }),
        }),
      ),
    );
  });
});
