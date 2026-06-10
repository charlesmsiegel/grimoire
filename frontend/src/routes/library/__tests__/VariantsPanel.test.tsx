import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { VariantsPanel } from "../VariantsPanel";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listCharacterVariants: vi.fn(),
      putCharacterVariant: vi.fn(),
      deleteCharacterVariant: vi.fn(),
    },
  };
});

const VARIANT = {
  id: "young",
  world_id: "wod-london",
  character_id: "alistair",
  label: "Young Alistair",
  frontmatter: { label: "Young Alistair", age: "25" },
  body: "A brash newcomer.",
  path: "library/worlds/wod-london/characters/alistair/variants/young.md",
};

describe("VariantsPanel (in-world variants)", () => {
  it("lists variants with their overridden fields", async () => {
    vi.mocked(libraryModule.libraryApi.listCharacterVariants).mockResolvedValue([VARIANT]);

    render(<VariantsPanel worldId="wod-london" characterId="alistair" />);

    await waitFor(() => expect(screen.getByText("Young Alistair")).toBeInTheDocument());
    expect(screen.getByText("age")).toBeInTheDocument();
    expect(libraryModule.libraryApi.listCharacterVariants).toHaveBeenCalledWith(
      "wod-london",
      "alistair",
    );
  });

  it("shows the empty state when the character has no variants", async () => {
    vi.mocked(libraryModule.libraryApi.listCharacterVariants).mockResolvedValue([]);

    render(<VariantsPanel worldId="wod-london" characterId="alistair" />);

    await waitFor(() => expect(screen.getByText(/No variants yet/)).toBeInTheDocument());
  });

  it("rejects creating a variant whose label collides with an existing id", async () => {
    vi.mocked(libraryModule.libraryApi.listCharacterVariants).mockResolvedValue([VARIANT]);
    vi.mocked(libraryModule.libraryApi.putCharacterVariant).mockClear();

    render(<VariantsPanel worldId="wod-london" characterId="alistair" />);
    await waitFor(() => expect(screen.getByText("Young Alistair")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "New variant" }));
    fireEvent.change(screen.getByLabelText("Variant label"), {
      target: { value: "YOUNG" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.getByText(/already exists/)).toBeInTheDocument());
    expect(libraryModule.libraryApi.putCharacterVariant).not.toHaveBeenCalled();
  });

  it("creates a variant with a slugified id from the label", async () => {
    vi.mocked(libraryModule.libraryApi.listCharacterVariants).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.putCharacterVariant).mockResolvedValue(VARIANT);

    render(<VariantsPanel worldId="wod-london" characterId="alistair" />);
    await waitFor(() => expect(screen.getByText(/No variants yet/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "New variant" }));
    fireEvent.change(screen.getByLabelText("Variant label"), {
      target: { value: "Young Alistair" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(libraryModule.libraryApi.putCharacterVariant).toHaveBeenCalledWith(
        "wod-london",
        "alistair",
        "young-alistair",
        { label: "Young Alistair" },
      ),
    );
  });
});
