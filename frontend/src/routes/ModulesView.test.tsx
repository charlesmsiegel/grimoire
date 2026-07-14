import { render, fireEvent, waitFor, within, screen } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    listModules: vi.fn(),
    readModule: vi.fn(),
    createModule: vi.fn(),
    duplicateModule: vi.fn(),
    importModule: vi.fn(),
    exportModuleUrl: (mid: string) => `/api/modules/${mid}/export`,
  },
}));
import { api } from "../api/client";
import ModulesView from "./ModulesView";

// minimal valid ModuleDetail — extend per-test with spreads
const DETAIL = {
  id: "mine",
  source: "user",
  manifest: { id: "mine", name: "Mine", description: "", version: "0.1", dice: "1d20" },
  sheets: { groups: {}, sheet_types: {} },
  checks: {},
  rules: [],
  content: [],
  errors: [],
};

const POOL = {
  id: "pool-basic",
  source: "builtin",
  manifest: { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", dice: "5d10 t6" },
  sheets: {
    groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: ["attributes"], fields: [], derived: {} },
      talisman: { label: "Talisman", kind: "items", groups: [], fields: [{ key: "power", label: "Power", type: "dots", max: 5 }], derived: {} },
    },
  },
  checks: { brawl: { label: "Vigor + Brawl", roll: "{vigor}d10 t6", requires: ["attributes"] } },
  rules: [{ id: "core", keys: [], always: true, on_roll: false, sheet_types: [] }],
  content: [],
  errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue(POOL);
});

test("clicking a row shows the read-only module detail", async () => {
  const { container } = render(<ModulesView />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Basic Pool"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  const detail = await waitFor(() => container.querySelector(".detail-view") as HTMLElement);
  expect(within(detail).getByText("d10 pools.")).toBeInTheDocument();
  expect(within(detail).getByText("Medium")).toBeInTheDocument();
  expect(within(detail).getByText("Talisman")).toBeInTheDocument();
  expect(within(detail).getByText("Vigor + Brawl")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();   // read-only
  expect(within(detail).queryByText("Edit")).toBeNull();    // no edit affordance
});

test("renders valid sheet types and the Problems section without throwing on a broken pack", async () => {
  const BROKEN = {
    ...POOL,
    sheets: {
      groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
      sheet_types: {
        broken: "oops",
        medium: { label: "Medium", kind: "characters", groups: ["ghost-group"], fields: [], derived: {} },
      },
    },
    errors: ["sheet_types.broken: must be an object", "sheet_types.medium: unknown group ref 'ghost-group'"],
  };
  (api.readModule as any).mockResolvedValue(BROKEN);
  const { container } = render(<ModulesView />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Basic Pool"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  const detail = await waitFor(() => container.querySelector(".detail-view") as HTMLElement);
  expect(within(detail).getByText("Medium")).toBeInTheDocument();
  expect(within(detail).getByText("ghost-group")).toBeInTheDocument(); // falls back to raw id
  expect(within(detail).getByText("Problems")).toBeInTheDocument();
  expect(within(detail).getByText(/unknown group ref/)).toBeInTheDocument();
});

test("list row flags display issues; detail shows Display section", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Pool Basic", description: "", version: "1",
      source: "builtin", valid: true, display_ok: false },
  ]);
  (api.readModule as any).mockResolvedValue({
    ...POOL,
    layout: { sheet_types: { medium: { column: [] } } },
    theme: { dots: "diamond" },
    display_errors: [{ source: "layout", sheet_type: "haven", message: "sheet_types.haven: bad" }],
  });
  render(<ModulesView />);
  expect(await screen.findByText(/display issues/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Pool Basic"));
  expect(await screen.findByText("Display")).toBeInTheDocument();
  expect(screen.getByText("medium layout")).toBeInTheDocument();
  expect(screen.getByText("theme")).toBeInTheDocument();
  expect(screen.getByText("sheet_types.haven: bad")).toBeInTheDocument();
});

test("user module shows Edit; builtin shows duplicate hint", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "mine", name: "Mine", source: "user", valid: true },
    { id: "d20-basic", name: "Basic D20", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "mine", source: "user" });
  render(<ModulesView />);
  fireEvent.click(await screen.findByText("Mine"));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "d20-basic", source: "builtin" });
  fireEvent.click(screen.getByText("Basic D20"));
  await waitFor(() =>
    expect(screen.getByText(/duplicate to customize/)).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

test("Duplicate prompts for a name and selects the copy", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "d20-basic", name: "Basic D20", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "d20-basic", source: "builtin" });
  (api.duplicateModule as any).mockResolvedValue({ id: "basic-d20-copy" });
  render(<ModulesView />);
  fireEvent.click(await screen.findByText("Basic D20"));
  await waitFor(() =>
    expect(screen.getByText(/duplicate to customize/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
  fireEvent.click(screen.getByRole("button", { name: "Create copy" }));
  await waitFor(() => expect(api.duplicateModule).toHaveBeenCalledWith(
    "d20-basic", "Basic D20 copy"));
});

test("Import posts the picked file and reloads the list", async () => {
  (api.importModule as any).mockResolvedValue({ id: "imported" });
  render(<ModulesView />);
  const input = screen.getByLabelText("Import module zip") as HTMLInputElement;
  const file = new File(["zip"], "pack.zip", { type: "application/zip" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(api.importModule).toHaveBeenCalled());
});

test("Edit mounts the module editor", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "mine", name: "Mine", source: "user", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "mine", source: "user" });
  render(<ModulesView />);
  fireEvent.click(await screen.findByText("Mine"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByText("Manifest")).toBeInTheDocument(); // section nav
});
