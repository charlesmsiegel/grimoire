import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import LibraryView from "./LibraryView";

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(), listModules: vi.fn(), listStyles: vi.fn(),
    listResponsePresets: vi.fn(), listClimates: vi.fn(),
  },
}));
import { api } from "../api/client";

function Probe() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderHub() {
  return render(
    <MemoryRouter initialEntries={["/library"]}>
      <LibraryView />
      <Routes><Route path="*" element={<Probe />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([{ id: "realm" }, { id: "saltmarch" }]);
  (api.listModules as any).mockResolvedValue([{ id: "d20" }]);
  (api.listStyles as any).mockResolvedValue([{ id: "a" }, { id: "b" }, { id: "c" }]);
  (api.listResponsePresets as any).mockResolvedValue([]);
  (api.listClimates as any).mockResolvedValue({ climates: [{ id: "temperate" }, { id: "arid" }] });
});

test("offers every library as a link to the page that already owns it", async () => {
  renderHub();
  for (const [name, href] of [
    ["Worlds", "/worlds"], ["Modules", "/modules"], ["Styles", "/styles"],
    ["Response Presets", "/response-presets"], ["Climates", "/climates"],
  ] as const) {
    expect(await screen.findByRole("link", { name: new RegExp(name, "i") }))
      .toHaveAttribute("href", href);
  }
});

test("navigates into a library when its card is clicked", async () => {
  renderHub();
  fireEvent.click(await screen.findByRole("link", { name: /Worlds/i }));
  expect(screen.getByTestId("where")).toHaveTextContent("/worlds");
});

test("counts each library from the list endpoint that already serves it", async () => {
  renderHub();
  await waitFor(() => expect(screen.getByTestId("count-worlds")).toHaveTextContent("2 worlds"));
  expect(screen.getByTestId("count-modules")).toHaveTextContent("1 module");
  expect(screen.getByTestId("count-styles")).toHaveTextContent("3 styles");
  expect(screen.getByTestId("count-climates")).toHaveTextContent("2 climates");
});

test("an empty library counts zero rather than going blank", async () => {
  renderHub();
  await waitFor(() =>
    expect(screen.getByTestId("count-response-presets")).toHaveTextContent("0 response presets"));
});

test("one library failing to load leaves the others counted", async () => {
  (api.listModules as any).mockRejectedValue(new Error("boom"));
  renderHub();
  await waitFor(() => expect(screen.getByTestId("count-worlds")).toHaveTextContent("2 worlds"));
  // an unknown count is a dash, never a made-up zero
  expect(screen.getByTestId("count-modules")).toHaveTextContent("—");
  expect(screen.getByRole("link", { name: /Modules/i })).toHaveAttribute("href", "/modules");
});
