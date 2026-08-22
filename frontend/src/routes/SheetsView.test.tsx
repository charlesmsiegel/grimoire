import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SheetsView from "./SheetsView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(), getCampaignModule: vi.fn(), readModule: vi.fn(),
    getCampaignSheetRoster: vi.fn(), createMissingSheets: vi.fn(),
    // SheetPanel's own reads, which this page renders the detail through
    getSheet: vi.fn(), putSheet: vi.fn(),
  },
}));
import { api } from "../api/client";

/** A two-type characters kind and a one-type items kind: the two cases the
 *  bulk create treats differently, in one module. */
const MODULE = {
  id: "pool-basic", source: "builtin",
  manifest: { id: "pool-basic", name: "Pool Basic" },
  sheets: {
    groups: {},
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: [], fields: [] },
      shifter: { label: "Shifter", kind: "characters", groups: [], fields: [] },
      talisman: { label: "Talisman", kind: "items", groups: [], fields: [] },
    },
  },
  checks: {}, rules: [], content: [], errors: [],
};

const row = (over: { id: string; name: string } & Record<string, unknown>) => ({
  sheeted: false, sheet_type: null, errors: [], unspent: {}, ...over,
});

const ROSTER = {
  characters: [
    row({ id: "mara", name: "Mara", sheeted: true, sheet_type: "medium",
          unspent: { abilities: 6 } }),
    row({ id: "winifred", name: "Winifred" }),
  ],
  items: [row({ id: "moon-disc", name: "Moon Disc", sheeted: true, sheet_type: "talisman" })],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch" }, body: "" });
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  (api.readModule as any).mockResolvedValue(MODULE);
  (api.getCampaignSheetRoster as any).mockResolvedValue({ roster: ROSTER });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
});

function renderSheets() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run/sheets"]}>
      <PaletteProvider>
        <Hotkey />
        <CommandPalette />
        <Routes>
          <Route path="/campaigns/:cid/sheets" element={<SheetsView />} />
          <Route path="/campaigns/:cid" element={<div>the play view</div>} />
        </Routes>
      </PaletteProvider>
    </MemoryRouter>,
  );
}

function Hotkey() { usePaletteHotkey(); return null; }

// Named, not just by role: the detail pane's sidebar is a <complementary>
// too, so a bare role query goes ambiguous the moment a member is selected.
const column = () => within(screen.getByRole("complementary", { name: "The cast" }));
const railRow = (name: string) =>
  column().getByRole("button", { name: new RegExp(name) });

test("the rail is the cast, badged with who has a sheet and who does not", async () => {
  renderSheets();
  await screen.findByText("Sheet coverage");

  // Both kinds the module sheets, each with its own count
  expect(column().getByText("Characters")).toBeInTheDocument();
  expect(column().getByText("1/2")).toBeInTheDocument();

  // A sheet that exists but still owes its creation pool is neither "Missing"
  // nor silently fine -- that distinction is the whole of #201's third bullet.
  expect(railRow("Mara")).toHaveTextContent("6 left");
  expect(railRow("Winifred")).toHaveTextContent("Missing");
  expect(railRow("Moon Disc")).toHaveTextContent("Sheet");
});

