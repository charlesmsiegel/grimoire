import { act, render, fireEvent, waitFor, within, screen } from "@testing-library/react";

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
import { MemoryRouter } from "react-router-dom";
import { fixture } from "../testkit/fixture";
import type { ModuleDetail, ModuleSummary } from "../api/types";

// The page lives in the library's shell now — a context column of sections
// beside main — and every row in that column is a <NavLink>.
const inShell = (el: React.ReactElement) => <MemoryRouter>{el}</MemoryRouter>;

// minimal valid ModuleDetail — extend per-test with spreads
const DETAIL: ModuleDetail = {
  id: "mine",
  source: "user",
  manifest: { id: "mine", name: "Mine", description: "", version: "0.1", dice: "1d20" },
  sheets: { groups: {}, sheet_types: {} },
  checks: {},
  rules: [],
  content: [],
  errors: [],
};

const POOL: ModuleDetail = {
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
  vi.mocked(api.listModules).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", source: "builtin", valid: true },
  ]);
  vi.mocked(api.readModule).mockResolvedValue(POOL);
});

test("clicking a row shows the read-only module detail", async () => {
  const { container } = render(inShell(<ModulesView />));
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
  // A broken pack is served as it is on disk, which the type does not
  // describe: `broken` is a sheet type that is not an object at all.
  const sheetTypes: Record<string, unknown> = {
    broken: "oops",
    medium: { label: "Medium", kind: "characters", groups: ["ghost-group"], fields: [], derived: {} },
  };
  const BROKEN = fixture<ModuleDetail>({
    ...POOL,
    sheets: {
      groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
      sheet_types: sheetTypes as ModuleDetail["sheets"]["sheet_types"],
    },
    errors: ["sheet_types.broken: must be an object", "sheet_types.medium: unknown group ref 'ghost-group'"],
  });
  vi.mocked(api.readModule).mockResolvedValue(BROKEN);
  const { container } = render(inShell(<ModulesView />));
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
  vi.mocked(api.listModules).mockResolvedValue([
    { id: "pool-basic", name: "Pool Basic", description: "", version: "1",
      source: "builtin", valid: true, display_ok: false },
  ]);
  vi.mocked(api.readModule).mockResolvedValue({
    ...POOL,
    layout: { sheet_types: { medium: { column: [] } } },
    theme: { dots: "diamond" },
    display_errors: [{ source: "layout", sheet_type: "haven", message: "sheet_types.haven: bad" }],
  });
  render(inShell(<ModulesView />));
  expect(await screen.findByText(/display issues/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Pool Basic"));
  expect(await screen.findByText("Display")).toBeInTheDocument();
  expect(screen.getByText("medium layout")).toBeInTheDocument();
  expect(screen.getByText("theme")).toBeInTheDocument();
  expect(screen.getByText("sheet_types.haven: bad")).toBeInTheDocument();
});

test("user module shows Edit; builtin shows duplicate hint", async () => {
  vi.mocked(api.listModules).mockResolvedValue(fixture([
    { id: "mine", name: "Mine", source: "user", valid: true },
    { id: "d20-basic", name: "Basic D20", source: "builtin", valid: true },
  ]));
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "mine", source: "user" });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByText("Mine"));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "d20-basic", source: "builtin" });
  fireEvent.click(screen.getByText("Basic D20"));
  await waitFor(() =>
    expect(screen.getByText(/duplicate to customize/)).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

test("Duplicate prompts for a name and selects the copy", async () => {
  vi.mocked(api.listModules).mockResolvedValue(fixture([
    { id: "d20-basic", name: "Basic D20", source: "builtin", valid: true },
  ]));
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "d20-basic", source: "builtin" });
  vi.mocked(api.duplicateModule).mockResolvedValue({ id: "basic-d20-copy" });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByText("Basic D20"));
  await waitFor(() =>
    expect(screen.getByText(/duplicate to customize/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
  fireEvent.click(screen.getByRole("button", { name: "Create copy" }));
  await waitFor(() => expect(api.duplicateModule).toHaveBeenCalledWith(
    "d20-basic", "Basic D20 copy"));
});

test("Import posts the picked file, reloads the list, and selects the imported module", async () => {
  vi.mocked(api.importModule).mockResolvedValue({ id: "imported" });
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "imported", source: "user" });
  render(inShell(<ModulesView />));
  const input = screen.getByLabelText("Import module zip");
  const file = new File(["zip"], "pack.zip", { type: "application/zip" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(api.importModule).toHaveBeenCalled());
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("imported"));
  expect(await screen.findByText("Mine")).toBeInTheDocument(); // DETAIL's manifest name, rendered
});

