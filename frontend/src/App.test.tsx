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

const READY_OPENROUTER = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  default_style_id: "", active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
};

beforeEach(() => {
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