test("picking a cast member opens their sheet, read-only", async () => {
  (api.getSheet as any).mockResolvedValue({
    sheet: { sheet_type: "medium", fields: {}, derived: {}, errors: [], gen: "g1" },
  });
  renderSheets();
  await screen.findByText("Sheet coverage");

  fireEvent.click(railRow("Mara"));
  await screen.findByRole("heading", { name: "Mara" });
  // SheetPanel's read-only summary, and its explicit edit step -- not a form
  expect(screen.getByRole("button", { name: "Open sheet" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.getByText("6 unspent in the abilities creation pool.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "‹ All sheets" }));
  await screen.findByText("Sheet coverage");
});

test("create missing sends the chosen type per kind and reports what it did", async () => {
  (api.createMissingSheets as any).mockResolvedValue({
    created: [{ kind: "characters", id: "winifred", name: "Winifred",
                sheet_type: "shifter", unspent: { abilities: 6 } }],
    skipped: [], failed: [],
  });
  renderSheets();
  await screen.findByText("Sheet coverage");

  fireEvent.change(screen.getByLabelText("Sheet type for Characters"),
                   { target: { value: "shifter" } });
  fireEvent.click(screen.getByRole("button", { name: /Create missing sheets/ }));

  await waitFor(() => expect(api.createMissingSheets).toHaveBeenCalledWith(
    "run", { characters: "shifter" }));
  // The report names the sheet it created incomplete rather than counting it
  // in with the rest -- a bulk create that only said "1 created" would be the
  // silent skip in a different hat.
  await screen.findByText("1 sheet created.");
  expect(screen.getByText("Winifred: 6 unspent in abilities")).toBeInTheDocument();
  // and the roster is re-read, so the rail stops saying Missing
  expect(api.getCampaignSheetRoster).toHaveBeenCalledTimes(2);
});

test("the palette offers the members still missing a sheet, not this page", async () => {
  // `usePaletteSource` only registers while this page is mounted, so an entry
  // that merely navigated here would be reachable only by already being here.
  renderSheets();
  await screen.findByText("Sheet coverage");
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  fireEvent.click(await screen.findByText("Characters without a sheet"));
  await screen.findByRole("heading", { name: "Winifred" });
});

test("a kind with more than one sheet type says so until one is chosen", async () => {
  renderSheets();
  await screen.findByText("Sheet coverage");
  expect(screen.getByText("2 types — choose one or this kind is skipped."))
    .toBeInTheDocument();

  // The action reads these selects, so it renders beside them rather than in
  // the shell's pinned footer -- which below 720px is a different view.
  const overview = screen.getByRole("table").closest(".page-wide");
  expect(within(overview as HTMLElement)
    .getByRole("button", { name: /Create missing sheets/ })).toBeInTheDocument();
  // Ordered by what the reader sees, not by the id behind it
  expect(within(screen.getByLabelText("Sheet type for Characters"))
    .getAllByRole("option").map((o) => o.textContent))
    .toEqual(["Choose…", "Medium", "Shifter"]);

  // items has exactly one type, so it needs no choice and offers no select
  expect(screen.queryByLabelText("Sheet type for Items")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Sheet type for Characters"),
                   { target: { value: "medium" } });
  await waitFor(() => expect(
    screen.queryByText("2 types — choose one or this kind is skipped.")).toBeNull());
});

test("a skipped kind is reported, not swallowed", async () => {
  (api.createMissingSheets as any).mockResolvedValue({
    created: [], failed: [],
    skipped: [{ kind: "characters", reason: "this module has 2 sheet types for characters" }],
  });
  renderSheets();
  await screen.findByText("Sheet coverage");

  fireEvent.click(screen.getByRole("button", { name: /Create missing sheets/ }));
  await screen.findByText(/Characters skipped/);
  expect(screen.getByText(/this module has 2 sheet types for characters/))
    .toBeInTheDocument();
});

test("nothing missing leaves the bulk button disabled and offers no choice", async () => {
  (api.getCampaignSheetRoster as any).mockResolvedValue({
    roster: {
      // Two sheet types, every member already sheeted: naming one of them
      // under CREATE AS would be a claim about rows nobody is creating.
      characters: [row({ id: "mara", name: "Mara", sheeted: true, sheet_type: "medium" })],
      items: [row({ id: "moon-disc", name: "Moon Disc", sheeted: true,
                    sheet_type: "talisman" })],
    },
  });
  renderSheets();
  await screen.findByText("Every cast member has a sheet.");
  expect(screen.getByRole("button", { name: /Create missing sheets/ })).toBeDisabled();
  expect(screen.queryByLabelText("Sheet type for Characters")).not.toBeInTheDocument();
});

test("a campaign with no module bound says so instead of an empty cast", async () => {
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "", resolved: null, source: null });
  (api.getCampaignSheetRoster as any).mockResolvedValue({ roster: {} });
  renderSheets();
  await screen.findByText(/no sheets to keep/);
  // and the rail says the same thing where the cast would be
  expect(column().getByText("No mechanics bound.")).toBeInTheDocument();
  // Not a disabled button: with no module there is no cast to sheet and no
  // table for the action to belong to, so the whole overview is absent.
  expect(screen.queryByRole("button", { name: /Create missing sheets/ })).toBeNull();
});

test("creating one sheet in the detail pane refreshes the rail beside it", async () => {
  // The bug this covers: `SheetPanel` refetched its own state and told nobody,
  // so the rail one metre away went on saying "Missing" about a sheet that had
  // just been created -- on the one screen whose entire job is who has a sheet.
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.putSheet as any).mockResolvedValue({ ok: true });
  renderSheets();
  await screen.findByText("Sheet coverage");
  fireEvent.click(railRow("Winifred"));
  await screen.findByRole("heading", { name: "Winifred" });

  // SheetPanel's own single-sheet create, and the roster it now invalidates
  (api.getCampaignSheetRoster as any).mockResolvedValue({
    roster: {
      ...ROSTER,
      characters: [
        ROSTER.characters[0],
        row({ id: "winifred", name: "Winifred", sheeted: true, sheet_type: "medium" }),
      ],
    },
  });
  (api.getSheet as any).mockResolvedValue({
    sheet: { sheet_type: "medium", fields: {}, derived: {}, errors: [], gen: "g2" },
  });
  fireEvent.change(screen.getByLabelText("Sheet type"), { target: { value: "medium" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(railRow("Winifred")).toHaveTextContent("Sheet"));
});

test("a failed create surfaces the reason rather than a silent no-op", async () => {
  (api.createMissingSheets as any).mockRejectedValue(new Error("no module resolved"));
  renderSheets();
  await screen.findByText("Sheet coverage");
  fireEvent.click(screen.getByRole("button", { name: /Create missing sheets/ }));
  await screen.findByText("no module resolved");
});
