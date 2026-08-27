import { render, screen, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// The three settings panels are driven by their own suites; here they only
// have to be REACHABLE, which is the bug this covers.
vi.mock("../components/MechanicsConfig", () => ({
  default: () => <div data-testid="mechanics-panel" />,
}));
vi.mock("../components/CalendarConfig", () => ({
  CalendarConfig: () => <div data-testid="calendar-panel" />,
}));
vi.mock("../components/CampaignCover", () => ({
  CampaignCover: () => <div data-testid="cover-panel" />,
}));

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

test("the layout picker is gone; the hub is one column", async () => {
  // Three layouts of the same cards is a setting the reader has to have an
  // opinion about before they can read the page.
  renderHub();
  await screen.findByText("Run One");
  expect(screen.queryByText(/two column/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/^cards$/i)).not.toBeInTheDocument();
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

test("with no mechanics module there is no sheets card and no sheets link", async () => {
  // "No mechanics bound" is a legal state, not a coverage of 0 of 0 -- and a
  // card whose whole content is "this does not apply here" is a card that
  // should not be on the page.
  renderHub();
  await screen.findByText("Run One");
  expect(screen.queryByText(/mechanics & sheets/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /^sheets$/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/0 of 0/)).not.toBeInTheDocument();
});

test("with a module bound the sheets card carries its coverage", async () => {
  (api.getShell as any).mockResolvedValue(shell({ sheets: { sheeted: 4, total: 7 } }));
  renderHub();
  await screen.findByText("Run One");
  expect(screen.getByText("4 of 7")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /^sheets$/i }))
    .toHaveAttribute("href", "/campaigns/run/sheets");
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


test("the campaign's own settings are reachable from its front door", () => {
  // Mechanics, Calendar and Cover belong to the CAMPAIGN, but they used to sit
  // on the scene bar -- so opening a campaign gave you no way to bind a
  // mechanics module at all. You had to already be inside a scene to configure
  // the thing the scenes belong to.
  renderHub();
  return screen.findByText("Run One").then(() => {
    fireEvent.click(screen.getByRole("button", { name: "Mechanics" }));
    expect(screen.getByTestId("mechanics-panel")).toBeInTheDocument();
    // One at a time, and clicking the open one closes it.
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    expect(screen.queryByTestId("mechanics-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("calendar-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    expect(screen.queryByTestId("calendar-panel")).not.toBeInTheDocument();
  });
});
