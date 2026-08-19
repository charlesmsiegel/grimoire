import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { WorldPushPanel } from "./WorldPushPanel";

vi.mock("../api/client", () => ({ api: { worldCampaigns: vi.fn() } }));
import { api } from "../api/client";

const SALTMARCH = { id: "saltmarch-nights", name: "Saltmarch Nights",
                    pending: { new: 2, update: 1, conflict: 0 } };
const WINIFRED = { id: "winifreds-war", name: "Winifred's War",
                   pending: { new: 0, update: 3, conflict: 2 } };

beforeEach(() => {
  vi.clearAllMocks();
  (api.worldCampaigns as any).mockResolvedValue([]);
});

async function renderPanel() {
  render(<MemoryRouter><WorldPushPanel wid="realm" /></MemoryRouter>);
  await act(async () => {});
}

test("lists each campaign for the world with its three pending counts", async () => {
  (api.worldCampaigns as any).mockResolvedValue([SALTMARCH, WINIFRED]);
  await renderPanel();
  expect(api.worldCampaigns).toHaveBeenCalledWith("realm");
  const row = screen.getByRole("link", { name: /Saltmarch Nights/ });
  expect(row).toBeInTheDocument();
  expect(row.textContent).toContain("2 new");
  expect(row.textContent).toContain("1 update");
  expect(row.textContent).toContain("0 conflict");
});

test("each row links to the campaign, where the changes are reviewed", async () => {
  (api.worldCampaigns as any).mockResolvedValue([SALTMARCH]);
  await renderPanel();
  expect(screen.getByRole("link", { name: /Saltmarch Nights/ }))
    .toHaveAttribute("href", "/campaigns/saltmarch-nights");
});

test("a campaign with conflicts is distinguished from one without", async () => {
  (api.worldCampaigns as any).mockResolvedValue([SALTMARCH, WINIFRED]);
  await renderPanel();
  const clean = screen.getByRole("link", { name: /Saltmarch Nights/ });
  const conflicted = screen.getByRole("link", { name: /Winifred's War/ });
  expect(clean.className).not.toContain("has-conflict");
  expect(conflicted.className).toContain("has-conflict");
  expect(screen.getByText("2 conflict").className).toContain("push-conflict");
  expect(screen.getByText("0 conflict").className).not.toContain("push-conflict");
});

test("a campaign with nothing pending says so", async () => {
  (api.worldCampaigns as any).mockResolvedValue([
    { id: "quiet", name: "Quiet Season", pending: { new: 0, update: 0, conflict: 0 } }]);
  await renderPanel();
  expect(screen.getByRole("link", { name: /Quiet Season/ }).textContent).toContain("up to date");
});

test("shows an empty state when the world has no campaigns", async () => {
  await renderPanel();
  expect(screen.getByText(/No campaigns are played in this world yet/)).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("a failed read reports the failure rather than reading as no campaigns", async () => {
  (api.worldCampaigns as any).mockRejectedValue(new Error("world not found"));
  await renderPanel();
  expect(screen.getByText("world not found")).toBeInTheDocument();
  expect(screen.queryByText(/No campaigns are played/)).not.toBeInTheDocument();
});
