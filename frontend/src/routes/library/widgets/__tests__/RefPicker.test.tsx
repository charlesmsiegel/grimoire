import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RefPicker } from "../RefPicker";
import * as libraryModule from "../../../../api/library";

vi.mock("../../../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../../../api/library");
  return { ...actual, libraryApi: { ...actual.libraryApi, listEntities: vi.fn() } };
});

describe("RefPicker", () => {
  it("loads suggestions and reports the chosen value", async () => {
    vi.mocked(libraryModule.libraryApi.listEntities).mockResolvedValue([
      { asset_id: "alistair", name: "Alistair" } as never,
    ]);
    const onChange = vi.fn();
    render(<RefPicker worldId="w1" refKinds={["character"]} value="" onChange={onChange} />);
    await waitFor(() =>
      expect(screen.getByRole("option", { hidden: true })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "alistair" } });
    expect(onChange).toHaveBeenCalledWith("alistair");
  });
});
