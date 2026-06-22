import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LorebookImport } from "./LorebookImport";

vi.mock("../api/client", () => ({
  api: { lorebookParse: vi.fn(), lorebookImport: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.lorebookParse as any).mockResolvedValue({
    entries: [
      { name: "Salt Pact", keys: ["pact"], body: "binds", category: "lore" },
      { name: "The Docks", keys: ["docks"], body: "wet", category: "lore" },
    ],
  });
  (api.lorebookImport as any).mockResolvedValue({ created: [{ kind: "lore", id: "a" }, { kind: "locations", id: "b" }] });
});

function pickFile() {
  const input = screen.getByLabelText(/lorebook or card file/i);
  fireEvent.change(input, { target: { files: [new File(["{}"], "wi.json")] } });
}

test("parse renders the entries and import posts the re-routed list", async () => {
  render(<LorebookImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /parse/i }));
  await screen.findByDisplayValue("Salt Pact");
  // route the second entry to locations
  fireEvent.change(screen.getByLabelText("category 1"), { target: { value: "locations" } });
  fireEvent.click(screen.getByRole("button", { name: /import 2 entries/i }));
  await waitFor(() => {
    const entries = (api.lorebookImport as any).mock.calls[0][1];
    expect(entries[0].category).toBe("lore");
    expect(entries[1].category).toBe("locations");
  });
  await screen.findByText(/imported 2 entries/i);
});

test("parse failure shows the error banner", async () => {
  (api.lorebookParse as any).mockRejectedValue({ detail: "could not parse: bad" });
  render(<LorebookImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /parse/i }));
  await screen.findByText(/could not parse: bad/i);
});
