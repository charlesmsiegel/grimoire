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
