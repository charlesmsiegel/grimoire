// The quick-reply editor follows the list/detail pattern (CLAUDE.md): a row
// opens read-only, Edit reveals the form, + New opens the form directly -- and
// every save is the whole set, sent with the digest it was read at.
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QuickReplyEditor } from "./QuickReplyEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { getQuickReplies: vi.fn(), setQuickReplies: vi.fn() } };
});
import { api, ApiError, type QuickReplySet } from "../api/client";

const WORLD = { kind: "world" as const, wid: "realm" };
const SET: QuickReplySet = { version: 1, digest: "d1", replies: [
  { id: "a", label: "Look around", kind: "send", text: "I take in the **room**.", mode: "send" },
  { id: "b", label: "Search", kind: "roll", notation: "1d20+2" }] };

beforeEach(() => {
  vi.clearAllMocks();
  (api.getQuickReplies as any).mockResolvedValue(SET);
  (api.setQuickReplies as any).mockImplementation(
    (_scope: unknown, replies: any[], _expect: string) => Promise.resolve({
      version: 1, digest: "d2",
      replies: replies.map((r, i) => (r.id ? r : { ...r, id: `minted${i}` })) }));
});

async function openWorld() {
  render(<QuickReplyEditor scope={WORLD} />);
  return screen.findByRole("button", { name: "Look around" });
}

test("clicking a row shows the read-only view with its sidebar", async () => {
  fireEvent.click(await openWorld());
  expect(await screen.findByRole("heading", { name: "Look around" })).toBeInTheDocument();
  expect(screen.getByText("room").tagName).toBe("STRONG");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(document.querySelector("textarea")).toBeNull();
  const side = document.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Speak")).toHaveClass("chip", "on");
  expect(within(side).getByText("Send at once")).toHaveClass("chip", "on");
  expect(within(side).getByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("a roll's view shows its notation", async () => {
  await openWorld();
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  const side = (await screen.findByRole("heading", { name: "Search" })) && document.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Roll")).toHaveClass("chip", "on");
  expect(within(side).getByText("1d20+2")).toBeInTheDocument();
});

test("Edit reveals the form", async () => {
  fireEvent.click(await openWorld());
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Label")).toHaveValue("Look around");
  expect(screen.getByLabelText("Text")).toHaveValue("I take in the **room**.");
});

test("+ New opens the form directly", async () => {
  await openWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ New quick reply" }));
  expect(screen.getByLabelText("Label")).toHaveValue("");
  expect(document.querySelector(".editor-list .row.active")).toBeNull();
});

test("save PUTs the whole set with the digest it read", async () => {
  fireEvent.click(await openWorld());
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Look closer" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setQuickReplies).toHaveBeenCalledWith(WORLD, [
    { id: "a", label: "Look closer", kind: "send", text: "I take in the **room**.", mode: "send" },
    SET.replies[1]], "d1"));
  expect(await screen.findByRole("heading", { name: "Look closer" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Label")).toBeNull();
});

test("a new reply is saved without an id and opened under the one it was given", async () => {
  await openWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ New quick reply" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Summarize" } });
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "task" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setQuickReplies).toHaveBeenCalledWith(WORLD, [
    ...SET.replies, { label: "Summarize", kind: "task", task: "rolling_summary" }], "d1"));
  expect(await screen.findByRole("heading", { name: "Summarize" })).toBeInTheDocument();
  expect(document.querySelector(".editor-list .row.active")).toHaveTextContent("Summarize");
});

test("the form's fields follow the kind", async () => {
  await openWorld();
  fireEvent.click(screen.getByRole("button", { name: "+ New quick reply" }));
  expect(screen.getByLabelText("Text")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "roll" } });
  expect(screen.getByLabelText("Notation")).toBeInTheDocument();
  expect(screen.getByLabelText("Roll label")).toBeInTheDocument();
  expect(screen.queryByLabelText("Text")).toBeNull();
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "opener" } });
  expect(screen.queryByLabelText("Notation")).toBeNull();
  expect(screen.queryByLabelText("Task")).toBeNull();
});

test("↓ reorders and saves", async () => {
  await openWorld();
  expect(screen.getByRole("button", { name: "Move Look around up" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Move Search down" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Move Look around down" }));
  await waitFor(() => expect(api.setQuickReplies).toHaveBeenCalledWith(
    WORLD, [SET.replies[1], SET.replies[0]], "d1"));
});

test("Delete removes the reply and saves", async () => {
  fireEvent.click(await openWorld());
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.setQuickReplies).toHaveBeenCalledWith(WORLD, [SET.replies[1]], "d1"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Look around" })).toBeNull());
});

test("a 400 is shown in the form", async () => {
  (api.setQuickReplies as any).mockRejectedValue(new ApiError(400, "can't read dice notation 'q'", "invalid_quick_reply"));
  await openWorld();
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Notation"), { target: { value: "q" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("can't read dice notation 'q'")).toBeInTheDocument();
  expect(screen.getByLabelText("Notation")).toHaveValue("q");
});

test("a 409 re-reads and keeps the draft", async () => {
  (api.setQuickReplies as any).mockRejectedValue(new ApiError(409, "the set changed", "set_changed"));
  fireEvent.click(await openWorld());
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/changed elsewhere and has been re-read/)).toBeInTheDocument();
  expect(api.getQuickReplies).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText("Label")).toHaveValue("My draft");
});
