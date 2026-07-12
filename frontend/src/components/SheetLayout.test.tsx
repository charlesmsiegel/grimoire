import { render, screen } from "@testing-library/react";
import SheetLayout, { defaultLayout, themeStyle } from "./SheetLayout";
import type { ModuleDetail } from "../api/client";

const MOD: ModuleDetail = {
  id: "pool-basic", source: "builtin",
  manifest: { id: "pool-basic", name: "Pool Basic" },
  sheets: {
    groups: {
      attributes: { label: "Attributes", fields: [
        { key: "vigor", label: "Vigor", type: "dots", max: 5 },
        { key: "wits", label: "Wits", type: "dots", max: 5 },
      ]},
    },
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: ["attributes"],
        fields: [{ key: "essence", label: "Essence", type: "resource", max: 10 },
                 { key: "gear", label: "Gear", type: "list" }],
        derived: { sight_pool: "wits" } },
    },
  },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {
    medium: { column: [
      { group: "attributes", title: "Attributes" },
      { row: [{ fields: ["essence"], title: "Power" },
              { derived: ["sight_pool"], title: "Gifts" }] },
    ]},
  }},
  theme: {}, display_errors: [],
};

const VALUES = { vigor: 3, wits: 2, essence: { current: 6, max: 10 }, gear: ["rope"] };
const DERIVED = { sight_pool: 2 };

test("renders layout tree with titles, widgets, and derived badges", () => {
  const { container } = render(
    <SheetLayout module={MOD} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument();
  expect(screen.getByText("Power")).toBeInTheDocument();
  expect(container.querySelectorAll(".pip").length).toBe(10);  // vigor + wits
  expect(screen.getByText("sight_pool")).toBeInTheDocument();
});

test("unplaced fields land in Other", () => {
  // layout places essence but not gear
  render(<SheetLayout module={MOD} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Other")).toBeInTheDocument();
  expect(screen.getByText("rope")).toBeInTheDocument();
});

test("no layout: default arrangement with widgets and trailing Derived", () => {
  const bare: ModuleDetail = { ...MOD, layout: { sheet_types: {} } };
  const { container } = render(
    <SheetLayout module={bare} sheetType="medium" mode="view" values={VALUES} derived={DERIVED} />);
  expect(screen.getByText("Attributes")).toBeInTheDocument(); // group title
  expect(screen.getByText("Details")).toBeInTheDocument();    // own fields
  expect(screen.getByText("Derived")).toBeInTheDocument();    // trailing derived
  expect(container.querySelectorAll(".pip").length).toBe(10);
  expect(screen.queryByText("Other")).toBeNull();             // everything placed
});

test("defaultLayout skips groups missing from the module", () => {
  const broken: ModuleDetail = { ...MOD, sheets: { ...MOD.sheets,
    sheet_types: { medium: { ...MOD.sheets.sheet_types.medium, groups: ["attributes", "ghost"] } } } };
  const node = defaultLayout(broken, "medium");
  expect(JSON.stringify(node)).not.toContain("ghost");
});

test("edit mode threads onChange through widgets", () => {
  const onChange = vi.fn();
  render(<SheetLayout module={MOD} sheetType="medium" mode="edit"
                      values={VALUES} derived={DERIVED} onChange={onChange} />);
  screen.getByLabelText("Vigor 4").click();
  expect(onChange).toHaveBeenCalledWith("vigor", 4);
});

test("themeStyle maps tokens to sheet vars", () => {
  expect(themeStyle({ colors: { bg: "#111", ink: "#eee" }, fonts: { body: "serif" } }))
    .toEqual({ "--sheet-bg": "#111", "--sheet-ink": "#eee",
               "--sheet-fb": "Georgia, 'Times New Roman', serif" });
  expect(themeStyle(undefined)).toEqual({});
});
