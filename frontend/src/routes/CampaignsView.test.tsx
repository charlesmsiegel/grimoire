import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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

const column = () => within(screen.getByRole("complementary"));
const card = (name: string) =>
  screen.getByText(name).closest(".campaign-card") as HTMLElement;

test("a campaign card carries the shelf line, the blurb and one primary action", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Ashes of the Verdigris Crown", world: "w1", created: "", updated: "",
      activity: new Date(Date.now() - 2 * 86_400_000).toISOString().replace("T", " ").slice(0, 19),
      scenes: 4, last_scene: "Verdigris & Ash", absorbed_through: "The Long Tide",
      blurb: "Wyle came for a drowned cousin and stayed for the nail." },
  ]);
  renderView();
  await screen.findByText("Ashes of the Verdigris Crown");
  const c = within(card("Ashes of the Verdigris Crown"));
  expect(c.getByText(/4 scenes/i)).toBeInTheDocument();
  expect(c.getByText(/last played 2 days ago/i)).toBeInTheDocument();
  // Absorbed-through is not derivable from the scene count: playing a scene
  // ahead of the absorb is the normal state of a campaign in progress.
  expect(c.getByText(/absorbed through The Long Tide/i)).toBeInTheDocument();
  expect(c.getByText(/drowned cousin/i)).toBeInTheDocument();
  expect(c.getByRole("link", { name: /continue/i })).toHaveAttribute("href", "/campaigns/c1");
  expect(c.getByText("Verdigris & Ash")).toBeInTheDocument();
});

test("a campaign nothing has been absorbed from says so rather than nothing", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Tidewrack", world: "w1", scenes: 3, last_scene: "" },
  ]);
  renderView();
  await screen.findByText("Tidewrack");
  const c = within(card("Tidewrack"));
  expect(c.getByText(/not yet absorbed/i)).toBeInTheDocument();
  expect(c.getByText(/never played/i)).toBeInTheDocument();
  // no last scene to continue into, so the action names what it really does
  expect(c.getByRole("link", { name: /open/i })).toBeInTheDocument();
});

test("only the campaign at the top of the shelf carries rename and delete", async () => {
  // A ✕ on every card is a ✕ you can hit by accident on the wrong campaign,
  // and the shelf's ordering already says which one you meant.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "old", name: "Tidewrack", world: "w1", activity: "2026-01-01", scenes: 1, last_scene: "" },
    { id: "new", name: "Saltmarch", world: "w1", activity: "2026-06-01", scenes: 1, last_scene: "" },
  ]);
  renderView();
  await screen.findByText("Saltmarch");
  expect(card("Saltmarch")).toHaveClass("active");
  expect(within(card("Saltmarch")).getByRole("button", { name: /delete/i })).toBeInTheDocument();
  expect(within(card("Tidewrack")).queryByRole("button", { name: /delete/i })).toBeNull();
});

test("the shelf ranks by activity, not by campaign.md's updated stamp", async () => {
  // `updated` only moves on metadata writes, so ordering by it ranks a
  // campaign renamed months ago above one played into last night.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "renamed", name: "Tidewrack", world: "w1", updated: "2026-06-01",
      activity: "2026-01-01", scenes: 1, last_scene: "" },
    { id: "played", name: "Saltmarch", world: "w1", updated: "2026-01-01",
      activity: "2026-06-01", scenes: 1, last_scene: "" },
  ]);
  renderView();
  await screen.findByText("Saltmarch");
  const names = Array.from(document.querySelectorAll(".campaign-name")).map((n) => n.textContent);
  expect(names).toEqual(["Saltmarch", "Tidewrack"]);
});

test("the column's worlds filter the shelf without leaving the page", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", counts: {} },
    { id: "w2", name: "Saltmarch", counts: {} },
  ]);
  (api.listCampaigns as any).mockResolvedValue([
    { id: "a", name: "In Realm", world: "w1", scenes: 1, last_scene: "" },
    { id: "b", name: "In Saltmarch", world: "w2", scenes: 1, last_scene: "" },
  ]);
  renderView();
  await screen.findByText("In Realm");
  // each world carries its own campaign count
  expect(within(column().getByRole("button", { name: /^Realm/ })).getByText("1")).toBeInTheDocument();

  fireEvent.click(column().getByRole("button", { name: /^Saltmarch/ }));
  expect(screen.queryByText("In Realm")).toBeNull();
  expect(screen.getByText("In Saltmarch")).toBeInTheDocument();
  expect(navigate).not.toHaveBeenCalled();   // a filter, not a navigation

  fireEvent.click(column().getByRole("button", { name: /all worlds/i }));
  expect(await screen.findByText("In Realm")).toBeInTheDocument();
});

test("New campaign navigates to the wizard", async () => {
  renderView();
  await waitFor(() => expect(screen.getByRole("button", { name: /new campaign/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /new campaign/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/new");
});

test("with no worlds the empty state names what is missing and where to go", async () => {
  (api.listWorlds as any).mockResolvedValue([]);
  renderView();
  await screen.findByText(/no worlds yet/i);
  expect(screen.getByRole("link", { name: /create a world/i })).toHaveAttribute("href", "/worlds");
  expect(screen.getByRole("button", { name: /new campaign/i })).toBeDisabled();
});

test("an empty world says which world is empty, not just that nothing is here", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", counts: {} },
    { id: "w2", name: "Saltmarch", counts: {} },
  ]);
  (api.listCampaigns as any).mockResolvedValue([
    { id: "a", name: "In Realm", world: "w1", scenes: 1, last_scene: "" },
  ]);
  renderView();
  await screen.findByText("In Realm");
  fireEvent.click(column().getByRole("button", { name: /^Saltmarch/ }));
  expect(screen.getByText(/no campaigns in Saltmarch yet/i)).toBeInTheDocument();
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

test("renders a cover for a campaign that has one and a placeholder for one that does not", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Nights", world: "w1", activity: "2026-06-01",
      scenes: 3, last_scene: "Arrival", cover: "v1" },
    { id: "winifred", name: "Winifred's War", world: "w1", activity: "2026-01-01",
      scenes: 1, last_scene: "", cover: "" },
  ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);

  const img = await screen.findByAltText("Saltmarch Nights cover");
  expect(img.getAttribute("src")).toContain("/api/campaigns/saltmarch/cover");
  // a card thumbnail must ask for a downscale, never the original
  expect(img.getAttribute("src")).toContain("w=208");
  expect(img.getAttribute("src")).toContain("v=v1");
  expect(screen.queryByAltText("Winifred's War cover")).toBeNull();
  expect(document.querySelectorAll(".shelf-cover").length).toBe(2);  // both boxes, aligned
});

test("a cover that fails to load falls back to the placeholder box", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Nights", world: "w1", scenes: 3, last_scene: "", cover: "v1" },
  ]);
  render(<MemoryRouter><CampaignsView /></MemoryRouter>);
  const img = await screen.findByAltText("Saltmarch Nights cover");
  fireEvent.error(img);
  await waitFor(() => expect(screen.queryByAltText("Saltmarch Nights cover")).toBeNull());
  expect(document.querySelectorAll(".shelf-cover").length).toBe(1);  // the box stays
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
