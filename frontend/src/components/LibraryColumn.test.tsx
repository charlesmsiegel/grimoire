import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import LibraryColumn from "./LibraryColumn";
import LibraryView from "../routes/LibraryView";

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(), listModules: vi.fn(), listStyles: vi.fn(),
    listClimates: vi.fn(), listConnections: vi.fn(),
  },
}));
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";

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
  vi.mocked(api.listWorlds).mockResolvedValue(fixture([{ id: "realm" }, { id: "saltmarch" }]));
  vi.mocked(api.listModules).mockResolvedValue(fixture([{ id: "d20" }]));
  vi.mocked(api.listStyles).mockResolvedValue(fixture([{ id: "a" }, { id: "b" }, { id: "c" }]));
  vi.mocked(api.listClimates).mockResolvedValue(fixture({ climates: [{ id: "temperate" }, { id: "arid" }] }));
  vi.mocked(api.listConnections).mockResolvedValue(fixture([{ id: "openrouter" }]));
});

test("a phone section scrolls its active link into the library strip", async () => {
  const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
  try {
    renderColumn("/climates");
    await screen.findByRole("link", { name: /Climates/ });
    expect(scroll).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
  } finally {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    if (previous) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", previous);
    else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});

test("offers every section as a link to the page that already owns it", async () => {
  renderColumn();
  for (const [name, href] of [
    ["Worlds", "/worlds"], ["Modules", "/modules"], ["Styles", "/styles"],
    ["Climates", "/climates"],
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
});

test("Providers left the library for Settings, Inference", async () => {
  renderColumn();
  await screen.findByRole("link", { name: /worlds/i });
  expect(screen.queryByRole("link", { name: /providers/i })).toBeNull();
});

test("retired response presets have no library row", async () => {
  renderColumn();
  await screen.findByRole("link", { name: /worlds/i });
  expect(screen.queryByRole("link", { name: /response presets/i })).toBeNull();
});

test("one section failing to load leaves the others counted", async () => {
  vi.mocked(api.listModules).mockRejectedValue(new Error("boom"));
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
