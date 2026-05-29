import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { EntityForm } from "../EntityForm";
import { getDescriptor } from "../entitySchemas";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual("../../../api/library");
  return { ...(actual as object), libraryApi: { listEntities: vi.fn().mockResolvedValue([]) } };
});

const descriptor = getDescriptor("character")!;

describe("EntityForm", () => {
  it("renders known fields as widgets and edits them", () => {
    const onFm = vi.fn();
    render(
      <EntityForm
        descriptor={descriptor}
        worldId="w1"
        frontmatter={{ name: "Alistair", role: "major_npc" }}
        body=""
        onFrontmatterChange={onFm}
        onBodyChange={() => {}}
      />,
    );
    const name = screen.getByDisplayValue("Alistair");
    fireEvent.change(name, { target: { value: "Al" } });
    expect(onFm).toHaveBeenCalledWith(expect.objectContaining({ name: "Al" }));
  });

  it("routes unknown keys into the Advanced section", () => {
    render(
      <EntityForm
        descriptor={descriptor}
        worldId="w1"
        frontmatter={{ name: "X", custom_field: "keepme" }}
        body=""
        onFrontmatterChange={() => {}}
        onBodyChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByText(/Advanced/));
    expect(screen.getByDisplayValue("keepme")).toBeInTheDocument();
  });
});
