import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import { GroupsSection, SheetTypesSection } from "./ModuleSchemaEditor";

vi.mock("../api/client", () => ({
  api: {
    readModule: vi.fn(), putModuleManifest: vi.fn(),
    putModuleGroup: vi.fn(), deleteModuleGroup: vi.fn(),
    putModuleSheetType: vi.fn(), deleteModuleSheetType: vi.fn(),
    putModuleCheck: vi.fn(), deleteModuleCheck: vi.fn(),
    putModuleCheckDefaults: vi.fn(),
    putModuleRule: vi.fn(), deleteModuleRule: vi.fn(),
    putModuleContent: vi.fn(), deleteModuleContent: vi.fn(),
    putModuleLayout: vi.fn(), putModuleTheme: vi.fn(),
    renameModulePart: vi.fn(), listEntities: vi.fn(),
  },
  ApiError: class extends Error {},
}));

const PACK: any = {
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System" },
  sheets: {
    groups: { attributes: { label: "Attributes",
      fields: [{ key: "strength", label: "Strength", type: "dots", max: 5 }],
      derived: { might: "strength * 2" } } },
    sheet_types: { warden: { label: "Warden", kind: "characters",
      groups: ["attributes"], fields: [] } },
  },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

test("group row opens read-only view; Edit reveals the form", async () => {
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  expect(screen.getByText("Strength")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("Strength")).not.toBeInTheDocument(); // no inputs
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByDisplayValue("Strength")).toBeInTheDocument();
});

test("saving a group posts the assembled def", async () => {
  (api.putModuleGroup as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByDisplayValue("Strength"), { target: { value: "Brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleGroup).toHaveBeenCalledWith(
    "realm-system", "attributes",
    expect.objectContaining({
      fields: [expect.objectContaining({ key: "strength", label: "Brawn" })] }),
    false));
});

test("+ New group opens the form directly with an id input", () => {
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "+ New group" }));
  expect(screen.getByLabelText("Group id")).toBeInTheDocument();
});

test("rename affordance dry-runs, then commits when impact is clean", async () => {
  (api.renameModulePart as any).mockResolvedValue({ ok: true, errors: [], display_errors: [],
    impact: { sheet_types: [], sheets_migrated: 0, sheets_newly_invalid: 0, dangling_refs: 0 } });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<GroupsSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Rename…" })[0]);
  fireEvent.change(screen.getByLabelText("New key"), { target: { value: "brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  await waitFor(() => expect(api.renameModulePart).toHaveBeenCalledWith(
    "realm-system", "field", { from: "strength", group: "attributes" }, "brawn", true));
  await waitFor(() => expect(api.renameModulePart).toHaveBeenCalledWith(
    "realm-system", "field", { from: "strength", group: "attributes" }, "brawn", false));
  expect(reload).toHaveBeenCalled();
  // dirty form blocks the affordance entirely
  fireEvent.change(screen.getByDisplayValue("Strength"), { target: { value: "X" } });
  expect(screen.getAllByRole("button", { name: "Rename…" })[0]).toBeDisabled();
});

test("impactful rename shows the confirm; Cancel sends no real call", async () => {
  (api.renameModulePart as any).mockResolvedValue({ ok: true, errors: [], display_errors: [],
    impact: { sheet_types: ["warden"], sheets_migrated: 3, sheets_newly_invalid: 0, dangling_refs: 0 } });
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Rename…" })[0]);
  fireEvent.change(screen.getByLabelText("New key"), { target: { value: "brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  expect(await screen.findByText(/migrates 3 sheets/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  const real = (api.renameModulePart as any).mock.calls.filter((c: any[]) => c[4] === false);
  expect(real).toHaveLength(0);
});

test("sheet-type form drives group membership and advancement pool options", async () => {
  (api.putModuleSheetType as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<SheetTypesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Warden"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByRole("checkbox", { name: "Attributes" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleSheetType).toHaveBeenCalledWith(
    "realm-system", "warden",
    expect.objectContaining({ kind: "characters", groups: ["attributes"] }),
    false));
});
