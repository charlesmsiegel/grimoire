import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { WorldView } from "../WorldView";
import { viewsApi } from "../../../api/views";
import type { ResolvedEntity } from "../../../api/types";

vi.mock("../../../api/views", () => ({
  viewsApi: {
    listMonsters: vi.fn(),
    listCharacters: vi.fn(),
    listCast: vi.fn(),
    listWorlds: vi.fn(),
    patchEntityOverride: vi.fn(),
    patchCharacterOverride: vi.fn(),
    promoteEntityToLibrary: vi.fn(),
    promoteCharacterToLibrary: vi.fn(),
  },
}));

function row(overrides: Partial<ResolvedEntity>): ResolvedEntity {
  return {
    kind: "monster",
    asset_id: "grendel",
    world_id: "wod-london",
    name: "Grendel",
    frontmatter: { id: "grendel", name: "Grendel", tags: [] },
    body: "",
    source_chain: [
      {
        layer: "library_live",
        scope: "library",
        library_id: null,
        world_id: "wod-london",
        version: 1,
        override_applied: false,
      },
    ],
    overrides_applied: [],
    extras: {},
    ...overrides,
  };
}

const libraryRow = row({});
const overriddenRow = row({
  asset_id: "wyrm",
  name: "The Wyrm",
  frontmatter: { id: "wyrm", name: "The Wyrm" },
  source_chain: [
    {
      layer: "override",
      scope: "campaign-file",
      library_id: null,
      world_id: "wod-london",
      version: 2,
      override_applied: true,
    },
  ],
  overrides_applied: ["override"],
});
const emergentRow = row({
  asset_id: "fen-beast",
  name: "Fen Beast",
  world_id: null,
  frontmatter: { id: "fen-beast", name: "Fen Beast" },
  source_chain: [
    {
      layer: "emergent",
      scope: "campaign-local",
      library_id: null,
      world_id: null,
      version: null,
      override_applied: false,
    },
  ],
});

function renderMonstersTab() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/c1/world?tab=monsters"]}>
      <Routes>
        <Route path="/campaigns/:campaignId/world" element={<WorldView />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(viewsApi.listMonsters).mockResolvedValue([libraryRow, overriddenRow, emergentRow]);
  vi.mocked(viewsApi.listWorlds).mockResolvedValue([
    { id: "wod-london", name: "London by Night" } as never,
  ]);
});

describe("WorldView campaign scope", () => {
  it("renders truthful chain badges and scope-driven actions per row", async () => {
    const { container } = renderMonstersTab();
    await waitFor(() => screen.getByText("Grendel"));

    expect(container.querySelector(".source-badge-library")).not.toBeNull();
    expect(container.querySelector(".source-badge-override")).not.toBeNull();
    expect(container.querySelector(".source-badge-emergent")).not.toBeNull();

    // Library-backed rows get an override editor; emergent rows get promote.
    expect(screen.getByRole("button", { name: "Edit override for Grendel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit override for The Wyrm" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Promote Fen Beast to library" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit override for Fen Beast" }),
    ).not.toBeInTheDocument();
  });

  it("promotes an emergent entity through the generic endpoint", async () => {
    vi.mocked(viewsApi.promoteEntityToLibrary).mockResolvedValue({});
    renderMonstersTab();
    await waitFor(() => screen.getByText("Fen Beast"));

    fireEvent.click(screen.getByRole("button", { name: "Promote Fen Beast to library" }));
    await waitFor(() => screen.getByLabelText(/Target world/i));
    fireEvent.click(screen.getByRole("button", { name: "Promote" }));

    await waitFor(() =>
      expect(viewsApi.promoteEntityToLibrary).toHaveBeenCalledWith("c1", "monsters", "fen-beast", {
        target_world_id: "wod-london",
      }),
    );
  });

  it("surfaces a cast-lookup failure instead of silently hiding membership", async () => {
    vi.mocked(viewsApi.listCharacters).mockResolvedValue([
      {
        character: {
          id: "alistair",
          name: "Alistair",
          role: "major_npc",
          world_id: "wod-london",
          aliases: [],
          age: null,
          tags: [],
          role_tags: [],
          voice: {
            summary: "",
            voice_register: "",
            samples: [],
            speech_patterns: [],
            address_terms: {},
            dos: [],
            donts: [],
          },
          image: null,
          structural_relationships: [],
          household_id: null,
          description: "",
          body: "",
          file_path: "",
          version: 1,
        },
        current_state: {},
        capabilities: [],
        source_chain: libraryRow.source_chain,
        overrides_applied: [],
      } as never,
    ]);
    vi.mocked(viewsApi.listCast).mockRejectedValue(new Error("cast exploded"));
    render(
      <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
        <Routes>
          <Route path="/campaigns/:campaignId/world" element={<WorldView />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => screen.getByText("Alistair"));
    expect(screen.getByRole("alert")).toHaveTextContent(/cast lookup failed/i);
  });

  it("edits an override through the structured form, submitting only changed keys", async () => {
    vi.mocked(viewsApi.patchEntityOverride).mockResolvedValue({
      ok: true,
      world_id: "wod-london",
      ref: "library:worlds/wod-london/monsters/grendel",
    });
    renderMonstersTab();
    await waitFor(() => screen.getByText("Grendel"));

    fireEvent.click(screen.getByRole("button", { name: "Edit override for Grendel" }));
    const nameInput = await screen.findByLabelText("Name");
    fireEvent.change(nameInput, { target: { value: "Grendel, Awakened" } });
    fireEvent.click(screen.getByRole("button", { name: "Save override" }));

    await waitFor(() =>
      expect(viewsApi.patchEntityOverride).toHaveBeenCalledWith("c1", "monsters", "grendel", {
        override: { name: "Grendel, Awakened" },
        world_id: "wod-london",
      }),
    );
  });
});
