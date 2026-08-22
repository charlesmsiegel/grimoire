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

test("the options are what the server files under, narrowed to what it can show", async () => {
  // The point of asking the server (#138), and both halves of the answer: the
  // server's list drops a kind it would refuse on commit, and this build's own
  // drops one it has no tab, label or editor for.
  (api.entityKinds as any).mockResolvedValue({ kinds: ["lore", "locations", "vehicles"] });
  await parsed();
  expect(options("category 0")).toEqual(["locations", "lore"]);   // no `vehicles`
  expect(options("category 0")).not.toContain("items");           // server did not offer it

  fireEvent.change(screen.getByLabelText("category 1"), { target: { value: "locations" } });
  fireEvent.click(screen.getByRole("button", { name: /import 2 entries/i }));
  await waitFor(() => {
    expect((api.lorebookImport as any).mock.calls[0][1][1].category).toBe("locations");
  });
});

test("two lists with nothing in common fall back rather than emptying the dropdown", async () => {
  // An intersection this cannot adjudicate is treated like no answer: an empty
  // dropdown would make every row uncommittable.
  (api.entityKinds as any).mockResolvedValue({ kinds: ["vehicles", "vessels"] });
  await parsed();
  expect(api.entityKinds).toHaveBeenCalled();
  expect(options("category 0")).toEqual([...ENTITY_KINDS]);
});

test("a kinds read that fails leaves the dropdown on the build's own kinds", async () => {
  // The dropdown is an auxiliary read; losing it must not cost the user the
  // import they already parsed.
  (api.entityKinds as any).mockRejectedValue(new Error("offline"));
  await parsed();
  // `toHaveBeenCalled` first: `ENTITY_KINDS` is also the hook's initial state,
  // so asserting the options alone would pass with the read deleted outright.
  expect(api.entityKinds).toHaveBeenCalled();
  expect(options("category 0")).toEqual([...ENTITY_KINDS]);
  fireEvent.click(screen.getByRole("button", { name: /import 2 entries/i }));
  await screen.findByText(/imported 2 entries/i);
});

test("an empty or malformed kind list is treated as no answer", async () => {
  // An empty dropdown makes every row uncommittable, which is worse than a
  // list that is merely out of date.
  (api.entityKinds as any).mockResolvedValue({ kinds: [] });
  await parsed();
  expect(api.entityKinds).toHaveBeenCalled();
  expect(options("category 0")).toEqual([...ENTITY_KINDS]);
});

test("a row whose kind the list is missing keeps it, rather than displaying another one", async () => {
  // The failure this guards: a `<select>` with no matching option renders as
  // its first, so the row would read `locations` and import as `vehicles`.
  // Distinct from the narrowing above — the server PARSED this row as
  // `vehicles`, and keeping what it made is not the same as letting the user
  // newly assign a kind this build cannot show.
  (api.entityKinds as any).mockResolvedValue({ kinds: ["lore", "locations"] });
  (api.lorebookParse as any).mockResolvedValue({
    entries: [{ name: "Salt Pact", keys: ["pact"], body: "binds", category: "vehicles" }],
  });
  await parsed();
  expect(options("category 0")).toEqual(["locations", "lore", "vehicles"]);
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
