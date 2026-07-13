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
  fireEvent.click(screen.getByLabelText("Vigor 4"));
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
  fireEvent.click(screen.getByLabelText("Vigor 1"));
  fireEvent.click(screen.getByText("Cancel"));
  expect(screen.queryByLabelText("Vigor 1")).toBeNull();
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

test("list textarea preserves newlines while typing", () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  const gear = screen.getByLabelText("Gear") as HTMLTextAreaElement;
  fireEvent.change(gear, { target: { value: "lantern\n" } });
  expect(gear.value).toBe("lantern\n");
  fireEvent.change(gear, { target: { value: "lantern\nrope" } });
  expect(gear.value).toBe("lantern\nrope");
});

test("empty list textarea saves an empty array, not a blank line", async () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.change(screen.getByLabelText("Gear"), { target: { value: "" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "medium", fields: expect.objectContaining({ gear: [] }) }));
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

test("layout applies in view and edit; same panels both modes", () => {
  const laid: ModuleDetail = { ...MOD, layout: { sheet_types: {
    medium: { column: [{ group: "attributes", title: "Attributes" },
                       { fields: ["essence", "quirk", "gear"], title: "Power" }] } } } };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={laid}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.getByText("Power")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Edit"));
  expect(screen.getByText("Power")).toBeInTheDocument(); // same arrangement in edit
});

test("theme sets vars and data attributes on the takeover", () => {
  const themed: ModuleDetail = { ...MOD,
    theme: { colors: { bg: "#191521", ink: "#d8d2c4" }, dots: "diamond", corners: "sharp" } };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={themed}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  const takeover = screen.getByRole("dialog");
  expect(takeover.getAttribute("data-dots")).toBe("diamond");
  expect(takeover.style.getPropertyValue("--sheet-bg")).toBe("#191521");
});

test("unthemed module sets no sheet vars", () => {
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  const takeover = screen.getByRole("dialog");
  expect(takeover.style.getPropertyValue("--sheet-bg")).toBe("");
  expect(takeover.getAttribute("data-dots")).toBeNull();
  expect(takeover.getAttribute("data-corners")).toBeNull();
});

test("dropped-layout hint routing", () => {
  const base = { scope: { kind: "campaign", id: "run" } as const, kind: "characters",
                 eid: "mara", initial: SHEET, onClose: () => {}, onSaved: () => {} };
  const HINT = /layout for this sheet type is invalid/;
  // names the current type -> fires
  const dropped: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: "medium", message: "sheet_types.medium: bad" }] };
  const { unmount } = render(<SheetEditor {...base} module={dropped} />);
  expect(screen.getByText(HINT)).toBeInTheDocument();
  unmount();
  // file-level failure (sentinel "*"), no surviving tree -> fires
  const global: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: "*", message: "layout.json: must be an object" }] };
  const r2 = render(<SheetEditor {...base} module={global} />);
  expect(screen.getByText(HINT)).toBeInTheDocument();
  r2.unmount();
  // unused-broken-fragment error (sheet_type null, drops nothing), zero surviving trees -> does NOT fire
  const unusedFragment: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: null, message: "fragments.broken: bad" }] };
  const r2b = render(<SheetEditor {...base} module={unusedFragment} />);
  expect(r2b.queryByText(HINT)).toBeNull();
  r2b.unmount();
  // unused-fragment error but current type's layout survived -> does NOT fire
  const survived: ModuleDetail = { ...MOD,
    layout: { sheet_types: { medium: { column: [] } } },
    display_errors: [{ source: "layout", sheet_type: null, message: "fragments.broken: bad" }] };
  const r3 = render(<SheetEditor {...base} module={survived} />);
  expect(r3.queryByText(HINT)).toBeNull();
  r3.unmount();
  // another type's tree dropped, current type never had a layout -> does NOT fire
  const other: ModuleDetail = { ...MOD, display_errors: [
    { source: "layout", sheet_type: "shifter", message: "sheet_types.shifter: bad" }] };
  const r4 = render(<SheetEditor {...base} module={other} />);
  expect(r4.queryByText(HINT)).toBeNull();
  r4.unmount();
  // null-sheet_type error but another type's tree survived, current type never had a layout -> does NOT fire
  const otherSurvived: ModuleDetail = { ...MOD,
    layout: { sheet_types: { shifter: { column: [] } } },
    display_errors: [{ source: "layout", sheet_type: null, message: "fragments.broken: bad" }] };
  const r5 = render(<SheetEditor {...base} module={otherSurvived} />);
  expect(r5.queryByText(HINT)).toBeNull();
});
