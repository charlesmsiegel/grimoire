import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SheetPanel from "./SheetPanel";
import type { EntityScope, ModuleDetail, Sheet } from "../api/client";

vi.mock("../api/client", () => ({
  api: { getSheet: vi.fn(), putSheet: vi.fn() },
}));
import { api } from "../api/client";

vi.mock("./SheetEditor", async () => {
  const actual = await vi.importActual<typeof import("./SheetEditor")>("./SheetEditor");
  return { ...actual, default: () => <div data-testid="sheet-editor" /> };
});

const CAMP: EntityScope = { kind: "campaign", id: "run" };

const MOD: ModuleDetail = {
  id: "pool-basic",
  source: "builtin",
  manifest: { id: "pool-basic", name: "Pool Basic" },
  sheets: {
    groups: {
      attributes: {
        label: "Attributes",
        fields: [
          { key: "vigor", label: "Vigor", type: "dots", max: 5 },
          { key: "strength", label: "Strength", type: "number", min: 1, max: 20, default: 10 },
        ],
      },
    },
    sheet_types: {
      medium: {
        label: "Medium",
        kind: "characters",
        groups: ["attributes"],
        fields: [
          { key: "essence", label: "Essence", type: "resource", max: 10 },
          { key: "quirk", label: "Quirk", type: "text" },
          { key: "gear", label: "Gear", type: "list" },
        ],
        derived: { sight_pool: "vigor" },
      },
      shifter: {
        label: "Shifter",
        kind: "characters",
        groups: ["attributes"],
        fields: [{ key: "fury", label: "Fury", type: "resource", max: 5 }],
      },
    },
  },
  checks: {},
  rules: [],
  content: [],
  errors: [],
};

const SHEET: Sheet = {
  sheet_type: "medium",
  fields: { vigor: 3, strength: 10, essence: { current: 6, max: 10 }, quirk: "", gear: [] },
  derived: { sight_pool: 6 },
  errors: [],
  gen: "g1",
};

const FRESH_SHEET: Sheet = { ...SHEET };

beforeEach(() => {
  vi.clearAllMocks();
});

test("renders nothing without module or matching type", () => {
  const { container, rerender } = render(
    <SheetPanel scope={CAMP} module={null} kind="characters" eid="mara" />);
  expect(container.firstChild).toBeNull();
  rerender(<SheetPanel scope={CAMP} module={MOD} kind="lore" eid="secret" />);
  expect(container.firstChild).toBeNull();   // MOD has no lore sheet types
  expect(api.getSheet).not.toHaveBeenCalled();
});

test("unsheeted: create with picked type then editor opens", async () => {
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.putSheet as any).mockResolvedValue({ ok: true });
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "medium" } });
  (api.getSheet as any).mockResolvedValue({ sheet: FRESH_SHEET });
  fireEvent.click(screen.getByText("Create"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    CAMP, "pool-basic", "characters", "mara", { sheet_type: "medium", fields: null, expected: null }));
  expect(await screen.findByTestId("sheet-editor")).toBeInTheDocument();
});

test("sheeted: summary chips + open", async () => {
  (api.getSheet as any).mockResolvedValue({ sheet: SHEET });  // medium, essence 6/10, sight_pool 6
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  expect(await screen.findByText("Medium")).toBeInTheDocument();       // type chip
  expect(screen.getByText(/essence 6\/10/)).toBeInTheDocument();       // resource summary
  expect(screen.getByText(/sight_pool 6/)).toBeInTheDocument();        // derived chip
  fireEvent.click(screen.getByText("Open sheet"));
  expect(await screen.findByTestId("sheet-editor")).toBeInTheDocument();
});

test("invalid: error hints listed", async () => {
  (api.getSheet as any).mockResolvedValue({
    sheet: { ...SHEET, errors: ["unknown sheet type 'medium'"] } });
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  expect(await screen.findByText(/unknown sheet type/)).toBeInTheDocument();
  expect(screen.getByText("Open sheet")).toBeInTheDocument();          // repair path stays open
});

test("fetch failure shows an error hint instead of throwing", async () => {
  (api.getSheet as any).mockRejectedValue({ detail: "boom" });
  render(<SheetPanel scope={CAMP} module={MOD} kind="characters" eid="mara" />);
  expect(await screen.findByText("boom")).toBeInTheDocument();
});
