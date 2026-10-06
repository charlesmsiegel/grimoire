import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Actor, AuthorsNotes, AuthorsNotesNext } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      getAuthorsNotes: vi.fn(),
      setCampaignAuthorsNote: vi.fn(),
      setSceneAuthorsNote: vi.fn(),
      setCharacterAuthorsNote: vi.fn(),
    },
  };
});

import { api } from "../api/client";
import { AuthorsNotesPanel } from "./AuthorsNotesPanel";

const mocked = vi.mocked(api);

const NOTES: AuthorsNotes = {
  campaign: { text: "Keep the storm audible.", depth: 4, every: 1 },
  scenes: { s: { text: "Rain on the roof.", depth: 2, every: 3 } },
  characters: {},
};
const EMPTY: AuthorsNotes = { campaign: null, scenes: {}, characters: {} };

const CAST: Actor[] = [
  { kind: "characters", id: "mara", role: "npc", name: "Mara" },
  { kind: "characters", id: "winifred", role: "npc", name: "Winifred" },
  { kind: "pcs", id: "seraphine", role: "player", name: "Seraphine" },
];

const NEXT: AuthorsNotesNext = {
  turn: 3, count: 2,
  notes: [
    { level: "campaign", depth: 4, every: 1, applies: true },
    { level: "scene", depth: 2, every: 2, applies: false },
    { level: "character", depth: 0, every: 1, applies: true, ref: "characters:mara", name: "Mara" },
  ],
};

function renderPanel(onSaved = vi.fn(), next: AuthorsNotesNext | null = null) {
  render(<AuthorsNotesPanel cid="c" sid="s" cast={CAST} next={next} onSaved={onSaved} />);
  return onSaved;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getAuthorsNotes.mockResolvedValue(NOTES);
  mocked.setCampaignAuthorsNote.mockResolvedValue(NOTES);
  mocked.setSceneAuthorsNote.mockResolvedValue(NOTES);
  mocked.setCharacterAuthorsNote.mockResolvedValue(NOTES);
});

describe("AuthorsNotesPanel", () => {
  it("shows the campaign note and saves an edit through the campaign route", async () => {
    const onSaved = renderPanel();
    const box = await screen.findByLabelText<HTMLTextAreaElement>("Author's note");
    expect(box.value).toBe("Keep the storm audible.");
    expect(screen.getByRole("tab", { name: "Campaign" })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(box, { target: { value: "Keep the storm audible." } });
    fireEvent.change(screen.getByLabelText("Depth"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("Every N turns"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocked.setCampaignAuthorsNote).toHaveBeenCalledWith(
      "c", { text: "Keep the storm audible.", depth: 6, every: 2 });
  });

  it("re-reads the notes when the scene is renamed under it", async () => {
    const { rerender } = render(
      <AuthorsNotesPanel cid="c" sid="s" cast={CAST} next={null} onSaved={vi.fn()} />);
    await screen.findByLabelText("Author's note");
    // A rename moves the sid; the server answers keyed by the new one.
    mocked.getAuthorsNotes.mockResolvedValue({ ...NOTES, scenes: { s2: NOTES.scenes.s } });
    rerender(<AuthorsNotesPanel cid="c" sid="s2" cast={CAST} next={null} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "This scene" }));
    await waitFor(() => expect(
      screen.getByLabelText<HTMLTextAreaElement>("Author's note").value).toBe("Rain on the roof."));
    expect(mocked.getAuthorsNotes).toHaveBeenCalledTimes(2);
  });

  it("saves the scene's note through the scene route", async () => {
    renderPanel();
    await screen.findByLabelText("Author's note");
    fireEvent.click(screen.getByRole("tab", { name: "This scene" }));
    const box = screen.getByLabelText<HTMLTextAreaElement>("Author's note");
    expect(box.value).toBe("Rain on the roof.");
    expect(screen.getByLabelText<HTMLInputElement>("Every N turns").value).toBe("3");
    fireEvent.change(box, { target: { value: "Rain harder." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocked.setSceneAuthorsNote).toHaveBeenCalledWith(
      "c", "s", { text: "Rain harder.", depth: 2, every: 3 }));
  });

  it("lists only NPC characters and saves the picked one's note", async () => {
    mocked.getAuthorsNotes.mockResolvedValue(EMPTY);
    renderPanel();
    await screen.findByLabelText("Author's note");
    fireEvent.click(screen.getByRole("tab", { name: "Character" }));
    const picker = screen.getByLabelText<HTMLSelectElement>("Character");
    expect([...picker.options].map((o) => o.textContent)).toEqual(["Mara", "Winifred"]);
    fireEvent.change(picker, { target: { value: "characters:winifred" } });
    const box = screen.getByLabelText<HTMLTextAreaElement>("Author's note");
    expect(box.value).toBe("");
    expect(screen.getByLabelText<HTMLInputElement>("Depth").value).toBe("4");
    fireEvent.change(box, { target: { value: "Winifred whispers." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocked.setCharacterAuthorsNote).toHaveBeenCalledWith(
      "c", "characters:winifred", { text: "Winifred whispers.", depth: 4, every: 1 }));
  });

  it("lists which notes apply next turn, a character's when they speak", async () => {
    renderPanel(vi.fn(), NEXT);
    await screen.findByLabelText("Author's note");
    expect(screen.getByText("Campaign: applies next turn")).toBeInTheDocument();
    expect(screen.getByText("This scene: not next turn (every 2)")).toBeInTheDocument();
    expect(screen.getByText("Mara: applies when Mara speaks")).toBeInTheDocument();
  });
});
