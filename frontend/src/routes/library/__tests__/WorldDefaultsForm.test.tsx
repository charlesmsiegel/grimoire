import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WorldDefaultsForm } from "../WorldDefaultsForm";
import * as libraryModule from "../../../api/library";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      listStyleGuides: vi.fn(),
      listImagePresets: vi.fn(),
    },
  };
});

describe("WorldDefaultsForm", () => {
  it("populates style-guide + image-preset selects from libraryApi", async () => {
    vi.mocked(libraryModule.libraryApi.listStyleGuides).mockResolvedValue([
      { asset_id: "shoujo-romance", name: "Shoujo Romance" } as never,
    ]);
    vi.mocked(libraryModule.libraryApi.listImagePresets).mockResolvedValue([
      { asset_id: "anime", name: "Anime" } as never,
    ]);

    render(
      <WorldDefaultsForm
        value={{
          starting_location: "classroom",
          default_style_guide_id: "shoujo-romance",
          default_image_preset_id: "anime",
        }}
        onChange={vi.fn()}
      />,
    );
    await waitFor(() => screen.getByRole("option", { name: /Shoujo Romance/ }));
    expect(screen.getByRole("option", { name: /Anime/ })).toBeInTheDocument();
  });

  it("editing starting_location fires onChange", () => {
    vi.mocked(libraryModule.libraryApi.listStyleGuides).mockResolvedValue([]);
    vi.mocked(libraryModule.libraryApi.listImagePresets).mockResolvedValue([]);
    const onChange = vi.fn();
    render(
      <WorldDefaultsForm
        value={{ starting_location: "a", default_style_guide_id: "", default_image_preset_id: "" }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/starting location/i), { target: { value: "b" } });
    expect(onChange).toHaveBeenLastCalledWith({
      starting_location: "b",
      default_style_guide_id: "",
      default_image_preset_id: "",
    });
  });
});
