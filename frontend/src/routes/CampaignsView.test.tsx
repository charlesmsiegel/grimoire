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
    forkCampaign: vi.fn(),
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
  (api.forkCampaign as any).mockResolvedValue({
    id: "c2", from_scene: "", removed_scenes: [], records: 0, refused: [], failed: [] });
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


// ---- fork lineage (#72) ----

const FAMILY = [
  { id: "c1", name: "Saltmarch", world: "w1", scenes: 3, last_scene: "",
    updated: "2026-08-02 10:00:00" },
  { id: "c2", name: "Saltmarch (fork)", world: "w1", scenes: 1, last_scene: "",
    updated: "2026-08-03 10:00:00", parent: "c1", forked_from_scene: "001--the-oath" },
];

test("a fork is nested under the campaign it came from, however it ranks", async () => {
  // c2 was played more recently, so the flat shelf would put it first. The tree
  // groups it under its parent without re-ranking anything else.
  (api.listCampaigns as any).mockResolvedValue(FAMILY);
  renderView();
  await screen.findByText("Saltmarch (fork)");
  const cards = document.querySelectorAll(".campaign-card");
  expect([...cards].map((c) => c.querySelector(".campaign-name")?.textContent))
    .toEqual(["Saltmarch", "Saltmarch (fork)"]);
  expect(cards[0].className).not.toContain("forked");
  expect(cards[1].className).toContain("forked");
});

test("a fork says which campaign it came from, and that it was cut at a scene", async () => {
  (api.listCampaigns as any).mockResolvedValue(FAMILY);
  renderView();
  await screen.findByText("Saltmarch (fork)");
  const c = within(card("Saltmarch (fork)"));
  expect(c.getByText(/FORKED FROM SALTMARCH/)).toBeInTheDocument();
  expect(c.getByText(/AT AN EARLIER SCENE/)).toBeInTheDocument();
  // ...and the campaign it was forked from carries no such chip.
  expect(within(card("Saltmarch")).queryByText(/FORKED FROM/)).toBeNull();
});

test("a fork whose parent is gone still sits on the shelf and still names it", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c2", name: "Orphan", world: "w1", scenes: 1, last_scene: "", parent: "deleted-one" },
  ]);
  renderView();
  await screen.findByText("Orphan");
  expect(document.querySelectorAll(".campaign-card")).toHaveLength(1);
  expect(within(card("Orphan")).getByText(/FORKED FROM DELETED-ONE/)).toBeInTheDocument();
});

test("forking from the shelf names the fork and relists", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Saltmarch", world: "w1", scenes: 3, last_scene: "" },
  ]);
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  renderView();
  await screen.findByText("Saltmarch");
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  expect(prompt.mock.calls[0][1]).toBe("Saltmarch (fork)");   // a default worth accepting
  await waitFor(() => expect(api.forkCampaign).toHaveBeenCalledWith("c1", "A Second Run"));
  // The shelf is re-read rather than navigated away from: the new branch
  // appearing under its parent is the point.
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalledTimes(2));
  expect(navigate).not.toHaveBeenCalled();
});

test("a cancelled fork prompt forks nothing", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Saltmarch", world: "w1", scenes: 3, last_scene: "" },
  ]);
  vi.spyOn(window, "prompt").mockReturnValue(null);
  renderView();
  await screen.findByText("Saltmarch");
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  expect(api.forkCampaign).not.toHaveBeenCalled();
});

test("what a fork could not put back is reported on the shelf", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Saltmarch", world: "w1", scenes: 3, last_scene: "" },
  ]);
  (api.forkCampaign as any).mockResolvedValue({
    id: "c2", from_scene: "001--the-oath", removed_scenes: ["002--the-debt"], records: 2,
    refused: [{ label: "The Pact — lore", reason: "this record changed after the edit" }],
    failed: [] });
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  renderView();
  await screen.findByText("Saltmarch");
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  await screen.findByText(/The Pact — lore/);
  expect(document.body.textContent).toMatch(/still holds what a removed scene wrote/i);
});

test("a fork the server refuses is reported rather than silently doing nothing", async () => {
  // 409 CAMPAIGN BUSY is reachable: a fork holds two campaign locks, and the
  // source may be being played in another window. Dropped, that looks exactly
  // like a fork that worked.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Saltmarch", world: "w1", scenes: 3, last_scene: "" },
  ]);
  (api.forkCampaign as any).mockRejectedValue({ detail: "campaign is busy" });
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  renderView();
  await screen.findByText("Saltmarch");
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  await screen.findByText(/could not be forked: campaign is busy/i);
  // ...and the shelf is not re-read, because nothing on it changed.
  expect(api.listCampaigns).toHaveBeenCalledTimes(1);
});
