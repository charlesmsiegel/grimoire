import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
      listEntities: vi.fn().mockResolvedValue([]),
      dependents: vi.fn().mockResolvedValue([]),
      variants: vi.fn().mockResolvedValue([]),
    },
  };
});

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

    render(
      <MemoryRouter initialEntries={["/library/worlds/w1/characters/alistair"]}>
        <Routes>
          <Route
            path="/library/worlds/:worldId/:kind/:entityId/*"
            element={<EntityEditorView />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Identity")).toBeInTheDocument());
    expect(screen.getByText(/tokens/)).toBeInTheDocument();
  });
});
