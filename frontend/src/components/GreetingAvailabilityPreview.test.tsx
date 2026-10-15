import { act, render, screen } from "@testing-library/react";
import { api, type GreetingAvailabilityPreview as PreviewRow } from "../api/client";
import { GreetingAvailabilityPreview } from "./GreetingAvailabilityPreview";

vi.mock("../api/client", () => ({ api: { previewGreetings: vi.fn() } }));

const opening: PreviewRow = {
  id: "open", name: "Open", available: true, reasons: [], pcless: false, location: "",
};
const blocked: PreviewRow = {
  id: "later", name: "Later", available: false,
  reasons: ["missing required tags", "predecessors not played (all)"], pcless: false, location: "",
};

function preview(world: string, tags: string[]) {
  return <GreetingAvailabilityPreview key={JSON.stringify([world, tags])} world={world} tags={tags} />;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api.previewGreetings).mockReset().mockResolvedValue([]);
});

afterEach(() => { vi.useRealTimers(); });

test("renders the server's available and blocked openings with every reason", async () => {
  vi.mocked(api.previewGreetings).mockResolvedValue([opening, blocked, {
    ...opening, id: "offscreen", name: "Offscreen", pcless: true,
  }]);
  render(preview("realm", ["sailor"]));
  expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  await act(() => vi.advanceTimersByTimeAsync(250));
  expect(screen.getByText("Open")).toBeInTheDocument();
  expect(screen.getByText("Later")).toBeInTheDocument();
  expect(screen.getByText(/missing required tags/)).toHaveTextContent("predecessors not played (all)");
  expect(screen.getByText(/Offscreen.*offscreen/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull(); // previewing cannot start an opening
});

test("debounces changes and sends only the settled world's proposed tags", async () => {
  const view = render(preview("realm", []));
  await act(() => vi.advanceTimersByTimeAsync(200));
  view.rerender(preview("saltmarch", ["sailor"]));
  await act(() => vi.advanceTimersByTimeAsync(249));
  expect(api.previewGreetings).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(api.previewGreetings).toHaveBeenCalledTimes(1);
  expect(api.previewGreetings).toHaveBeenLastCalledWith("saltmarch", ["sailor"]);
  expect(screen.getByText(/no greetings/i)).toBeInTheDocument();
});

test("a changed choice immediately clears the previous preview", async () => {
  vi.mocked(api.previewGreetings).mockResolvedValue([opening]);
  const view = render(preview("realm", []));
  await act(() => vi.advanceTimersByTimeAsync(250));
  expect(screen.getByText("Open")).toBeInTheDocument();
  view.rerender(preview("realm", ["sailor"]));
  expect(screen.queryByText("Open")).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
});

test.each(["success", "failure"])("ignores an old world's late %s", async (outcome) => {
  let resolveOld: (rows: PreviewRow[]) => void = () => {};
  let rejectOld: (err: Error) => void = () => {};
  const old = new Promise<PreviewRow[]>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
  vi.mocked(api.previewGreetings).mockReturnValueOnce(old).mockResolvedValueOnce([opening]);
  const view = render(preview("realm", []));
  await act(() => vi.advanceTimersByTimeAsync(250));
  view.rerender(preview("saltmarch", ["sailor"]));
  await act(() => vi.advanceTimersByTimeAsync(250));
  await act(async () => {
    if (outcome === "success") resolveOld([blocked]);
    else rejectOld(new Error("old world failed"));
    await old.catch(() => {});
  });
  expect(screen.getByText("Open")).toBeInTheDocument();
  expect(screen.queryByText("Later")).toBeNull();
  expect(screen.queryByText(/old world failed/)).toBeNull();
});

test("a failed request is distinguished from an empty world", async () => {
  vi.mocked(api.previewGreetings).mockRejectedValue(new Error("try later"));
  render(preview("realm", []));
  await act(() => vi.advanceTimersByTimeAsync(250));
  expect(screen.getByText(/could not preview openings/i)).toHaveTextContent("try later");
  expect(screen.queryByText(/no greetings/i)).toBeNull();
});

test("unmounting cancels the unsent request", async () => {
  const view = render(preview("realm", []));
  view.unmount();
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(api.previewGreetings).not.toHaveBeenCalled();
});
