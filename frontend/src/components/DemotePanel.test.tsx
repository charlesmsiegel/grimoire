import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DemotePanel } from "./DemotePanel";

vi.mock("../api/client", () => ({
  ApiError: class extends Error {
    constructor(public status: number, public detail: string, public kind?: string) {
      super(detail);
    }
  },
  api: {
    libraryDependents: vi.fn(),
    demoteFromLibrary: vi.fn(),
  },
}));

const { api, ApiError } = await import("../api/client");

const DEPS = [
  { id: "run", name: "The Long Run", has_copy: false },
  { id: "other", name: "Saltmarch Nights", has_copy: true },
];

function show(deps = DEPS, onDemoted?: () => void) {
  (api.libraryDependents as any).mockResolvedValue(deps);
  return render(<DemotePanel wid="w" kind="locations" id="saltmarch" onDemoted={onDemoted} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.demoteFromLibrary as any).mockResolvedValue({ copied_down: ["run"], dependents: ["run"] });
});

test("the destructive action is behind a first click", async () => {
  show();
  expect(await screen.findByRole("button", { name: "Remove from library…" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Remove and copy down/ })).toBeNull();
});

test("opening it names every campaign that reads the record", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));

  expect(await screen.findByText(/2 campaigns read this record/)).toBeTruthy();
  expect(screen.getByText("The Long Run")).toBeTruthy();
  // the one holding its own copy is marked, because it is the one the
  // copy-down does not have to rescue
  expect(screen.getByText("Saltmarch Nights (own copy)")).toBeTruthy();
});

test("copy-down is the default", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));

  expect(await screen.findByRole("checkbox")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Remove and copy down" }));

  await waitFor(() => expect(api.demoteFromLibrary)
    .toHaveBeenCalledWith("w", "locations", "saltmarch", { copy_down: true }));
});

test("unchecking it changes both the wording and the request", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  fireEvent.click(await screen.findByRole("checkbox"));

  expect(screen.getByText(/already have their own keep it/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove without copying down" }));

  await waitFor(() => expect(api.demoteFromLibrary)
    .toHaveBeenCalledWith("w", "locations", "saltmarch", { copy_down: false }));
});

test("opening it re-reads the dependents rather than trusting the mount", async () => {
  show();
  await waitFor(() => expect(api.libraryDependents).toHaveBeenCalledTimes(1));
  (api.libraryDependents as any).mockResolvedValue(
    [...DEPS, { id: "new", name: "Saltmarch Run", has_copy: false }]);

  fireEvent.click(screen.getByRole("button", { name: "Remove from library…" }));

  expect(await screen.findByText("Saltmarch Run")).toBeTruthy();
});

test("the destructive button is not live while that re-read is in flight", async () => {
  // otherwise the user can confirm against the PREVIOUS list and never see the
  // campaign they are about to take the record away from
  show();
  await waitFor(() => expect(api.libraryDependents).toHaveBeenCalledTimes(1));
  let settle: (v: unknown) => void = () => {};
  (api.libraryDependents as any).mockReturnValue(new Promise((r) => { settle = r; }));

  fireEvent.click(screen.getByRole("button", { name: "Remove from library…" }));

  expect(screen.getByText("checking which campaigns use this record…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Remove and copy down/ })).toBeNull();
  expect(screen.queryByText("The Long Run")).toBeNull();

  settle(DEPS);
  expect(await screen.findByRole("button", { name: "Remove and copy down" })).toBeTruthy();
});


test("a world with no campaigns says so rather than listing nothing", async () => {
  show([]);
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  expect(await screen.findByText("No campaign uses this world yet.")).toBeTruthy();
});

test("cancelling closes without calling anything", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  // wait for the CONFIRMATION to be up before grabbing its Cancel: the pending
  // "checking…" panel has a Cancel of its own, and clicking the node that one
  // left behind dispatches nothing at all
  await screen.findByRole("button", { name: "Remove and copy down" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(await screen.findByRole("button", { name: "Remove from library…" })).toBeTruthy();
  expect(api.demoteFromLibrary).not.toHaveBeenCalled();
});

test("a refusal is shown and the record is not reported gone", async () => {
  const onDemoted = vi.fn();
  show(DEPS, onDemoted);
  (api.demoteFromLibrary as any).mockRejectedValueOnce(
    new (ApiError as any)(409, "characters cannot be demoted", "library_move_refused"));

  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove and copy down" }));

  expect(await screen.findByText(/cannot be demoted/)).toBeTruthy();
  expect(onDemoted).not.toHaveBeenCalled();
});

test("a successful demote tells the parent so the rail can drop the row", async () => {
  const onDemoted = vi.fn();
  show(DEPS, onDemoted);
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove and copy down" }));

  await waitFor(() => expect(onDemoted).toHaveBeenCalled());
});

test("dependents that cannot be read render nothing rather than a broken panel", async () => {
  (api.libraryDependents as any).mockRejectedValue(new Error("offline"));
  const { container } = render(<DemotePanel wid="w" kind="locations" id="saltmarch" />);
  await waitFor(() => expect(api.libraryDependents).toHaveBeenCalled());
  expect(container.querySelector(".side-section")).toBeNull();
});

test("a refresh that fails leaves a way out instead of checking forever", async () => {
  show();
  await waitFor(() => expect(api.libraryDependents).toHaveBeenCalledTimes(1));
  (api.libraryDependents as any).mockRejectedValueOnce(
    new (ApiError as any)(500, "the world could not be read", undefined));

  fireEvent.click(screen.getByRole("button", { name: "Remove from library…" }));

  expect(await screen.findByText("the world could not be read")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  // and retrying really re-asks
  (api.libraryDependents as any).mockResolvedValue(DEPS);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "Remove and copy down" })).toBeTruthy();
});
