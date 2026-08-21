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

  expect(screen.getByText(/deleted everywhere/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove everywhere" }));

  await waitFor(() => expect(api.demoteFromLibrary)
    .toHaveBeenCalledWith("w", "locations", "saltmarch", { copy_down: false }));
});

test("a world with no campaigns says so rather than listing nothing", async () => {
  show([]);
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  expect(await screen.findByText("No campaign uses this world yet.")).toBeTruthy();
});

test("cancelling closes without calling anything", async () => {
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Remove from library…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

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
