import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SheetEditor, { typeKind } from "./SheetEditor";
import type { ModuleDetail, Sheet } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      putSheet: vi.fn(), deleteSheet: vi.fn(), listEntities: vi.fn(), readModuleContent: vi.fn(),
      instantiateContent: vi.fn(), advanceSheet: vi.fn(), getSheet: vi.fn(),
    },
  };
});
import { ApiError, api } from "../api/client";

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
  gen: "g1",
};

const REF_MOD: ModuleDetail = {
  ...MOD,
  sheets: {
    groups: {},
    sheet_types: {
      warden: {
        label: "Warden", kind: "characters", groups: [],
        fields: [{ key: "known", label: "Known Spells", type: "ref", ref_kind: "lore" }],
      },
    },
  },
};

const ADV_MOD: ModuleDetail = {
  ...MOD,
  sheets: {
    groups: {},
    sheet_types: {
      warden: {
        label: "Warden", kind: "characters", groups: [],
        fields: [
          { key: "wits", label: "Wits", type: "dots", max: 5 },
          { key: "xp", label: "XP", type: "resource", max: 999 },
        ],
        advancement: { pool: "xp", costs: { wits: "new * 3" } },
      },
    },
  },
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
    { sheet_type: "medium", fields: expect.objectContaining({ vigor: 4 }),
      expected: { sheet_type: "medium", fields: SHEET.fields, gen: "g1" } }));
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
  expect(body.expected).toEqual({ sheet_type: "medium", fields: SHEET.fields, gen: "g1" });
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
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara", "g1"));
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
      quirk: "Hums in the dark", gear: ["lantern", "rope"] }),
      expected: { sheet_type: "medium", fields: SHEET.fields, gen: "g1" } }));
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
    { sheet_type: "medium", fields: expect.objectContaining({ gear: [] }),
      expected: { sheet_type: "medium", fields: SHEET.fields, gen: "g1" } }));
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

test("view mode renders entity-form ref chips that call onOpenRef", async () => {
  const onOpenRef = vi.fn();
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:fireball"] }, derived: {}, errors: [], gen: "g2" };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} onOpenRef={onOpenRef} />);
  fireEvent.click(screen.getByText(/fireball/i));
  expect(onOpenRef).toHaveBeenCalledWith("lore", "fireball");
});

test("view mode renders module-content ref chips that open a preview instead", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "icebolt", name: "Icebolt", body: "A shard of ice.", keys: "", sheet_type: null, fields: {},
  });
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:module:icebolt"] }, derived: {}, errors: [], gen: "g2" };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText(/icebolt/i));
  await screen.findByText("A shard of ice.");
});

test("module-content ref preview's Instantiate button calls the API and closes the preview", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "icebolt", name: "Icebolt", body: "A shard of ice.", keys: "", sheet_type: null, fields: {},
  });
  (api.instantiateContent as any).mockResolvedValue({ id: "icebolt" });
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:module:icebolt"] }, derived: {}, errors: [], gen: "g2" };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText(/icebolt/i));
  await screen.findByText("A shard of ice.");
  fireEvent.click(screen.getByText("Instantiate"));
  await waitFor(() => expect(api.instantiateContent).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "lore", "pool-basic", "icebolt"));
  await waitFor(() => expect(screen.queryByText("A shard of ice.")).not.toBeInTheDocument());
});

test("module-content ref preview shows an error and stays open when Instantiate fails", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "lore", id: "icebolt", name: "Icebolt", body: "A shard of ice.", keys: "", sheet_type: null, fields: {},
  });
  (api.instantiateContent as any).mockRejectedValue({ detail: "already instantiated" });
  const initial: Sheet = { sheet_type: "warden", fields: { known: ["lore:module:icebolt"] }, derived: {}, errors: [], gen: "g2" };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText(/icebolt/i));
  await screen.findByText("A shard of ice.");
  fireEvent.click(screen.getByText("Instantiate"));
  await screen.findByText("already instantiated");
  expect(screen.getByText("A shard of ice.")).toBeInTheDocument();
});

test("edit mode offers a two-group checkbox picker for a ref field", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "fireball", name: "Fireball" }]);
  const initial: Sheet = { sheet_type: "warden", fields: { known: [] }, derived: {}, errors: [], gen: "g2" };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={REF_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  await screen.findByText("In your world/campaign");
  await screen.findByText("From Pool Basic");
  fireEvent.click(screen.getByLabelText("Fireball"));
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "warden", fields: { known: ["lore:fireball"] },
      expected: { sheet_type: "warden", fields: { known: [] }, gen: "g2" } }));
});

