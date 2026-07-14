import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import { ChecksSection, RulesSection } from "./ModuleRulesEditor";

vi.mock("../api/client", () => ({
  api: {
    readModule: vi.fn(), putModuleManifest: vi.fn(),
    putModuleGroup: vi.fn(), deleteModuleGroup: vi.fn(),
    putModuleSheetType: vi.fn(), deleteModuleSheetType: vi.fn(),
    putModuleCheck: vi.fn(), deleteModuleCheck: vi.fn(),
    putModuleCheckDefaults: vi.fn(),
    putModuleRule: vi.fn(), deleteModuleRule: vi.fn(), readModuleRule: vi.fn(),
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
  checks: { brawl: { label: "Brawl", roll: "1d20 + {might}", requires: ["attributes"] } },
  rules: [{ id: "combat", keys: ["melee"], always: true, on_roll: false, sheet_types: [] }],
  content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

test("check row → view → Edit → save round-trip", async () => {
  (api.putModuleCheck as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Brawl"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Roll"), { target: { value: "1d20" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleCheck).toHaveBeenCalledWith(
    "realm-system", "brawl",
    expect.objectContaining({ roll: "1d20", requires: ["attributes"] }), false));
});

test("blocked check delete shows the guard message", async () => {
  (api.deleteModuleCheck as any).mockResolvedValue(
    { ok: false, errors: ["check 'brawl' has a live roll proposal in campaign 'c1', scene 's1' — resolve or discard it first"], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Brawl"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/live roll proposal/)).toBeInTheDocument();
});

test("rule form loads the body and saves flags + body", async () => {
  (api.readModuleRule as any).mockResolvedValue({ meta: {}, body: "Swing first." });
  (api.putModuleRule as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<RulesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("combat"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("Swing first.")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("On roll"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleRule).toHaveBeenCalledWith(
    "realm-system", "combat",
    expect.objectContaining({ always: true, on_roll: true, keys: ["melee"] }),
    "Swing first.", false));
});
