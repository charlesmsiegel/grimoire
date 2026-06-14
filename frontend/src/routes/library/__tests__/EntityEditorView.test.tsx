import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { EntityEditorView } from "../EntityEditorView";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      getEntity: vi.fn(),
      updateEntity: vi.fn(),
      listEntities: vi.fn().mockResolvedValue([]),
      dependents: vi.fn().mockResolvedValue([]),
      listCharacterVariants: vi.fn().mockResolvedValue([]),
    },
  };
});

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={["/library/worlds/w1/characters/alistair"]}>
      <Routes>
        <Route path="/library/worlds/:worldId/:kind/:entityId/*" element={<EntityEditorView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("EntityEditorView (character)", () => {
  it("renders the structured form and a token badge", async () => {
    vi.mocked(libraryModule.libraryApi.getEntity).mockResolvedValue({
      asset_id: "alistair",
      name: "Alistair",
      path: "p.md",
      version: 1,
      frontmatter: { name: "Alistair", role: "major_npc" },
      body: "Body text",
    } as never);

    renderEditor();

    await waitFor(() => expect(screen.getByText("Identity")).toBeInTheDocument());
    expect(screen.getByText(/tokens/)).toBeInTheDocument();
  });

  it("walks load → edit → save → reload, deriving dirty from the loaded entity", async () => {
    const v1 = {
      asset_id: "alistair",
      name: "Alistair",
      path: "p.md",
      version: 1,
      frontmatter: { name: "Alistair", role: "major_npc" },
      body: "Body text",
    };
    // First load returns v1; after save, the reload returns the edited v2 so
    // the derived `dirty` flips back to false (Save disabled again) with no
    // props→state mirror effect.
    const getEntity = vi.mocked(libraryModule.libraryApi.getEntity);
    getEntity.mockResolvedValueOnce(v1 as never);
    // Simulate a server that normalizes on save: the user types a trailing
    // space, but the reload returns the canonical (trimmed) body.
    getEntity.mockResolvedValueOnce({ ...v1, version: 2, body: "Body text edited" } as never);
    const updateEntity = vi.mocked(libraryModule.libraryApi.updateEntity);
    updateEntity.mockResolvedValue(undefined as never);

    renderEditor();

    await waitFor(() => expect(screen.getByText("Identity")).toBeInTheDocument());

    // Freshly loaded: nothing edited, so Save is disabled.
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    // Edit the markdown body → derived dirty → Save enabled.
    const body = screen.getByDisplayValue("Body text");
    fireEvent.change(body, { target: { value: "Body text edited " } });
    await waitFor(() => expect(saveButton).toBeEnabled());

    // Save: no dependents → performs the update immediately, then reloads.
    await act(async () => {
      fireEvent.click(saveButton);
    });
    await waitFor(() =>
      expect(updateEntity).toHaveBeenCalledWith("w1", "characters", "alistair", {
        frontmatter_patch: { name: "Alistair", role: "major_npc" },
        body: "Body text edited ",
      }),
    );

    // After the reload, the render-time reset re-seeds the draft from the
    // canonical (trimmed) revision, so the editor is clean (Save disabled) and
    // shows the server's value — not the stale untrimmed draft.
    await waitFor(() => expect(screen.getByDisplayValue("Body text edited")).toBeInTheDocument());
    expect(saveButton).toBeDisabled();
    expect(screen.getByText(/v2/)).toBeInTheDocument();
  });
});
