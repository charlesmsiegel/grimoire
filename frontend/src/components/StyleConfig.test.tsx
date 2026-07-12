import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StyleConfig } from "./StyleConfig";

vi.mock("../api/client", () => ({
  api: { getCampaignStyle: vi.fn(), setCampaignStyle: vi.fn(), listStyles: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaignStyle as any).mockResolvedValue({ style_id: "" });
  (api.setCampaignStyle as any).mockResolvedValue({ ok: true });
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
});

test("picks a style and saves it", async () => {
  render(<StyleConfig cid="run" />);
  const sel = await screen.findByLabelText("Prose style");
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCampaignStyle).toHaveBeenCalledWith("run", "noir-detective"));
});

test("shows the currently saved style on load", async () => {
  (api.getCampaignStyle as any).mockResolvedValue({ style_id: "gothic-horror" });
  render(<StyleConfig cid="run" />);
  const sel = await screen.findByLabelText("Prose style") as HTMLSelectElement;
  await waitFor(() => expect(sel.value).toBe("gothic-horror"));
});
