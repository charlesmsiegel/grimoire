import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import ModuleEditor from "./ModuleEditor";

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

const DETAIL: any = {
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System", version: "1", notes: "n" },
  sheets: { groups: {}, sheet_types: {} },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
};
const OK = { ok: true, errors: [], display_errors: [] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  (api.readModule as any).mockResolvedValue(DETAIL);
});

test("manifest section saves and reloads", async () => {
  (api.putModuleManifest as any).mockResolvedValue(OK);
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Manifest" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm 2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ name: "Realm 2", dry_run: false })));
  await waitFor(() => expect(api.readModule).toHaveBeenCalled());
});

test("debounced dry-run renders errors inline", async () => {
  (api.putModuleManifest as any).mockResolvedValue(
    { ok: false, errors: ["module.md: manifest requires a name"], display_errors: [] });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "" } });
  await vi.advanceTimersByTimeAsync(600);
  expect(await screen.findByText(/requires a name/)).toBeInTheDocument();
  expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ dry_run: true }));
});

test("impact confirm gates the save and Cancel aborts", async () => {
  (api.putModuleManifest as any).mockResolvedValue(
    { ...OK, impact: { sheet_types: ["warden"], sheets_migrated: 2,
                       sheets_newly_invalid: 1, dangling_refs: 0 } });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
  await vi.advanceTimersByTimeAsync(600);          // dry-run stores the impact
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/migrates 2 sheets/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  // only the dry_run call happened
  const real = (api.putModuleManifest as any).mock.calls
    .filter((c: any[]) => c[1].dry_run === false);
  expect(real).toHaveLength(0);
});
