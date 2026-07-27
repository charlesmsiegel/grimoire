import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn(),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
    listModules: vi.fn().mockResolvedValue([]),
  },
}));
import { api } from "./api/client";

vi.mock("./routes/CampaignView", () => ({
  default: ({ topbarCollapsed, onToggleTopbar }: any) => (
    <div data-testid="campaign-view">
      <button onClick={onToggleTopbar}>toggle-topbar</button>
      <span>{topbarCollapsed ? "collapsed" : "expanded"}</span>
    </div>
  ),
}));

vi.mock("./routes/CampaignWizard", () => ({
  default: () => <div data-testid="campaign-wizard" />,
}));

const READY_OPENROUTER = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);
});

test("renders the chrome top bar with brand, nav, and connection status", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/GRIMOIRE/)).toBeInTheDocument();
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /campaigns/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /modules/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /connections/i })).toBeInTheDocument();
  expect(topbar.getByText(/openrouter · connected/i)).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /config/i })).toBeInTheDocument();
});

test("shows NOT READY and the connection's name when unready", async () => {
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false,
    active_connection: { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/z\.ai glm · not ready/i)).toBeInTheDocument();
});

test("the status pill refetches and updates after navigating, without a reload", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/openrouter · connected/i);

  // simulate the active connection having changed elsewhere (Config/Connections
  // page) — the next getConfig() call reflects it
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude" },
  });
  const topbar = within(screen.getByRole("banner"));
  fireEvent.click(topbar.getByRole("link", { name: /worlds/i }));
  await waitFor(() => expect(screen.getByText(/claude · not ready/i)).toBeInTheDocument());
});

test("the topbar collapses only while viewing a campaign, via CampaignView's own toggle", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/run"]}><App /></MemoryRouter>);
  const view = await screen.findByTestId("campaign-view");
  expect(within(view).getByText("expanded")).toBeInTheDocument();
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");

  fireEvent.click(within(view).getByText("toggle-topbar"));
  expect(within(view).getByText("collapsed")).toBeInTheDocument();
  expect(screen.getByRole("banner")).toHaveClass("collapsed");
});

test("a previously-collapsed topbar preference does not apply on non-campaign routes", async () => {
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
});

test("the topbar stays fully visible on /campaigns/new even with a stored collapsed preference", async () => {
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/campaigns/new"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-wizard");
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
});