test("Edit mounts the module editor", async () => {
  vi.mocked(api.listModules).mockResolvedValue(fixture([
    { id: "mine", name: "Mine", source: "user", valid: true },
  ]));
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "mine", source: "user" });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByText("Mine"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByText("Manifest")).toBeInTheDocument(); // section nav
});

test("New is an addressed cancellable name form and keeps a refused draft", async () => {
  vi.mocked(api.createModule).mockRejectedValue({ detail: "A module already uses this name." });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "+ New module" }));
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create module" }));
  expect(await screen.findByText("A module already uses this name.")).toBeInTheDocument();
  expect(screen.getByLabelText("Module name")).toHaveValue("Realm");
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  expect(await screen.findByText(/Select a module/)).toBeInTheDocument();
});

test.each(["success", "refusal"])("a cancelled module creation cannot reclaim the page: %s", async (outcome) => {
  let finish: (() => void) | undefined;
  vi.mocked(api.createModule).mockImplementation(() => new Promise((resolve, reject) => {
    finish = () => outcome === "success" ? resolve({ id: "realm" }) : reject(new Error("creation refused"));
  }));
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "+ New module" }));
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create module" }));
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  if (!finish) throw new Error("creation never started");
  await act(async () => { finish?.(); });
  expect(screen.getByText(/Select a module/)).toBeInTheDocument();
  expect(screen.queryByText(/creation refused/)).not.toBeInTheDocument();
});

test.each([false, true])("only the exact defaults check is hidden: defaults-only=%s", async (defaultsOnly) => {
  vi.mocked(api.readModule).mockResolvedValue({ ...POOL, checks: {
    _defaults: { label: "Internal defaults", roll: "", requires: [] },
    ...(defaultsOnly ? {} : { _attack: { label: "Attack", roll: "1d20", requires: [] } }),
  } });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByText("Basic Pool"));
  await screen.findByRole("heading", { name: "Basic Pool" });
  expect(screen.queryByText("Internal defaults")).not.toBeInTheDocument();
  if (defaultsOnly) expect(screen.queryByRole("heading", { name: "Checks" })).not.toBeInTheDocument();
  else expect(screen.getByText("Attack")).toBeInTheDocument();
});

test.each([false, true])("a cancelled creation's list refresh keeps its scope: refusal=%s", async (refused) => {
  let finishRefresh: (() => void) | undefined;
  vi.mocked(api.createModule).mockResolvedValue({ id: "realm" });
  vi.mocked(api.listModules).mockResolvedValue([]);
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "+ New module" }));
  vi.mocked(api.listModules).mockImplementation(() => new Promise((resolve, reject) => {
    finishRefresh = () => refused ? reject(new Error("refresh refused"))
      : resolve([{ id: "realm", name: "Realm", description: "", version: "0.1", source: "user", valid: true }]);
  }));
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create module" }));
  await waitFor(() => expect(finishRefresh).toBeDefined());
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  if (!finishRefresh) throw new Error("refresh never started");
  await act(async () => { finishRefresh?.(); });
  expect(screen.getByText(/Select a module/)).toBeInTheDocument();
  expect(screen.queryByText(/refresh refused/)).not.toBeInTheDocument();
  if (!refused) expect(screen.getByRole("link", { name: "Realm" })).toBeInTheDocument();
});

test("an older creation refresh cannot hide a module from the newer refresh", async () => {
  const realm: ModuleSummary = {
    id: "realm", name: "Realm", description: "", version: "0.1", source: "user", valid: true,
  };
  const saltmarch: ModuleSummary = { ...realm, id: "saltmarch", name: "Saltmarch" };
  let finishOlder: (() => void) | undefined;
  vi.mocked(api.listModules).mockResolvedValue([]);
  vi.mocked(api.createModule).mockResolvedValueOnce({ id: "realm" }).mockResolvedValueOnce({ id: "saltmarch" });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "+ New module" }));
  const initialReads = vi.mocked(api.listModules).mock.calls.length;
  vi.mocked(api.listModules).mockImplementationOnce(() => new Promise((resolve) => {
    finishOlder = () => resolve([realm]);
  })).mockResolvedValueOnce([realm, saltmarch]);
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create module" }));
  await waitFor(() => expect(finishOlder).toBeDefined());
  expect(api.listModules).toHaveBeenCalledTimes(initialReads + 1);
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: "+ New module" }));
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: "Create module" }));
  await screen.findByRole("link", { name: "Saltmarch" });
  await waitFor(() => expect(api.readModule).toHaveBeenLastCalledWith("saltmarch"));
  if (!finishOlder) throw new Error("the older refresh never started");
  await act(async () => { finishOlder?.(); });
  expect(screen.getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
  expect(api.readModule).toHaveBeenLastCalledWith("saltmarch");
});

