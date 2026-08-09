import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignsView from "./CampaignsView";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", () => ({
  api: {
    listCampaigns: vi.fn(),
    listWorlds: vi.fn(),
    renameCampaign: vi.fn(),
    deleteCampaign: vi.fn(),
    campaignCoverUrl: (cid: string, o?: { w?: number; v?: string }) =>
      `/api/campaigns/${cid}/cover?w=${o?.w}&v=${o?.v}`,
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCampaigns as any).mockResolvedValue([]);
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  (api.renameCampaign as any).mockResolvedValue({ id: "c1", name: "New" });
  (api.deleteCampaign as any).mockResolvedValue({ ok: true });
});

function renderView() {
  render(
    <MemoryRouter>
      <CampaignsView />
    </MemoryRouter>,
  );
}

test("lists campaigns with world/scene metadata rows", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Ashes of the Verdigris Crown", world: "w1", created: "", updated: "",
      scenes: 4, last_scene: "Verdigris & Ash" },
  ]);
  renderView();
  await screen.findByText("Ashes of the Verdigris Crown");
  expect(screen.getByText(/WORLD ▸ Realm · 4 SCENES · LAST: Verdigris & Ash/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /campaigns/i })).toBeInTheDocument();
});

test("New campaign button navigates to the wizard", async () => {
  renderView();
  await waitFor(() => expect(screen.getByRole("button", { name: /new campaign/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /new campaign/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/new");
});

test("New campaign is disabled with guidance when there are no worlds", async () => {
  (api.listWorlds as any).mockResolvedValue([]);
  renderView();
  await screen.findByText(/create a world first/i);
  expect(screen.getByRole("button", { name: /new campaign/i })).toBeDisabled();
});

test("deletes a campaign after confirm", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Doomed", world: "w1", created: "", updated: "", scenes: 0, last_scene: "" },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Doomed");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteCampaign).toHaveBeenCalledWith("c1"));
});

test("renders a thumbnail for a campaign with a cover and a placeholder without", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Nights", world: "realm", scenes: 3, last_scene: "Arrival", cover: "v1" },
    { id: "winifred", name: "Winifred's War", world: "realm", scenes: 1, last_scene: "", cover: "" },
  ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);

  const img = await screen.findByAltText("Saltmarch Nights cover");
  expect(img.getAttribute("src")).toContain("/api/campaigns/saltmarch/cover");
  expect(img.getAttribute("src")).toContain("w=96");  // a list thumbnail must ask for a downscale, never the original
  expect(img.getAttribute("src")).toContain("v=v1");
  expect(screen.queryByAltText("Winifred's War cover")).toBeNull();
  expect(document.querySelectorAll(".list-row-cover").length).toBe(2);  // both boxes, aligned
});

test("a thumbnail that fails to load falls back to the placeholder box", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Nights", world: "realm", scenes: 3, last_scene: "", cover: "v1" },
  ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);
  const img = await screen.findByAltText("Saltmarch Nights cover");
  fireEvent.error(img);
  await waitFor(() => expect(screen.queryByAltText("Saltmarch Nights cover")).toBeNull());
  expect(document.querySelectorAll(".list-row-cover").length).toBe(1);  // the box stays
});

test("a replacement cover is not hidden by the previous version's broken mark", async () => {
  (api.listCampaigns as any)
    .mockResolvedValueOnce([
      { id: "saltmarch", name: "Saltmarch Nights", world: "w1", scenes: 3, last_scene: "", cover: "v1" },
    ])
    .mockResolvedValueOnce([
      { id: "saltmarch", name: "Saltmarch Nights", world: "w1", scenes: 3, last_scene: "", cover: "v2" },
    ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);

  const v1img = await screen.findByAltText("Saltmarch Nights cover");
  expect(v1img.getAttribute("src")).toContain("v=v1");
  fireEvent.error(v1img);  // v1 marked broken
  await waitFor(() => expect(screen.queryByAltText("Saltmarch Nights cover")).toBeNull());

  // a refetch (same id, new cover token) must not stay hidden under the old key
  fireEvent.click(screen.getByRole("button", { name: "Rename Saltmarch Nights" }));
  fireEvent.keyDown(screen.getByLabelText("Rename campaign"), { key: "Enter" });
  await waitFor(() => expect(api.renameCampaign).toHaveBeenCalled());

  const v2img = await screen.findByAltText("Saltmarch Nights cover");
  expect(v2img.getAttribute("src")).toContain("v=v2");
});
