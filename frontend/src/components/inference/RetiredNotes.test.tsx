import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { api, type RetiredNote } from "../../api/client";
import { RetiredNotes } from "./RetiredNotes";

vi.mock("../../api/client", async () => ({
  ...(await vi.importActual<typeof import("../../api/client")>("../../api/client")),
  api: { dismissRetiredNote: vi.fn() },
}));

const note = (over: Partial<RetiredNote> = {}): RetiredNote => ({
  id: "a1", scope: "global", scope_name: "", subject: "summary", provider_id: "glm",
  effort: "high", kind: "route_preset",
  text: "On the Summary route, the preset “Cold” sets no reasoning effort, so the GLM "
    + "provider “glm” no longer sends its reasoning effort (high) there — this was not "
    + "carried over.",
  ...over,
});

beforeEach(() => vi.clearAllMocks());

test("renders nothing when nothing was lost", () => {
  const { container } = render(<RetiredNotes notes={[]} onDismissed={() => {}} />);
  expect(container).toBeEmptyDOMElement();
});

test("shows each note, where it applies, worded as permanent", () => {
  render(<RetiredNotes onDismissed={() => {}} notes={[
    note(),
    note({ id: "b2", scope: "campaign:saltmarch", scope_name: "Saltmarch",
           text: "On the Tracker route in this campaign, … — this was not carried over." }),
  ]} />);
  const region = screen.getByRole("region", { name: "Not carried over" });
  const items = within(region).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Library settings:");
  expect(items[0]).toHaveTextContent("— this was not carried over.");
  expect(items[1]).toHaveTextContent("Campaign “Saltmarch”:");
  expect(region).not.toHaveTextContent(/for later/i);
});

test("Dismiss records it on the server, then drops the row", async () => {
  (api.dismissRetiredNote as any).mockResolvedValue({ ok: true });
  const onDismissed = vi.fn();
  render(<RetiredNotes notes={[note(), note({ id: "b2" })]} onDismissed={onDismissed} />);
  fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
  await vi.waitFor(() => expect(onDismissed).toHaveBeenCalledWith("a1"));
  expect(api.dismissRetiredNote).toHaveBeenCalledWith("a1");
});

test("a dismiss the server refuses keeps the row and says why", async () => {
  (api.dismissRetiredNote as any).mockRejectedValue(new Error("record unreadable"));
  const onDismissed = vi.fn();
  render(<RetiredNotes notes={[note()]} onDismissed={onDismissed} />);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(await screen.findByText(/record unreadable/)).toBeInTheDocument();
  expect(onDismissed).not.toHaveBeenCalled();
});
