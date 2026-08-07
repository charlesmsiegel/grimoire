import { render, screen } from "@testing-library/react";
import StatusBar from "./StatusBar";

const BASE = { connection: "OpenRouter", ready: true, model: "vendor/model-x", context: null };

test("names the connection and reports it connected", () => {
  render(<StatusBar {...BASE} />);
  expect(screen.getByTestId("status-connection")).toHaveTextContent(/OpenRouter/);
  expect(screen.getByTestId("status-connection")).toHaveTextContent(/CONNECTED/i);
});

test("reports NOT READY for a connection that cannot be used yet", () => {
  render(<StatusBar {...BASE} ready={false} />);
  expect(screen.getByTestId("status-connection")).toHaveTextContent(/NOT READY/i);
});

test("reports NO CONNECTION when none is active", () => {
  render(<StatusBar {...BASE} connection="" ready={false} />);
  expect(screen.getByTestId("status-connection")).toHaveTextContent(/NO CONNECTION/i);
});

test("names the active model", () => {
  render(<StatusBar {...BASE} />);
  expect(screen.getByTestId("status-model")).toHaveTextContent("vendor/model-x");
});

test("dashes the model when the active connection has none chosen", () => {
  render(<StatusBar {...BASE} model="" />);
  expect(screen.getByTestId("status-model")).toHaveTextContent("—");
});

test("reserves token budget, queue and drift as dashes — there is no data for them yet", () => {
  render(<StatusBar {...BASE} />);
  for (const slot of ["tokens", "queue", "drift"]) {
    const cell = screen.getByTestId(`status-${slot}`);
    expect(cell).toHaveTextContent("—");
    // the slot says why it is empty rather than implying a real zero
    expect(cell.getAttribute("title")).toMatch(/not .*(yet|available)/i);
  }
});

test("a reserved slot renders a real value once one is supplied", () => {
  // #126 / #174 / #59 fill these in later; the bar must not need a rewrite
  render(<StatusBar {...BASE} budget="12k/32k" queue="2" drift="LOW" />);
  expect(screen.getByTestId("status-tokens")).toHaveTextContent("12k/32k");
  expect(screen.getByTestId("status-queue")).toHaveTextContent("2");
  expect(screen.getByTestId("status-drift")).toHaveTextContent("LOW");
});

test("omits the location cell entirely when no page has published one", () => {
  render(<StatusBar {...BASE} />);
  expect(screen.queryByTestId("status-context")).not.toBeInTheDocument();
});

test("names the campaign and the open scene when a page publishes them", () => {
  render(<StatusBar {...BASE} context={{ campaign: "Saltmarch", scene: "The Long Tide" }} />);
  const cell = screen.getByTestId("status-context");
  expect(cell).toHaveTextContent("Saltmarch");
  expect(cell).toHaveTextContent("The Long Tide");
});

test("names the campaign alone when no scene is open", () => {
  render(<StatusBar {...BASE} context={{ campaign: "Saltmarch", scene: "" }} />);
  const cell = screen.getByTestId("status-context");
  expect(cell).toHaveTextContent("Saltmarch");
  expect(cell.textContent).not.toContain("▸");
});
