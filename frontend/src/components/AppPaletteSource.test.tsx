import { act, fireEvent, render, screen } from "@testing-library/react";
import { useCallback } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { ShellCampaign, ShellPayload } from "../api/types";
import AppPaletteSource from "./AppPaletteSource";
import CommandPalette from "./CommandPalette";
import { PaletteProvider, usePalette, usePaletteSource, type PaletteItem } from "./palette";

vi.mock("../api/client", () => ({ api: {
  listCampaigns: vi.fn().mockResolvedValue([]), listWorlds: vi.fn().mockResolvedValue([]),
} }));

const campaign: ShellCampaign = {
    id: "saltmarch", name: "Saltmarch", world: "realm", world_name: "Realm", scenes: 1,
    open: [], ledger_open: 0, sheets: { sheeted: 1, total: 1 },
    unreviewed: 0, pending: [], images_undescribed: 0,
};
const payload: ShellPayload = {
  campaigns: 1, todo: null, campaign,
};

function Controls() {
  const { setOpen } = usePalette();
  const location = useLocation();
  return <><button onClick={() => setOpen(true)}>Open palette</button>
    <output aria-label="Destination">{location.pathname + location.search}</output></>;
}

function PageOffers({ items }: { items: PaletteItem[] }) {
  usePaletteSource(useCallback(() => items, [items]));
  return null;
}

function Harness({ held, items }: { held: ShellPayload | null; items?: PaletteItem[] }) {
  return <MemoryRouter><PaletteProvider>
    <AppPaletteSource payload={held} />
    {items && <PageOffers items={items} />}
    <Controls /><CommandPalette />
  </PaletteProvider></MemoryRouter>;
}

function open(held: ShellPayload | null = payload) {
  render(<Harness held={held} />);
  fireEvent.click(screen.getByRole("button", { name: "Open palette" }));
  return screen.getByRole("combobox");
}

test.each(["IN THIS CAMPAIGN", "SCENES"])("page-owned %s rows stay first after their source refreshes", async (group) => {
  const choose = vi.fn();
  const offer: PaletteItem = { id: "page:mara", group, label: "Mara", run: choose };
  const view = render(<Harness held={payload} items={[offer]} />);
  fireEvent.click(screen.getByRole("button", { name: "Open palette" }));
  // Let the shell's lists land first, then refresh page-owned data. This puts
  // its source after the shell even when the shell re-registered on its read.
  await act(async () => { await Promise.resolve(); });
  view.rerender(<Harness held={payload} items={[{ ...offer }]} />);
  const first = screen.getAllByRole("option")[0];
  expect(first).toHaveTextContent("Mara");
  expect(first).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
  expect(choose).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Destination")).toHaveTextContent(/^\/$/);
  fireEvent.click(screen.getByRole("button", { name: "Open palette" }));
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "to do" } });
  fireEvent.click(screen.getByRole("option", { name: /To do.*Saltmarch/ }));
  expect(screen.getByLabelText("Destination")).toHaveTextContent("/campaigns/saltmarch/todo");
});

test.each([
  ["costs", "Costs", "/costs"],
  ["stats", "Stats", "/stats"],
  ["backups", "Backups", "/config?section=backups"],
  ["timeline", "Timeline", "/campaigns/saltmarch/timeline"],
  ["knowledge", "Knowledge", "/campaigns/saltmarch/ledger/knowledge"],
  ["sheets", "Sheets", "/campaigns/saltmarch/sheets"],
])("the shell palette reaches %s from any page", async (query, label, destination) => {
  const search = open();
  fireEvent.change(search, { target: { value: query } });
  const options = await screen.findAllByRole("option");
  const item = options.find((option) => option.textContent?.includes(label)
    && (destination.startsWith("/campaigns/") || !option.textContent?.includes("Saltmarch")));
  expect(item).toBeDefined();
  if (!item) return;
  fireEvent.click(item);
  expect(screen.getByLabelText("Destination").textContent).toBe(destination);
});

test.each([["costs", "Costs"], ["stats", "Stats"]])("campaign %s remains distinct from the global destination", async (query, label) => {
  const search = open();
  fireEvent.change(search, { target: { value: query } });
  const offers = (await screen.findAllByRole("option"))
    .filter((option) => option.textContent?.startsWith(label));
  expect(offers).toHaveLength(2);
  expect(new Set(offers.map((option) => option.id)).size).toBe(2);
  const item = offers.find((option) => option.textContent?.includes("Saltmarch"));
  expect(item).toBeDefined();
  if (!item) return;
  fireEvent.click(item);
  expect(screen.getByLabelText("Destination").textContent).toBe(`/campaigns/saltmarch/${query}`);
});

test("an open palette follows campaign changes and removes forgotten destinations", async () => {
  const view = render(<Harness held={payload} />);
  fireEvent.click(screen.getByRole("button", { name: "Open palette" }));
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "sheets" } });
  expect(await screen.findByRole("option", { name: /Sheets.*Saltmarch/ })).toBeInTheDocument();

  view.rerender(<Harness held={{ ...payload, campaign: { ...campaign, id: "realm", name: "Realm" } }} />);
  expect(screen.queryByRole("option", { name: /Sheets.*Saltmarch/ })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("option", { name: /Sheets.*Realm/ }));
  expect(screen.getByLabelText("Destination").textContent).toBe("/campaigns/realm/sheets");

  fireEvent.click(screen.getByRole("button", { name: "Open palette" }));
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "sheets" } });
  view.rerender(<Harness held={null} />);
  expect(screen.queryByRole("option", { name: /Sheets/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "costs" } });
  expect(screen.getByRole("option", { name: /^Costs/ })).toBeInTheDocument();
});

test("unavailable campaign destinations are absent while global navigation remains", async () => {
  const search = open(null);
  fireEvent.change(search, { target: { value: "sheets" } });
  expect(screen.queryByRole("option", { name: /Sheets/ })).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: "costs" } });
  expect(await screen.findByRole("option", { name: /Costs/ })).toBeInTheDocument();
  expect(api.listCampaigns).toHaveBeenCalled();
});
