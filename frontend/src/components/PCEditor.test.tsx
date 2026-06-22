import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", () => ({
  api: {
    listPCs: vi.fn(), listTags: vi.fn(), readPC: vi.fn(), createPC: vi.fn(),
    updatePC: vi.fn(), deletePC: vi.fn(), createPCVersion: vi.fn(), updatePCVersion: vi.fn(),
  },
}));
import { api } from "../api/client";

const DETAIL = {
  meta: { id: "elara", name: "Elara", tags: [], default_version: "default" },
  versions: [{ id: "default", name: "default", persona: { name: "Elara", pronouns: "she/her", summary: "scholar", description: "a wanderer" } }],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listTags as any).mockResolvedValue({ student: "Student" });
  (api.readPC as any).mockResolvedValue(DETAIL);
  (api.createPC as any).mockResolvedValue({ pc: "rook", version: "default" });
  (api.updatePC as any).mockResolvedValue({ ok: true });
  (api.updatePCVersion as any).mockResolvedValue({ ok: true });
  (api.createPCVersion as any).mockResolvedValue({ version: "young" });
});

test("creating a PC prompts for a name and posts it", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Rook");
  render(<PCEditor wid="w" />);
  await screen.findByText("Elara");
  fireEvent.click(screen.getByRole("button", { name: /new pc/i }));
  await waitFor(() => expect(api.createPC).toHaveBeenCalledWith("w", { name: "Rook" }));
});

test("editing persona saves the selected version", async () => {
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "a sage" } });
  fireEvent.click(screen.getByRole("button", { name: /save persona/i }));
  await waitFor(() =>
    expect(api.updatePCVersion).toHaveBeenCalledWith("w", "elara", "default",
      expect.objectContaining({ description: "a sage" })),
  );
});

test("toggling a tag chip updates the PC tags", async () => {
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  fireEvent.click(await screen.findByRole("button", { name: "Student" }));
  await waitFor(() => expect(api.updatePC).toHaveBeenCalledWith("w", "elara", { tags: ["student"] }));
});

test("adding a version prompts and posts the current persona", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Young");
  render(<PCEditor wid="w" />);
  fireEvent.click(await screen.findByText("Elara"));
  await screen.findByLabelText("Description");
  fireEvent.click(screen.getByRole("button", { name: /\+ version/i }));
  await waitFor(() =>
    expect(api.createPCVersion).toHaveBeenCalledWith("w", "elara",
      expect.objectContaining({ name: "Young" })),
  );
});
