import { render, fireEvent, waitFor, within } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    listModules: vi.fn(),
    readModule: vi.fn(),
  },
}));
import { api } from "../api/client";
import ModulesView from "./ModulesView";

const POOL = {
  id: "pool-basic",
  source: "builtin",
  manifest: { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", dice: "5d10 t6" },
  sheets: {
    groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
    sheet_types: {
      medium: { label: "Medium", kind: "characters", groups: ["attributes"], fields: [], derived: {} },
      talisman: { label: "Talisman", kind: "items", groups: [], fields: [{ key: "power", label: "Power", type: "dots", max: 5 }], derived: {} },
    },
  },
  checks: { brawl: { label: "Vigor + Brawl", roll: "{vigor}d10 t6", requires: ["attributes"] } },
  rules: [{ id: "core", keys: [], always: true, on_roll: false, sheet_types: [] }],
  content: [],
  errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listModules as any).mockResolvedValue([
    { id: "pool-basic", name: "Basic Pool", description: "d10 pools.", version: "0.1", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue(POOL);
});

test("clicking a row shows the read-only module detail", async () => {
  const { container } = render(<ModulesView />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Basic Pool"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  const detail = await waitFor(() => container.querySelector(".detail-view") as HTMLElement);
  expect(within(detail).getByText("d10 pools.")).toBeInTheDocument();
  expect(within(detail).getByText("Medium")).toBeInTheDocument();
  expect(within(detail).getByText("Talisman")).toBeInTheDocument();
  expect(within(detail).getByText("Vigor + Brawl")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();   // read-only
  expect(within(detail).queryByText("Edit")).toBeNull();    // no edit affordance
});

test("renders valid sheet types and the Problems section without throwing on a broken pack", async () => {
  const BROKEN = {
    ...POOL,
    sheets: {
      groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", label: "Vigor", type: "dots", max: 5 }] } },
      sheet_types: {
        broken: "oops",
        medium: { label: "Medium", kind: "characters", groups: ["ghost-group"], fields: [], derived: {} },
      },
    },
    errors: ["sheet_types.broken: must be an object", "sheet_types.medium: unknown group ref 'ghost-group'"],
  };
  (api.readModule as any).mockResolvedValue(BROKEN);
  const { container } = render(<ModulesView />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Basic Pool"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  const detail = await waitFor(() => container.querySelector(".detail-view") as HTMLElement);
  expect(within(detail).getByText("Medium")).toBeInTheDocument();
  expect(within(detail).getByText("ghost-group")).toBeInTheDocument(); // falls back to raw id
  expect(within(detail).getByText("Problems")).toBeInTheDocument();
  expect(within(detail).getByText(/unknown group ref/)).toBeInTheDocument();
});
