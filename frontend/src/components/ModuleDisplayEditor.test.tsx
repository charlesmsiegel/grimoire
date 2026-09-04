import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import { LayoutSection, MAX_LAYOUT_DEPTH, splice, ThemeSection } from "./ModuleDisplayEditor";

vi.mock("../api/client", () => ({
  api: {
    putModuleLayout: vi.fn(), putModuleTheme: vi.fn(),
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
  layout_source: { sheet_types: { warden: { group: "attributes" } } },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

test("layout textarea seeds from layout_source and previews the selected type", async () => {
  (api.putModuleLayout as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  const ta = screen.getByLabelText("Layout JSON") as HTMLTextAreaElement;
  expect(ta.value).toContain('"warden"');
  // preview renders the group's fields via SheetLayout
  expect(await screen.findByText("Strength")).toBeInTheDocument();
});

test("invalid JSON disables Save and shows a hint", () => {
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Layout JSON"), { target: { value: "{nope" } });
  expect(screen.getByText(/invalid JSON/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("dry-run display errors render as hints", async () => {
  (api.putModuleLayout as any).mockResolvedValue({ ok: true, errors: [],
    display_errors: [{ source: "layout", sheet_type: "warden", message: "warden: unknown field 'ghost'" }] });
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Layout JSON"),
    { target: { value: JSON.stringify({ sheet_types: { warden: { fields: ["ghost"] } } }) } });
  await vi.advanceTimersByTimeAsync(600);
  expect(await screen.findByText(/unknown field 'ghost'/)).toBeInTheDocument();
});

test("theme controls drive the preview vars and save the token object", async () => {
  (api.putModuleTheme as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ThemeSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Dots"), { target: { value: "diamond" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleTheme).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ dots: "diamond" }), false));
});

// Depth of a spliced tree: how many nested containers sit under the root.
function nesting(node: any): number {
  const kids = node?.column ?? node?.row;
  return Array.isArray(kids) && kids.length ? 1 + Math.max(...kids.map(nesting)) : 0;
}

test("the preview depth cap bounds raw row/column nesting, not only use-chains", () => {
  // #227 T18. The backend caps every structural step; the client used to
  // count only `use` hops, so a draft nesting bare columns far past the cap
  // recursed all the way down.
  let node: any = { fields: ["strength"] };
  for (let i = 0; i < MAX_LAYOUT_DEPTH * 4; i++) node = { column: [node] };
  const out = splice(node, {});
  expect(nesting(out)).toBeLessThanOrEqual(MAX_LAYOUT_DEPTH + 1);
  // and a `use` chain past the cap still ends in an empty node, as before
  const fragments: Record<string, any> = {};
  for (let i = 0; i < MAX_LAYOUT_DEPTH * 2; i++) fragments[`f${i}`] = { use: `f${i + 1}` };
  fragments[`f${MAX_LAYOUT_DEPTH * 2}`] = { fields: ["strength"] };
  expect(splice({ use: "f0" }, fragments)).toEqual({});
  // a self-referencing fragment is still cut at the first repeat
  expect(splice({ use: "loop" }, { loop: { column: [{ use: "loop" }] } }))
    .toEqual({ column: [{}] });
});
