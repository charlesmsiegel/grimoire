import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import { ContentSection } from "./ModuleContentEditor";

vi.mock("../api/client", () => ({
  api: {
    readModule: vi.fn(), putModuleManifest: vi.fn(),
    putModuleGroup: vi.fn(), deleteModuleGroup: vi.fn(),
    putModuleSheetType: vi.fn(), deleteModuleSheetType: vi.fn(),
    putModuleCheck: vi.fn(), deleteModuleCheck: vi.fn(),
    putModuleCheckDefaults: vi.fn(),
    putModuleRule: vi.fn(), deleteModuleRule: vi.fn(), readModuleRule: vi.fn(),
    readModuleContent: vi.fn(), putModuleContent: vi.fn(), deleteModuleContent: vi.fn(),
    putModuleLayout: vi.fn(), putModuleTheme: vi.fn(),
    renameModulePart: vi.fn(), listEntities: vi.fn(),
  },
  ApiError: class extends Error {},
}));

const PACK: any = {
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System" },
  sheets: {
    groups: {},
    sheet_types: { relic: { label: "Relic", kind: "items", groups: [],
      fields: [{ key: "power", type: "dots", max: 5 }] } },
  },
  checks: {}, rules: [],
  content: [{ kind: "items", id: "sunblade", name: "Sunblade", sheet_type: "relic" }],
  errors: [], layout: { sheet_types: {} }, theme: {}, display_errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

test("content row loads the body read-only, Edit reveals the form", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "sunblade", sheet_type: "relic", fields: { power: 3 } });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  expect(await screen.findByText("A blade of dawn.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("A blade of dawn.")).toBeInTheDocument();
  expect(screen.getByLabelText("power")).toHaveValue(3);
});

test("saving posts body + stat block", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "b", keys: "",
    sheet_type: "relic", fields: { power: 3 } });
  (api.putModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("power"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleContent).toHaveBeenCalledWith(
    "realm-system", "items", "sunblade",
    expect.objectContaining({ sheet: { sheet_type: "relic", fields: { power: 4 } } }),
    false));
});

test("+ New content offers the kind select and posts a fresh entry", async () => {
  (api.putModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "+ New content" }));
  fireEvent.change(screen.getByLabelText("Content id"), { target: { value: "moonshard" } });
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "lore" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Moonshard" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleContent).toHaveBeenCalledWith(
    "realm-system", "lore", "moonshard",
    expect.objectContaining({ name: "Moonshard", sheet: null }), false));
});

test("custom frontmatter metadata round-trips through save instead of being dropped", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "", sheet_type: null, fields: {}, rarity: "legendary" });
  (api.putModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("legendary")).toBeInTheDocument();
  fireEvent.change(screen.getByDisplayValue("A blade of dawn."), { target: { value: "A blade of dusk." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleContent).toHaveBeenCalledWith(
    "realm-system", "items", "sunblade",
    expect.objectContaining({ fields: { rarity: "legendary" } }), false));
});

test("deleting the selected content clears the selection instead of leaving a ghost view", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "", sheet_type: null, fields: {} });
  (api.deleteModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<ContentSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("Sunblade"));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteModuleContent).toHaveBeenCalledWith(
    "realm-system", "items", "sunblade", false));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(screen.queryByText("A blade of dawn.")).not.toBeInTheDocument();
});

test("a successful content-id rename resyncs the still-open form to the new id", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "", sheet_type: null, fields: {} });
  (api.renameModulePart as any).mockResolvedValue({ ok: true, errors: [], display_errors: [],
    impact: { sheet_types: [], sheets_migrated: 0, sheets_newly_invalid: 0, dangling_refs: 0 } });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<ContentSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("Sunblade"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Rename…" }));
  fireEvent.change(screen.getByLabelText("New key"), { target: { value: "dawnblade" } });
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  await waitFor(() => expect(api.renameModulePart).toHaveBeenCalledWith(
    "realm-system", "content", { from: "sunblade", kind: "items" }, "dawnblade", false));
  expect(reload).toHaveBeenCalled();
  await waitFor(() => expect(screen.getByText("dawnblade")).toBeInTheDocument());
});

test("a failed content delete's guard banner clears when another row is selected", async () => {
  const pack = { ...PACK, content: [...PACK.content,
    { kind: "lore", id: "moonshard", name: "Moonshard", sheet_type: null }] };
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "", sheet_type: null, fields: {} });
  (api.deleteModuleContent as any).mockResolvedValue(
    { ok: false, errors: ["content 'sunblade' has dangling refs — resolve first"], display_errors: [] });
  render(<ContentSection pack={pack} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  await screen.findByRole("button", { name: "Edit" });
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/dangling refs/)).toBeInTheDocument();
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "moonshard", name: "Moonshard", body: "A sliver of night.",
    keys: "", sheet_type: null, fields: {} });
  fireEvent.click(screen.getByText("Moonshard"));
  await screen.findByText("A sliver of night.");
  expect(screen.queryByText(/dangling refs/)).not.toBeInTheDocument();
});
