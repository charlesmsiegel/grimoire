import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SheetEditor, { typeKind } from "./SheetEditor";
import type { ModuleDetail, Sheet } from "../api/client";

vi.mock("../api/client", () => ({
  api: { putSheet: vi.fn(), deleteSheet: vi.fn() },
}));
import { api } from "../api/client";

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
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.putSheet as any).mockResolvedValue({ ok: true });
  (api.deleteSheet as any).mockResolvedValue({ ok: true });
});

test("typeKind maps pcs to characters and passes through others", () => {
  expect(typeKind("pcs")).toBe("characters");
  expect(typeKind("characters")).toBe("characters");
  expect(typeKind("items")).toBe("items");
});

test("view shows groups and derived; edit saves fields", async () => {
  const onSaved = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={onSaved} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument();
  expect(screen.getByText(/sight_pool/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("Edit"));
  const vigor = screen.getByLabelText("Vigor") as HTMLInputElement;
  fireEvent.change(vigor, { target: { value: "4" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "medium", fields: expect.objectContaining({ vigor: 4 }) }));
  expect(onSaved).toHaveBeenCalled();
});

test("change type confirms and filters orphans", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onSaved = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "shifter" } });
  await waitFor(() => expect(api.putSheet).toHaveBeenCalled());
  const call = (api.putSheet as any).mock.calls[0];
  expect(call[0]).toEqual({ kind: "campaign", id: "run" });
  expect(call[1]).toBe("pool-basic");
  expect(call[2]).toBe("characters");
  expect(call[3]).toBe("mara");
  const body = call[4];
  expect(body.sheet_type).toBe("shifter");
  expect(body.fields).not.toHaveProperty("essence");
  expect(body.fields).toHaveProperty("vigor", 3);
  expect(onSaved).toHaveBeenCalled();
});

test("change type is a no-op if the user declines the confirm", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "shifter" } });
  await new Promise((r) => setTimeout(r, 0));
  expect(api.putSheet).not.toHaveBeenCalled();
});

test("sheet errors render in a banner", () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara"
                      initial={{ ...SHEET, errors: ["unknown sheet type 'ghost'"] }}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.getByText(/unknown sheet type/)).toHaveClass("banner");
});

test("delete sheet confirms then calls deleteSheet and closes", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={onClose} onSaved={onSaved} />);
  fireEvent.click(screen.getByText("Delete sheet"));
  await waitFor(() => expect(api.deleteSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara"));
  expect(onClose).toHaveBeenCalled();
});

test("close calls onClose without saving", () => {
  const onClose = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={onClose} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Close"));
  expect(onClose).toHaveBeenCalled();
  expect(api.putSheet).not.toHaveBeenCalled();
});

test("cancel discards edits and returns to view", () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.change(screen.getByLabelText("Vigor"), { target: { value: "1" } });
  fireEvent.click(screen.getByText("Cancel"));
  expect(screen.queryByLabelText("Vigor")).toBeNull();
  expect(screen.getByText("Attributes")).toBeInTheDocument();
});

test("text and list widgets round-trip through save", async () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.change(screen.getByLabelText("Quirk"), { target: { value: "Hums in the dark" } });
  fireEvent.change(screen.getByLabelText("Gear"), { target: { value: "lantern\nrope" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "medium", fields: expect.objectContaining({
      quirk: "Hums in the dark", gear: ["lantern", "rope"] }) }));
});

test("number widget carries schema min/max bounds", async () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  const strength = screen.getByLabelText("Strength") as HTMLInputElement;
  expect(strength.min).toBe("1");
  expect(strength.max).toBe("20");
});
