import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ImportDialog } from "../ImportDialog";
import * as importsModule from "../../../api/imports";

vi.mock("../../../api/imports", async () => {
  const actual = await vi.importActual<typeof importsModule>("../../../api/imports");
  return {
    ...actual,
    previewSillyTavernImport: vi.fn(),
    commitSillyTavernImport: vi.fn(),
  };
});

function makePreview() {
  return {
    preview_id: "pid-1",
    expires_in_seconds: 900,
    ingested: {
      data: { id: "beatrice", name: "Beatrice", description: "A witch.", tags: ["witch"] },
      spec: "chara_card_v2",
      spec_version: "",
      creator: "",
      creator_notes: "",
      system_prompt: "",
      post_history_instructions: "",
      alternate_greetings: [],
      extensions: {},
      warnings: [],
      lore_entries: [
        {
          source_index: 0,
          name: "Brackhollow Inn",
          keys: ["inn"],
          body: "A village inn.",
          secondary_keys: [],
          selective_logic: "and_any",
          constant: false,
          enabled: true,
          case_sensitive: false,
          match_whole_words: false,
          priority: 100,
          probability: 100,
          position: "after_cast",
          at_depth: null,
          scan_depth: null,
          comment: "",
        },
        {
          source_index: 1,
          name: "Random Note",
          keys: ["note"],
          body: "Some random fact.",
          secondary_keys: [],
          selective_logic: "and_any",
          constant: false,
          enabled: true,
          case_sensitive: false,
          match_whole_words: false,
          priority: 100,
          probability: 100,
          position: "after_cast",
          at_depth: null,
          scan_depth: null,
          comment: "",
        },
      ],
      greetings: [],
    },
    lore_suggestions: [
      {
        source_index: 0,
        kind: "location" as const,
        confidence: 0.82,
        reason: "title contains a place noun",
      },
      { source_index: 1, kind: "lore" as const, confidence: 0.0, reason: "" },
    ],
  };
}

describe("ImportDialog reclassification flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(importsModule.previewSillyTavernImport).mockResolvedValue(makePreview());
    vi.mocked(importsModule.commitSillyTavernImport).mockResolvedValue({
      result: { created: ["beatrice"], updated: [], skipped: [], warnings: [], errors: [] },
    });
  });

  async function uploadFile() {
    render(<ImportDialog worldId="w1" onClose={() => {}} />);
    const input = await screen.findByLabelText(/Select a SillyTavern card/i);
    fireEvent.change(input, {
      target: { files: [new File(["x"], "card.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(importsModule.previewSillyTavernImport).toHaveBeenCalledTimes(1));
    // Wait for the lore-row UI to render.
    await screen.findByText(/Brackhollow Inn/);
  }

  it("defaults the dropdown to the server suggestion when above threshold", async () => {
    await uploadFile();
    const selects = screen.getAllByLabelText(/Category for/i);
    expect((selects[0] as HTMLSelectElement).value).toBe("location");
    expect((selects[1] as HTMLSelectElement).value).toBe("lore");
  });

  it("reveals the Location 'kind' input when a row's target is Location", async () => {
    await uploadFile();
    const kindInput = await screen.findByLabelText(/Location kind \(row 0\)/i);
    expect(kindInput).toBeInTheDocument();
  });

  it("blocks commit and shows an error when a required override is missing", async () => {
    await uploadFile();
    const commit = await screen.findByRole("button", { name: /^Commit$/ });
    fireEvent.click(commit);
    expect(await screen.findByText(/Location row 0 requires kind/i)).toBeInTheDocument();
    expect(importsModule.commitSillyTavernImport).not.toHaveBeenCalled();
  });

  it("commits with the right lore_overrides shape and excludes lore-as-lore rows", async () => {
    await uploadFile();
    const kindInput = await screen.findByLabelText(/Location kind \(row 0\)/i);
    fireEvent.change(kindInput, { target: { value: "building" } });
    // Set the second row to skip.
    const selects = screen.getAllByLabelText(/Category for/i);
    fireEvent.change(selects[1]!, { target: { value: "skip" } });

    fireEvent.click(await screen.findByRole("button", { name: /^Commit$/ }));
    await waitFor(() => expect(importsModule.commitSillyTavernImport).toHaveBeenCalledTimes(1));
    const args = vi.mocked(importsModule.commitSillyTavernImport).mock.calls[0]!;
    expect(args[0]).toBe("w1");
    expect(args[1]).toBe("pid-1");
    expect(args[3]).toEqual([
      { source_index: 0, kind: "location", overrides: { kind: "building" } },
      { source_index: 1, kind: "skip", overrides: {} },
    ]);
  });
});
