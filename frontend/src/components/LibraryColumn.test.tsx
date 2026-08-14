import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import LibraryColumn from "./LibraryColumn";
import LibraryView from "../routes/LibraryView";

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(), listModules: vi.fn(), listStyles: vi.fn(),
    listResponsePresets: vi.fn(), listClimates: vi.fn(), listConnections: vi.fn(),
  },
}));
import { api } from "../api/client";

function Probe() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderColumn(at = "/worlds") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <LibraryColumn />
      <Routes><Route path="*" element={<Probe />} /></Routes>
    </MemoryRouter>,
  );
}

const row = (name: RegExp) => screen.getByRole("link", { name });

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([{ id: "realm" }, { id: "saltmarch" }]);
  (api.listModules as any).mockResolvedValue([{ id: "d20" }]);
  (api.listStyles as any).mockResolvedValue([{ id: "a" }, { id: "b" }, { id: "c" }]);
  (api.listResponsePresets as any).mockResolvedValue([]);
  (api.listClimates as any).mockResolvedValue({ climates: [{ id: "temperate" }, { id: "arid" }] });
  (api.listConnections as any).mockResolvedValue([{ id: "openrouter" }]);
});

test("offers every section as a link to the page that already owns it", async () => {
  renderColumn();
  for (const [name, href] of [
    ["Worlds", "/worlds"], ["Modules", "/modules"], ["Styles", "/styles"],
    ["Response Presets", "/response-presets"], ["Climates", "/climates"],
    // Connections used to sit outside the library entirely, beside it in the
    // nav rail. It is a thing a campaign is built from like the other five.
    ["Connections", "/connections"],
  ] as const) {
    expect(await screen.findByRole("link", { name: new RegExp(name, "i") }))
      .toHaveAttribute("href", href);
  }
});

test("lights the section you are in, including its record pages", async () => {
  renderColumn("/worlds/saltmarch");
  await waitFor(() => expect(row(/worlds/i)).toHaveClass("active"));
  expect(row(/modules/i)).not.toHaveClass("active");
});

test("switching section is one click, with no hub in between", async () => {
  renderColumn();
  fireEvent.click(await screen.findByRole("link", { name: /Modules/i }));
  expect(screen.getByTestId("where")).toHaveTextContent("/modules");
});

test("counts each section from the list endpoint that already serves it", async () => {
  renderColumn();
  await waitFor(() => expect(row(/worlds/i)).toHaveTextContent("2"));
  expect(row(/modules/i)).toHaveTextContent("1");
  expect(row(/styles/i)).toHaveTextContent("3");
  expect(row(/climates/i)).toHaveTextContent("2");
  expect(row(/connections/i)).toHaveTextContent("1");
});

test("an empty section counts zero rather than going blank", async () => {
  renderColumn();
  await waitFor(() => expect(row(/response presets/i)).toHaveTextContent("0"));
});

test("one section failing to load leaves the others counted", async () => {
  (api.listModules as any).mockRejectedValue(new Error("boom"));
  renderColumn();
  await waitFor(() => expect(row(/worlds/i)).toHaveTextContent("2"));
  // an unknown count is a dash, never a made-up zero
  expect(row(/modules/i)).toHaveTextContent("—");
  expect(row(/modules/i)).toHaveAttribute("href", "/modules");
});

test("/library keeps working as a link, and lands in the first section", () => {
  render(
    <MemoryRouter initialEntries={["/library"]}>
      <Routes>
        <Route path="/library" element={<LibraryView />} />
        <Route path="*" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );
  // The card hub it used to render is gone; the six sections are the column.
  expect(screen.getByTestId("where")).toHaveTextContent("/worlds");
});
