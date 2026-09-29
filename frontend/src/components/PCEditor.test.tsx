import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listPCs: vi.fn(), createPC: vi.fn(), createCampaignPC: vi.fn(),
    deletePC: vi.fn(), actorImageUrl: vi.fn(() => "/portrait"),
  } };
});
import { api } from "../api/client";

function Destination() {
  const location = useLocation();
  return <div>{location.pathname} {String((location.state as { newPC?: boolean } | null)?.newPC)}</div>;
}
function renderIndex(campaign = false) {
  const scope = campaign ? { kind: "campaign" as const, id: "saltmarch" }
    : { kind: "world" as const, id: "realm" };
  const base = campaign ? "/campaigns/saltmarch/world/pcs" : "/worlds/realm/pcs";
  return render(<MemoryRouter initialEntries={[base]}><Routes>
    <Route path={base} element={<PCEditor scope={scope} wid="realm"
      recordHref={(id) => `${base}/${encodeURIComponent(id)}`} />} />
    <Route path={`${base}/:pid`} element={<Destination />} />
  </Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.clearAllMocks();
  (api.listPCs as any).mockResolvedValue([{ id: "mara", name: "Mara",
    default_version: "default", has_avatar: false }]);
  (api.createPC as any).mockResolvedValue({ pc: "winifred" });
  (api.createCampaignPC as any).mockResolvedValue({ pc: "winifred" });
});

test("the PC section lists records at their own URLs", async () => {
  renderIndex();
  expect(await screen.findByRole("link", { name: "Mara" })).toHaveAttribute("href", "/worlds/realm/pcs/mara");
  expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "realm" });
});

test("creating a world PC opens its record in edit mode", async () => {
  (api.createPC as any).mockImplementation(async (world: string, input: { name: string }) => {
    if (world !== "realm" || input.name !== "Winifred") throw new Error("wrong create scope");
    return { pc: "winifred" };
  });
  const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce("Winifred");
  renderIndex();
  fireEvent.click(screen.getByRole("button", { name: "+ New PC" }));
  await waitFor(() => expect(screen.getByText("/worlds/realm/pcs/winifred true")).toBeInTheDocument());
  prompt.mockRestore();
});

test("creating a campaign PC keeps campaign scope", async () => {
  (api.createCampaignPC as any).mockImplementation(async (campaign: string, input: { name: string }) => {
    if (campaign !== "saltmarch" || input.name !== "Winifred") throw new Error("wrong create scope");
    return { pc: "winifred" };
  });
  const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce("Winifred");
  renderIndex(true);
  fireEvent.click(screen.getByRole("button", { name: "+ New PC" }));
  await waitFor(() => expect(screen.getByText("/campaigns/saltmarch/world/pcs/winifred true")).toBeInTheDocument());
  prompt.mockRestore();
});

test("a list read failure is visible", async () => {
  (api.listPCs as any).mockRejectedValueOnce(new Error("unavailable"));
  renderIndex();
  expect(await screen.findByText(/unavailable/i)).toBeInTheDocument();
});
