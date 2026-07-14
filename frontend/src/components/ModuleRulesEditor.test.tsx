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

test("Save is absent while the rule body read is pending (P1-2)", async () => {
  let resolveRead: (v: { meta: Record<string, unknown>; body: string }) => void = () => {};
  (api.readModuleRule as any).mockReturnValue(new Promise((res) => { resolveRead = res; }));
  render(<RulesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("combat"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  // still in flight: no Save button, no body textarea to silently blank out
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Body")).not.toBeInTheDocument();
  expect(screen.getByText("Loading rule body…")).toBeInTheDocument();
  resolveRead({ meta: {}, body: "Swing first." });
  expect(await screen.findByRole("button", { name: "Save" })).toBeInTheDocument();
  expect(screen.getByLabelText("Body")).toHaveValue("Swing first.");
});

test("a failed rule body fetch shows an error and keeps Save blocked (P1-2)", async () => {
  (api.readModuleRule as any).mockRejectedValue(new Error("network down"));
  render(<RulesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("combat"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByText("network down")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Body")).not.toBeInTheDocument();
  expect(api.putModuleRule).not.toHaveBeenCalled();
});

test("a failed check delete's guard banner clears when another row is selected", async () => {
  (api.deleteModuleCheck as any).mockResolvedValue(
    { ok: false, errors: ["check 'brawl' has a live roll proposal in campaign 'c1', scene 's1' — resolve or discard it first"], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Brawl"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/live roll proposal/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Defaults"));
  expect(screen.queryByText(/live roll proposal/)).not.toBeInTheDocument();
});

test("a failed rule delete's guard banner clears when another row is selected", async () => {
  (api.deleteModuleRule as any).mockResolvedValue(
    { ok: false, errors: ["rule 'combat' is referenced elsewhere — resolve first"], display_errors: [] });
  const pack = { ...PACK, rules: [...PACK.rules, { id: "stealth", keys: [], always: false, on_roll: false, sheet_types: [] }] };
  render(<RulesSection pack={pack} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("combat"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/referenced elsewhere/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("stealth"));
  expect(screen.queryByText(/referenced elsewhere/)).not.toBeInTheDocument();
});

test("deleting the selected check clears the selection instead of leaving a ghost view", async () => {
  (api.deleteModuleCheck as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<ChecksSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("Brawl"));
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteModuleCheck).toHaveBeenCalledWith(
    "realm-system", "brawl", false));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(screen.queryByText("1d20 + {might}")).not.toBeInTheDocument();
});

test("deleting the selected rule clears the selection instead of leaving a ghost view", async () => {
  (api.deleteModuleRule as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<RulesSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("combat"));
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteModuleRule).toHaveBeenCalledWith(
    "realm-system", "combat", false));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

test("Defaults pseudo-row round-trips difficulty through putModuleCheckDefaults", async () => {
  (api.putModuleCheckDefaults as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Defaults"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleCheckDefaults).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ difficulty: 12 }), false));
});

test("Defaults pseudo-row omits difficulty when cleared", async () => {
  const pack = { ...PACK, checks: { ...PACK.checks, _defaults: { difficulty: 10 } } };
  (api.putModuleCheckDefaults as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ChecksSection pack={pack} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Defaults"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Difficulty")).toHaveValue(10);
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleCheckDefaults).toHaveBeenCalledWith(
    "realm-system", {}, false));
});
