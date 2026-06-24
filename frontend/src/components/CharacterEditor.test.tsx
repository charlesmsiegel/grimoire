import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CharacterEditor } from "./CharacterEditor";

vi.mock("../api/client", () => ({
  api: {
    listCharacters: vi.fn(), readCharacter: vi.fn(), createCharacter: vi.fn(),
    updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
    deleteCharacter: vi.fn(), importCharacter: vi.fn(),
  },
}));
import { api } from "../api/client";

const CARD = {
  spec: "chara_card_v3", spec_version: "3.0",
  data: { name: "Seraphine", description: "keeper", alternate_greetings: ["hi"], extensions: {} },
};
const DETAIL = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
  versions: [{ id: "default", name: "default", card: CARD }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  (api.readCharacter as any).mockResolvedValue(DETAIL);
  (api.createCharacter as any).mockResolvedValue({ character: "rook", version: "default" });
  (api.updateVersion as any).mockResolvedValue({ ok: true });
  (api.importCharacter as any).mockResolvedValue({ character: "imp", version: "default" });
});

test("creating a character prompts and posts the name", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /new character/i }));
  await waitFor(() => expect(api.createCharacter).toHaveBeenCalledWith("w", { name: "Rook" }));
});

test("editing description + alternate greetings (repeatable) saves a rebuilt card", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "cold keeper" } });
  // the seed card has one greeting "hi"; add a second and edit both
  fireEvent.click(screen.getByRole("button", { name: /add greeting/i }));
  const areas = screen.getAllByLabelText(/greeting \d+/i);
  fireEvent.change(areas[0], { target: { value: "line one\nstill one" } });
  fireEvent.change(areas[1], { target: { value: "two" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.description).toBe("cold keeper");
    expect(card.data.alternate_greetings).toEqual(["line one\nstill one", "two"]);
    expect(card.spec).toBe("chara_card_v3"); // preserved
  });
});

test("editing creator and tags saves them", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Creator");
  fireEvent.change(screen.getByLabelText("Creator"), { target: { value: "anon" } });
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "fantasy, oc " } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.creator).toBe("anon");
    expect(card.data.tags).toEqual(["fantasy", "oc"]);
  });
});

test("importing a file posts multipart to importCharacter", async () => {
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const input = screen.getByLabelText("Import character JSON");
  fireEvent.change(input, { target: { files: [new File(["{}"], "c.json")] } });
  await waitFor(() => expect(api.importCharacter).toHaveBeenCalledWith("w", expect.any(File), "json"));
});
