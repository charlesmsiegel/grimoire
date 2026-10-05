import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorldAbout } from "./WorldAbout";

vi.mock("../api/client", () => ({ api: { getWorld: vi.fn(), updateWorld: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getWorld as any).mockResolvedValue({
    meta: { id: "realm", name: "Realm", genre: "Coastal gothic", tone: "Wry dread", themes: ["salt", "debt"] },
    body: "A **drowned** coast.\n", counts: {} });
  (api.updateWorld as any).mockResolvedValue({ id: "realm", name: "Realm" });
});

test("the profile reads as a rendered view, with no form until Edit", async () => {
  render(<WorldAbout wid="realm" />);
  expect(await screen.findByText("drowned")).toBeInTheDocument();     // markdown rendered
  expect(screen.getByText(/Genre: Coastal gothic · Tone: Wry dread/)).toBeInTheDocument();
  expect(screen.getByText("salt")).toHaveClass("chip");
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("Edit opens the form; Save writes the profile and returns to the view", async () => {
  render(<WorldAbout wid="realm" />);
  // what the view shows after a save is the server's re-read, not the draft
  (api.getWorld as any).mockResolvedValue({
    meta: { id: "realm", name: "Realm", genre: "Coastal gothic", tone: "Bright", themes: ["salt", "debt"] },
    body: "A **drowned** coast.\n", counts: {} });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Themes")).toHaveValue("salt, debt");
  fireEvent.change(screen.getByLabelText("Tone"), { target: { value: "Bright" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateWorld).toHaveBeenCalledWith("realm", {
    genre: "Coastal gothic", tone: "Bright", themes: ["salt", "debt"], description: "A **drowned** coast." }));
  expect(await screen.findByText(/Tone: Bright/)).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("Cancel discards the draft", async () => {
  render(<WorldAbout wid="realm" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Something else" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByText(/Genre: Coastal gothic/)).toBeInTheDocument();
  expect(api.updateWorld).not.toHaveBeenCalled();
});

test("a world with no profile says what one is for", async () => {
  (api.getWorld as any).mockResolvedValue({ meta: { id: "realm", name: "Realm" }, body: "", counts: {} });
  render(<WorldAbout wid="realm" />);
  expect(await screen.findByText(/No description yet/)).toBeInTheDocument();
});

test("a failed save keeps the form and says why", async () => {
  (api.updateWorld as any).mockRejectedValue(new Error("world kept changing"));
  render(<WorldAbout wid="realm" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/world kept changing/)).toBeInTheDocument();
  expect(screen.getByLabelText("Genre")).toBeInTheDocument();
});

test("a save still in flight when the world changes does not land on the next world", async () => {
  let releasePut: (v: unknown) => void = () => {};
  (api.updateWorld as any).mockImplementation(() => new Promise((r) => { releasePut = r; }));
  const { rerender } = render(<WorldAbout wid="realm" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Realm's genre" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  (api.getWorld as any).mockImplementation(async (wid: string) => wid === "saltmarch"
    ? { meta: { id: "saltmarch", name: "Saltmarch", genre: "Harbour noir", tone: "", themes: [] },
        body: "", counts: {} }
    : { meta: { id: "realm", name: "Realm", genre: "Realm's genre", tone: "", themes: [] },
        body: "", counts: {} });
  rerender(<WorldAbout wid="saltmarch" />);
  expect(await screen.findByText(/Genre: Harbour noir/)).toBeInTheDocument();
  releasePut({ id: "realm", name: "Realm" });
  await waitFor(() => expect(api.getWorld).toHaveBeenCalledWith("realm"));
  expect(screen.getByText(/Genre: Harbour noir/)).toBeInTheDocument();
  expect(screen.queryByText(/Realm's genre/)).not.toBeInTheDocument();
});