test("a module create in flight cannot be submitted twice", async () => {
  vi.mocked(api.createModule).mockReturnValue(new Promise(() => {}));
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "+ New module" }));
  fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
  const create = screen.getByRole("button", { name: "Create module" });
  fireEvent.click(create);
  fireEvent.click(create);
  expect(api.createModule).toHaveBeenCalledTimes(1);
  expect(create).toBeDisabled();
});

test("a bookmarked module reads its record and hides internal check defaults", async () => {
  vi.mocked(api.readModule).mockResolvedValue({
    ...POOL, source: "builtin", checks: { ...POOL.checks, _defaults: { label: "Internal defaults", roll: "", requires: [] } },
  });
  render(<MemoryRouter initialEntries={["/modules?id=pool-basic"]}><ModulesView /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Basic Pool" })).toBeInTheDocument();
  expect(screen.getByText("Vigor + Brawl")).toBeInTheDocument();
  expect(screen.queryByText("Internal defaults")).not.toBeInTheDocument();
});


test("Done cannot install an earlier module over a newer addressed selection", async () => {
  vi.mocked(api.listModules).mockResolvedValue(fixture([
    { id: "mine", name: "Realm", source: "user", valid: true },
    { id: "pool-basic", name: "Saltmarch", source: "builtin", valid: true },
  ]));
  vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, manifest: { ...DETAIL.manifest, name: "Realm" } });
  render(inShell(<ModulesView />));
  fireEvent.click(await screen.findByRole("link", { name: "Realm" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  let release: (v: Awaited<ReturnType<typeof api.readModule>>) => void = () => {};
  vi.mocked(api.readModule).mockReturnValueOnce(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  vi.mocked(api.readModule).mockResolvedValue({ ...POOL, manifest: { ...POOL.manifest, name: "Saltmarch" } });
  fireEvent.click(screen.getByRole("link", { name: "Saltmarch" }));
  await screen.findByRole("heading", { name: "Saltmarch" });
  await act(async () => release({ ...DETAIL, manifest: { ...DETAIL.manifest, name: "Realm stale" } }));
  expect(screen.getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Realm stale" })).toBeNull();
});

test.each(["create", "import", "duplicate"])(
  "review regression: a committed module %s preserves refresh failure and retries only the list", async (operation) => {
    vi.mocked(api.createModule).mockResolvedValue({ id: "realm" });
    vi.mocked(api.importModule).mockResolvedValue({ id: "realm" });
    vi.mocked(api.duplicateModule).mockResolvedValue({ id: "realm" });
    render(inShell(<ModulesView />));
    await screen.findByRole("link", { name: "Basic Pool" });
    if (operation === "duplicate") {
      fireEvent.click(screen.getByRole("link", { name: "Basic Pool" }));
      fireEvent.click(await screen.findByRole("button", { name: "Duplicate" }));
    }
    vi.mocked(api.listModules).mockRejectedValueOnce(new Error("Module refresh refused"));
    vi.mocked(api.readModule).mockResolvedValue({ ...DETAIL, id: "realm", manifest: { ...DETAIL.manifest, name: "Realm" } });
    if (operation === "create") {
      fireEvent.click(screen.getByRole("link", { name: "+ New module" }));
      fireEvent.change(screen.getByLabelText("Module name"), { target: { value: "Realm" } });
      fireEvent.click(screen.getByRole("button", { name: "Create module" }));
    } else if (operation === "import") {
      fireEvent.change(screen.getByLabelText("Import module zip"), {
        target: { files: [new File(["zip"], "realm.zip", { type: "application/zip" })] },
      });
    } else fireEvent.click(screen.getByRole("button", { name: "Create copy" }));
    await screen.findByRole("heading", { name: "Realm" });
    expect(screen.getByText(/Module refresh refused/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create module" })).not.toBeInTheDocument();
    vi.mocked(api.listModules).mockResolvedValue([{
      id: "realm", name: "Realm", description: "", version: "0.1", source: "user", valid: true,
    }]);
    fireEvent.click(screen.getByRole("button", { name: "Retry module list" }));
    await screen.findByRole("link", { name: "Realm" });
    expect(screen.queryByText(/Module refresh refused/)).not.toBeInTheDocument();
    expect(api.createModule).toHaveBeenCalledTimes(operation === "create" ? 1 : 0);
    expect(api.importModule).toHaveBeenCalledTimes(operation === "import" ? 1 : 0);
    expect(api.duplicateModule).toHaveBeenCalledTimes(operation === "duplicate" ? 1 : 0);
  });
