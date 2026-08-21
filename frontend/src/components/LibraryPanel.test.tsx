import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LibraryPanel } from "./LibraryPanel";

vi.mock("../api/client", () => ({
  // A real class: the panel branches on `instanceof ApiError` to tell a push
  // conflict from any other failure. Declared inside the factory because
  // `vi.mock` is hoisted above every top-level statement.
  ApiError: class extends Error {
    constructor(public status: number, public detail: string, public kind?: string) {
      super(detail);
    }
  },
  api: {
    libraryStatus: vi.fn(),
    promoteToLibrary: vi.fn(),
    pushToLibrary: vi.fn(),
  },
}));

const { api, ApiError } = await import("../api/client");

const STATUS = {
  local: { in_library: false, diverged: false, can_promote: true, can_push: false },
  diverged: { in_library: true, diverged: true, can_promote: false, can_push: true },
  synced: { in_library: true, diverged: false, can_promote: false, can_push: false },
  inherited: { in_library: false, diverged: false, can_promote: false, can_push: false },
};

function show(status: (typeof STATUS)[keyof typeof STATUS], onMoved?: () => void) {
  (api.libraryStatus as any).mockResolvedValue(status);
  return render(<LibraryPanel cid="run" kind="locations" id="saltmarch" onMoved={onMoved} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.promoteToLibrary as any).mockResolvedValue({ ok: true });
  (api.pushToLibrary as any).mockResolvedValue({ ok: true });
});

test("a campaign-local record offers publishing", async () => {
  show(STATUS.local);
  expect(await screen.findByRole("button", { name: "Publish to library" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save to library" })).toBeNull();
});

test("publishing calls promote", async () => {
  show(STATUS.local);
  fireEvent.click(await screen.findByRole("button", { name: "Publish to library" }));
  await waitFor(() =>
    expect(api.promoteToLibrary).toHaveBeenCalledWith("run", "locations", "saltmarch"));
});

test("a diverged copy offers saving back, not publishing", async () => {
  show(STATUS.diverged);
  expect(await screen.findByRole("button", { name: "Save to library" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Publish to library" })).toBeNull();
});

test("saving calls push without forcing", async () => {
  show(STATUS.diverged);
  fireEvent.click(await screen.findByRole("button", { name: "Save to library" }));
  await waitFor(() =>
    expect(api.pushToLibrary).toHaveBeenCalledWith("run", "locations", "saltmarch"));
});

test("a record identical to the library says so instead of showing a button", async () => {
  show(STATUS.synced);
  expect(await screen.findByText("in sync with the library")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});

test("a record the campaign only inherits shows nothing at all", async () => {
  const { container } = show(STATUS.inherited);
  await waitFor(() => expect(api.libraryStatus).toHaveBeenCalled());
  expect(container.querySelector(".side-section")).toBeNull();
});

test("a push conflict offers an explicit overwrite rather than retrying", async () => {
  show(STATUS.diverged);
  (api.pushToLibrary as any).mockRejectedValueOnce(
    new (ApiError as any)(409, "changed in the library since this campaign copied it",
                          "push_conflict"));

  fireEvent.click(await screen.findByRole("button", { name: "Save to library" }));

  expect(await screen.findByText(/changed in the library/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Overwrite the library anyway" })).toBeTruthy();
  // the plain save is gone: the next click has to be the deliberate one
  expect(screen.queryByRole("button", { name: "Save to library" })).toBeNull();
});

test("the overwrite forces the push", async () => {
  show(STATUS.diverged);
  (api.pushToLibrary as any).mockRejectedValueOnce(
    new (ApiError as any)(409, "moved", "push_conflict"));
  fireEvent.click(await screen.findByRole("button", { name: "Save to library" }));

  fireEvent.click(await screen.findByRole("button", { name: "Overwrite the library anyway" }));

  await waitFor(() =>
    expect(api.pushToLibrary).toHaveBeenLastCalledWith("run", "locations", "saltmarch", true));
});

test("a refusal that is not a conflict is shown as a message with no overwrite", async () => {
  show(STATUS.local);
  (api.promoteToLibrary as any).mockRejectedValueOnce(
    new (ApiError as any)(409, "the library already has locations/saltmarch",
                          "library_move_refused"));

  fireEvent.click(await screen.findByRole("button", { name: "Publish to library" }));

  expect(await screen.findByText(/already has/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Overwrite the library anyway" })).toBeNull();
});

test("a completed move re-reads the status and tells the parent", async () => {
  const onMoved = vi.fn();
  show(STATUS.local, onMoved);
  fireEvent.click(await screen.findByRole("button", { name: "Publish to library" }));

  await waitFor(() => expect(onMoved).toHaveBeenCalled());
  // once on mount, once after the move
  expect((api.libraryStatus as any).mock.calls.length).toBeGreaterThanOrEqual(2);
});

test("a status that cannot be read renders nothing rather than a broken panel", async () => {
  (api.libraryStatus as any).mockRejectedValue(new Error("offline"));
  const { container } = render(<LibraryPanel cid="run" kind="locations" id="saltmarch" />);
  await waitFor(() => expect(api.libraryStatus).toHaveBeenCalled());
  expect(container.querySelector(".side-section")).toBeNull();
});
