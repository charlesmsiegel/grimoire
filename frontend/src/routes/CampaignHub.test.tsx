import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    getShell: vi.fn(),
    listScenes: vi.fn(),
    getChronicle: vi.fn(),
  },
}));

import { api } from "../api/client";
import CampaignHub from "./CampaignHub";

const META = { id: "run", name: "Run One", world: "w", world_name: "Saltmarch",
               created: "", updated: "", scenes: 3, last_scene: "", absorbed: 1 };

function shell(over: Record<string, unknown> = {}) {
  return {
    campaigns: 2, todo: null,
    campaign: {
      id: "run", name: "Run One", world_name: "Saltmarch", scenes: 3,
      open: [], ledger_open: 0, sheets: null, unreviewed: 0, pending: [],
      images_undescribed: null, ...over,
    },
  };
}

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes><Route path="/campaigns/:cid" element={<CampaignHub />} /></Routes>
    </MemoryRouter>);
}

beforeEach(() => {
  (api.getCampaign as any).mockResolvedValue({ meta: META, body: "" });
  (api.getShell as any).mockResolvedValue(shell());
  (api.listScenes as any).mockResolvedValue([
    { id: "s3", title: "The third", done: false, model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "The second", done: true, model: "", created: "", updated: "", date: "" },
  ]);
  (api.getChronicle as any).mockResolvedValue([
    { sid: "s2", one_line: "It ended.", summary: "It ended, and the tide went out." },
  ]);
});

test("the hub leads with what to do next, not with state", async () => {
  (api.getShell as any).mockResolvedValue(
    shell({ open: [{ sid: "s3", title: "The third", turns: null }] }));
  renderHub();
  await screen.findByText("Run One");
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByText("The third")).toBeInTheDocument();
  expect(within(next).getByRole("link", { name: /continue scene/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s3");
});

test("with several scenes open it offers each rather than resuming one", async () => {
  // The behaviour this whole page replaced: opening a campaign used to navigate
  // straight to whichever scene was played last. Picking one inside a card
  // would be the same mistake with a nicer border.
  (api.getShell as any).mockResolvedValue(shell({
    open: [{ sid: "s3", title: "The third", turns: null },
            { sid: "s4", title: "The fourth", turns: null }],
  }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText(/pick one rather than being sent to whichever was last/i))
    .toBeInTheDocument();
  // Scoped to Next up: the Scenes card lists the same scenes further down, and
  // a bare query would match both and prove neither.
  const next = screen.getByText("Next up").closest("section")!;
  expect(within(next).getByRole("link", { name: /the third/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s3");
  expect(within(next).getByRole("link", { name: /the fourth/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s4");
  // ...and no single "continue", which would be a pick the reader did not make.
  expect(screen.queryByRole("link", { name: /continue scene/i })).not.toBeInTheDocument();
});

test("unreviewed proposals are named as holding the world back", async () => {
  (api.getShell as any).mockResolvedValue(
    shell({ unreviewed: 8, pending: [{ sid: "s2", proposals: 8 }] }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("Waiting on you")).toBeInTheDocument();
  expect(screen.getByText(/8 proposals/)).toBeInTheDocument();
});

test("nothing waiting reads as an answer, not as an empty banner", async () => {
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("Nothing waiting")).toBeInTheDocument();
  expect(screen.queryByText("Waiting on you")).not.toBeInTheDocument();
});

test("no mechanics module is not 0 of 0 sheeted", async () => {
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText(/binds no mechanics module/i)).toBeInTheDocument();
});

// Moved here with the controls themselves: these were on the scene toolbar,
// which is where four campaign-level destinations were duplicating the rail.
test("the export menu carries a download link per format", async () => {
  renderHub();
  await screen.findByText("Run One");
  const epub = screen.getByRole("link", { name: /^epub$/i });
  expect(epub).toHaveAttribute("href", "/api/campaigns/run/export.epub");
  expect(epub).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: /markdown/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.md.zip");
  expect(screen.getByRole("link", { name: /^html$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.html");
  expect(screen.getByRole("link", { name: /plain text/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.txt");
});

test("the ledger is reachable from the hub", async () => {
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByRole("link", { name: /ledger & timeline/i }))
    .toHaveAttribute("href", "/campaigns/run/ledger");
});

test("a failed read is not an empty campaign", async () => {
  // Opposite answers, and the difference has to survive: "could not be read"
  // must never render as "there is nothing here".
  (api.getShell as any).mockRejectedValue(new Error("offline"));
  renderHub();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText("Next up")).not.toBeInTheDocument();
});
