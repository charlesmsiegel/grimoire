import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MechanicsEditor } from "../MechanicsEditor";
import { mechanicsApi } from "../../../../api/library/mechanics";

const manifest = {
  id: "acme",
  name: "Acme",
  version: "1.0.0",
  api_version: "1",
  author: "",
  homepage: "",
  description: "",
  sheet_kinds: ["character"],
  content_kinds: [],
  capabilities: [],
  ui: {},
};

describe("MechanicsEditor", () => {
  it("shows tabs and saves the manifest", async () => {
    const spy = vi
      .spyOn(mechanicsApi, "updateManifest")
      .mockResolvedValue({ discovered: [], loaded: ["acme"], failed: [], removed: [] });
    render(<MechanicsEditor manifest={manifest} themeCss={null} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: /manifest/i }));
    fireEvent.click(screen.getByRole("button", { name: /save manifest/i }));
    expect(spy).toHaveBeenCalledWith("acme", expect.objectContaining({ id: "acme" }));
  });
});
