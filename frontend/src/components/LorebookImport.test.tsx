import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LorebookImport } from "./LorebookImport";

vi.mock("../api/client", () => ({
  api: { lorebookParse: vi.fn(), lorebookImport: vi.fn(), entityKinds: vi.fn() },
}));
import { api } from "../api/client";
// NOT from the mocked client: this is the list the build itself ships, and the
// fallback test below is about what the dialog offers when the server's list
// never arrives.
import { ENTITY_KINDS } from "../api/types";

beforeEach(() => {
  vi.clearAllMocks();
  (api.lorebookParse as any).mockResolvedValue({
    entries: [
      { name: "Salt Pact", keys: ["pact"], body: "binds", category: "lore" },
      { name: "The Docks", keys: ["docks"], body: "wet", category: "lore" },
    ],
  });
  (api.lorebookImport as any).mockResolvedValue({ created: [{ kind: "lore", id: "a" }, { kind: "locations", id: "b" }] });
  (api.entityKinds as any).mockResolvedValue({ kinds: [...ENTITY_KINDS] });
});

function pickFile() {
  const input = screen.getByLabelText(/lorebook or card file/i);
  fireEvent.change(input, { target: { files: [new File(["{}"], "wi.json")] } });
}

function options(label: string): string[] {
  return [...screen.getByLabelText<HTMLSelectElement>(label).options].map((o) => o.value);
}

async function parsed() {
  render(<LorebookImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /parse/i }));
  await screen.findByDisplayValue("Salt Pact");
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

test("the category options are the server's kinds, one of which this build has never heard of", async () => {
  // The point of asking the server (#138): a kind added to `ENTITY_KINDS`
  // reaches the review table without a frontend release, and commits as itself.
  (api.entityKinds as any).mockResolvedValue({ kinds: ["lore", "locations", "vehicles"] });
  await parsed();
  expect(options("category 0")).toEqual(["lore", "locations", "vehicles"]);

  fireEvent.change(screen.getByLabelText("category 1"), { target: { value: "vehicles" } });
  fireEvent.click(screen.getByRole("button", { name: /import 2 entries/i }));
  await waitFor(() => {
    expect((api.lorebookImport as any).mock.calls[0][1][1].category).toBe("vehicles");
  });
});

test("a kinds read that fails leaves the dropdown on the build's own kinds", async () => {
  // The dropdown is an auxiliary read; losing it must not cost the user the
  // import they already parsed.
  (api.entityKinds as any).mockRejectedValue(new Error("offline"));
  await parsed();
  expect(options("category 0")).toEqual([...ENTITY_KINDS]);
  fireEvent.click(screen.getByRole("button", { name: /import 2 entries/i }));
  await screen.findByText(/imported 2 entries/i);
});

test("a row whose kind the list is missing keeps it, rather than displaying another one", async () => {
  // The failure this guards: a `<select>` with no matching option renders as
  // its first, so the row would read `locations` and import as `vehicles`.
  (api.entityKinds as any).mockResolvedValue({ kinds: ["lore", "locations"] });
  (api.lorebookParse as any).mockResolvedValue({
    entries: [{ name: "Salt Pact", keys: ["pact"], body: "binds", category: "vehicles" }],
  });
  await parsed();
  expect(options("category 0")).toEqual(["lore", "locations", "vehicles"]);
  expect(screen.getByLabelText<HTMLSelectElement>("category 0").value).toBe("vehicles");

  fireEvent.click(screen.getByRole("button", { name: /import 1 entry/i }));
  await waitFor(() => {
    expect((api.lorebookImport as any).mock.calls[0][1][0].category).toBe("vehicles");
  });
});

test("nothing is asked of the server until there are rows to file", async () => {
  // The dialog mounts inside a collapsed <details> on the Lore section, so an
  // unconditional read would fire on every visit to a page nobody imported on
  // -- and a file with nothing importable in it has no Category column either.
  (api.lorebookParse as any).mockResolvedValue({ entries: [] });
  render(<LorebookImport wid="w" />);
  await screen.findByRole("button", { name: /parse/i });
  expect(api.entityKinds).not.toHaveBeenCalled();

  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /parse/i }));
  await screen.findByText(/no importable entries/i);
  expect(api.entityKinds).not.toHaveBeenCalled();

  // and a parse that does yield rows asks
  (api.lorebookParse as any).mockResolvedValue({
    entries: [{ name: "Salt Pact", keys: ["pact"], body: "binds", category: "lore" }],
  });
  fireEvent.click(screen.getByRole("button", { name: /parse/i }));
  await screen.findByDisplayValue("Salt Pact");
  expect(api.entityKinds).toHaveBeenCalled();
});
