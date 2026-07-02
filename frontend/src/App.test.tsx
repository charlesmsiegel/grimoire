import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn().mockResolvedValue({
      model: "m", theme: "codex", key_set: true, system_prompt: "",
      quote_color: "off", user_label: "You", assistant_label: "Grimoire",
    }),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
  },
}));

test("renders the chrome top bar with brand, nav, and connection status", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/GRIMOIRE/)).toBeInTheDocument();
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /campaigns/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
  expect(topbar.getByText(/openrouter · connected/i)).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /config/i })).toBeInTheDocument();
});