test("shows an advance button for advancement-eligible fields and calls the API on click", async () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 20, max: 999 } }, derived: {}, errors: [], gen: "g2",
  };
  (api.advanceSheet as any).mockResolvedValue({
    sheet: { sheet_type: "warden", fields: { wits: 3, xp: { current: 11, max: 999 } }, derived: {}, errors: [], gen: "g3" },
  });
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByLabelText("Advance Wits"));
  await waitFor(() => expect(api.advanceSheet).toHaveBeenCalledWith("run", "characters", "mara", "wits"));
});

test("shows the SheetError message on a rejected advance", async () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 1, max: 999 } }, derived: {}, errors: [], gen: "g2",
  };
  (api.advanceSheet as any).mockRejectedValue({ detail: "needs 6 xp, have 1" });
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByLabelText("Advance Wits"));
  await screen.findByText("needs 6 xp, have 1");
});

test("hides the advance button at world scope (starting sheets have no XP economy)", () => {
  const initial: Sheet = {
    sheet_type: "warden", fields: { wits: 2, xp: { current: 20, max: 999 } }, derived: {}, errors: [], gen: "g2",
  };
  render(<SheetEditor scope={{ kind: "world", id: "realm" }} module={ADV_MOD}
                      kind="characters" eid="mara" initial={initial}
                      onClose={() => {}} onSaved={() => {}} />);
  expect(screen.queryByLabelText("Advance Wits")).not.toBeInTheDocument();
});

test("saving a legacy sheet with no gen sends gen: null inside the expected snapshot", async () => {
  const fresh: Sheet = { sheet_type: "medium", fields: {}, derived: {}, errors: [], gen: null };
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={fresh}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara",
    { sheet_type: "medium", fields: {}, expected: { sheet_type: "medium", fields: {}, gen: null } }));
});

test("a type change refreshes the held gen, so the next save uses it instead of 409ing", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  (api.getSheet as any).mockResolvedValue({
    sheet: { sheet_type: "shifter",
      fields: { vigor: 3, strength: 10, fury: { current: 5, max: 5 } },
      derived: {}, errors: [], gen: "g2" } });
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={() => {}} />);
  fireEvent.change(screen.getByLabelText("Change type"), { target: { value: "shifter" } });
  await waitFor(() => expect(api.putSheet).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(api.getSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara"));

  fireEvent.click(screen.getByText("Edit"));
  fireEvent.click(screen.getByLabelText("Vigor 4"));
  fireEvent.click(screen.getByText("Save"));

  await waitFor(() => expect(api.putSheet).toHaveBeenCalledTimes(2));
  const secondCall = (api.putSheet as any).mock.calls[1];
  expect(secondCall[4].expected.gen).toBe("g2");
  expect(secondCall[4].expected.gen).not.toBe(SHEET.gen);
  // refreshSnapshot runs after both the type change and the second save --
  // neither is the reloadAfterConflict path, so putSheet is never re-called.
  expect(api.getSheet).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/changed elsewhere/)).toBeNull();
});

test("a 409 on save re-fetches the sheet, replaces the form, and shows a changed-elsewhere notice", async () => {
  (api.putSheet as any).mockRejectedValue(new ApiError(409, "the sheet changed since it was loaded"));
  (api.getSheet as any).mockResolvedValue({
    sheet: { sheet_type: "medium",
      fields: { vigor: 3, strength: 10, essence: { current: 2, max: 10 }, quirk: "", gear: [] },
      derived: { sight_pool: 3 }, errors: [], gen: "g9" } });
  const onSaved = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={() => {}} onSaved={onSaved} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.click(screen.getByLabelText("Vigor 4"));
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.getSheet).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "pool-basic", "characters", "mara"));
  await screen.findByText("This sheet changed elsewhere — reloaded.");
  // reloaded value (essence 2/10), not the discarded local edit or the stale 6/10
  expect(screen.getByText("2 / 10")).toBeInTheDocument();
  expect(onSaved).toHaveBeenCalled();
});

// Escape mirrors whichever control the form is showing. Always-Close would be
// the one dismissal in the app that discards typing without saying so.
test("Escape cancels the edit first, and only then closes the sheet", () => {
  const onClose = vi.fn();
  render(<SheetEditor scope={{ kind: "campaign", id: "run" }} module={MOD}
                      kind="characters" eid="mara" initial={SHEET}
                      onClose={onClose} onSaved={() => {}} />);
  fireEvent.click(screen.getByText("Edit"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText("Edit")).toBeInTheDocument();      // back to the read view
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});
