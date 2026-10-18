import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { ModuleDetail } from "../api/types";
import { fixture } from "../testkit/fixture";
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
  ApiError: class extends Error {
    constructor(public status: number, public detail: string) { super(detail); }
  },
}));

const DETAIL = fixture<ModuleDetail>({
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System", version: "1", notes: "n" },
  sheets: { groups: {}, sheet_types: {} },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
});
const OK = { ok: true, errors: [], display_errors: [] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(api.readModule).mockResolvedValue(DETAIL);
});

test("manifest section saves and reloads", async () => {
  vi.mocked(api.putModuleManifest).mockResolvedValue(OK);
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Manifest" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm 2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ name: "Realm 2", dry_run: false })));
  await waitFor(() => expect(api.readModule).toHaveBeenCalled());
});

test("debounced dry-run renders errors inline", async () => {
  vi.mocked(api.putModuleManifest).mockResolvedValue(
    { ok: false, errors: ["module.md: manifest requires a name"], display_errors: [] });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "" } });
  await vi.advanceTimersByTimeAsync(600);
  expect(await screen.findByText(/requires a name/)).toBeInTheDocument();
  expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ dry_run: true }));
});

test("Save never commits an invalid draft", async () => {
  vi.mocked(api.putModuleManifest).mockResolvedValue(
    { ok: false, errors: ["module.md: manifest requires a name"], display_errors: [] });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/requires a name/)).toBeInTheDocument();
  // no real (non-dry-run) save was ever issued for the invalid draft
  const real = vi.mocked(api.putModuleManifest).mock.calls
    .filter((c) => c[1].dry_run === false);
  expect(real).toHaveLength(0);
});

test.each(["preview", "write"])("a refused Save %s explains the failure and keeps the draft for retry", async (stage) => {
  vi.mocked(api.putModuleManifest).mockImplementation(async (_id, data) => {
    if (stage === "preview" || !data.dry_run) throw new ApiError(409, "Module is busy. Try again.");
    return OK;
  });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm revised" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Module is busy. Try again.")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Realm revised");
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  expect(api.readModule).not.toHaveBeenCalled();

  vi.mocked(api.putModuleManifest).mockResolvedValue(OK);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.readModule).toHaveBeenCalled());
  expect(screen.queryByText("Module is busy. Try again.")).not.toBeInTheDocument();
});

test("impact confirm gates the save and Cancel aborts", async () => {
  vi.mocked(api.putModuleManifest).mockResolvedValue(
    { ...OK, impact: { sheet_types: ["warden"], sheets_migrated: 2,
                       sheets_newly_invalid: 1, dangling_refs: 0 } });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
  await vi.advanceTimersByTimeAsync(600);          // dry-run stores the impact
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/migrates 2 sheets/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  // only the dry_run call happened
  const real = vi.mocked(api.putModuleManifest).mock.calls
    .filter((c) => c[1].dry_run === false);
  expect(real).toHaveLength(0);
});
