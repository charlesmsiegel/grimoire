import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ConvertModal } from "./ConvertModal";
import * as libraryModule from "../../api/library";

vi.mock("../../api/library", async () => {
  const actual = await vi.importActual<typeof libraryModule>("../../api/library");
  return {
    ...actual,
    libraryApi: {
      ...actual.libraryApi,
      previewReclassify: vi.fn(),
      commitReclassify: vi.fn(),
    },
  };
});

describe("ConvertModal", () => {
  beforeEach(() => {
    vi.mocked(libraryModule.libraryApi.previewReclassify).mockResolvedValue({
      source_id: "beatrice",
      target_kind: "character",
      frontmatter: { name: "Beatrice", aliases: ["b"] },
      body: "She lived.",
      kept: ["name", "aliases"],
      dropped: ["priority"],
      into_notes: [],
      warnings: ["matching metadata discarded (lore-only fields)"],
      required_overrides: [],
      suggestion: { kind: "character", confidence: 0.85, reason: "pronouns" },
    });
    vi.mocked(libraryModule.libraryApi.commitReclassify).mockResolvedValue({
      source_id: "beatrice",
      target_id: "beatrice",
      target_kind: "character",
      fields_kept: ["name", "aliases"],
      fields_dropped: ["priority"],
      fields_into_notes: [],
      warnings: [],
    });
  });

  it("renders the mapping preview after loading", async () => {
    render(
      <ConvertModal worldId="w" sourceId="beatrice" onClose={() => {}} onConverted={() => {}} />,
    );
    await waitFor(() => screen.getByText(/Beatrice/));
    // Frontmatter JSON section
    expect(screen.getByText(/"name"/)).toBeInTheDocument();
    // "Dropped" list item
    expect(screen.getByText(/^priority$/)).toBeInTheDocument();
    // Warning
    expect(screen.getByText(/matching metadata/)).toBeInTheDocument();
  });

  it("disables commit until required overrides are filled in", async () => {
    vi.mocked(libraryModule.libraryApi.previewReclassify).mockResolvedValue({
      source_id: "chantry",
      target_kind: "location",
      frontmatter: { name: "The Chantry" },
      body: "",
      kept: ["name"],
      dropped: [],
      into_notes: [],
      warnings: [],
      required_overrides: ["kind"],
      suggestion: { kind: "location", confidence: 0.7, reason: "place noun" },
    });
    render(
      <ConvertModal
        worldId="w"
        sourceId="chantry"
        initialTargetKind="location"
        onClose={() => {}}
        onConverted={() => {}}
      />,
    );
    const commit = await screen.findByRole("button", { name: /^Convert$/i });
    expect(commit).toBeDisabled();
    const kindInput = await screen.findByLabelText(/^kind$/i);
    fireEvent.change(kindInput, { target: { value: "building" } });
    await waitFor(() => expect(commit).not.toBeDisabled());
  });

  it("calls commitReclassify with overrides on submit", async () => {
    const onConverted = vi.fn();
    render(
      <ConvertModal worldId="w" sourceId="beatrice" onClose={() => {}} onConverted={onConverted} />,
    );
    await waitFor(() => screen.getByText(/Beatrice/));
    fireEvent.click(screen.getByRole("button", { name: /^Convert$/i }));
    await waitFor(() => expect(onConverted).toHaveBeenCalled());
    expect(libraryModule.libraryApi.commitReclassify).toHaveBeenCalledWith(
      "w",
      "beatrice",
      expect.objectContaining({ target_kind: "character" }),
    );
  });
});
