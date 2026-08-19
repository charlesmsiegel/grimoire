import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { IncomingReview } from "./IncomingReview";

vi.mock("../api/client", () => ({
  api: { getIncoming: vi.fn(), acceptIncoming: vi.fn(), rejectIncoming: vi.fn() },
}));
import { api } from "../api/client";

const HARBOR = {
  ref: { kind: "locations", id: "saltmarch-harbor" },
  status: "update",
  world: { name: "Saltmarch Harbor", body: "The harbour is blockaded." },
  mine: { name: "Saltmarch Harbor", body: "A busy port town." },
};

const card = (description: string, firstMes: string) => ({
  spec: "chara_card_v3", spec_version: "3.0",
  data: { name: "Seraphine", description, first_mes: firstMes },
});

const SERAPHINE = {
  ref: { kind: "characters", id: "seraphine" },
  status: "conflict",
  world: { name: "Seraphine", version: "v2", card: card("Keeps the tide ledger.", "You again.") },
  mine: { name: "Seraphine", version: "v2", card: card("Keeps two tide ledgers.", "You again.") },
};

/** `new` is in the API's vocabulary and in the world-side counts, but no pass in
 *  `incoming()` emits it today -- kept because the panel handles it rather than
 *  assuming it away. The shape the backend really does send for "no campaign
 *  copy" is ORPHAN below. */
const MARA = {
  ref: { kind: "pcs", id: "mara" },
  status: "new",
  world: {
    name: "Mara", version: "v1",
    persona: { name: "Mara", pronouns: "they/them", summary: "A cartographer.",
               description: "Draws the coast nobody asked for." },
  },
};

/** What `_actor_incoming` sends when the campaign's copy of a locked version is
 *  gone: `mine_h` is None, which does not match the base either, so the item is
 *  graded a *conflict* with no `mine` at all (`store/sync.py`). */
const ORPHAN = {
  ref: { kind: "characters", id: "winifred" },
  status: "conflict",
  world: { name: "Winifred", version: "v1", card: card("Harbourmaster.", "Ledger's shut.") },
};

/** A card change outside the prose fields this view compares -- a new greeting,
 *  a tag, the embedded lorebook. Both sides render identically, so the panel has
 *  to say why rather than let it read as no change. */
const UNSEEN = {
  ref: { kind: "characters", id: "seraphine" },
  status: "conflict",
  world: { name: "Seraphine", version: "v2", card: card("Keeps the tide ledger.", "You again.") },
  mine: { name: "Seraphine", version: "v2", card: card("Keeps the tide ledger.", "You again.") },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getIncoming as any).mockResolvedValue([]);
  (api.acceptIncoming as any).mockResolvedValue({ ok: true });
  (api.rejectIncoming as any).mockResolvedValue({ ok: true });
});

async function renderPanel() {
  render(<IncomingReview cid="c1" />);
  await act(async () => {});
}

const row = (name: RegExp) => screen.getByRole("button", { name });

test("lists each incoming item with its name, kind and status badge", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR, SERAPHINE, MARA]);
  await renderPanel();
  expect(row(/Saltmarch Harbor · Location update/)).toBeInTheDocument();
  expect(row(/Seraphine · Character conflict/)).toBeInTheDocument();
  expect(row(/Mara · PC new/)).toBeInTheDocument();
  // Only conflict is painted: the badge says which row cannot just be accepted.
  // Read off the rail rows, since the open item names its status in the sidebar too.
  const badge = (name: RegExp, status: string) => within(row(name)).getByText(status);
  expect(badge(/Seraphine/, "conflict").className).toContain("incoming-conflict");
  expect(badge(/Saltmarch Harbor/, "update").className).not.toContain("incoming-conflict");
});

test("an entity update shows world and campaign bodies side by side", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR]);
  await renderPanel();
  // Name and Body, each in two columns.
  expect(screen.getAllByText("From the world")).toHaveLength(2);
  expect(screen.getAllByText("In this campaign")).toHaveLength(2);
  expect(screen.getByText("The harbour is blockaded.")).toBeInTheDocument();
  expect(screen.getByText("A busy port town.")).toBeInTheDocument();
});

test("an entity renamed in the world reads as a rename, not as an empty change", async () => {
  (api.getIncoming as any).mockResolvedValue([{
    ref: { kind: "locations", id: "saltmarch-harbor" }, status: "update",
    world: { name: "Saltmarch Quay", body: "A busy port town." },
    mine: { name: "Saltmarch Harbor", body: "A busy port town." },
  }]);
  await renderPanel();
  const field = screen.getByRole("heading", { name: "Name" }).parentElement!;
  expect(within(field).getByText("Saltmarch Quay")).toBeInTheDocument();
  expect(within(field).getByText("Saltmarch Harbor")).toBeInTheDocument();
  // The bodies match, but the change is not invisible, so no notice is due.
  expect(screen.queryByText(/Every field below is identical/)).not.toBeInTheDocument();
});

test("a plot map is compared as text, with no name row to compare", async () => {
  (api.getIncoming as any).mockResolvedValue([{
    ref: { kind: "plotmap", id: "plotmap" }, status: "update",
    world: { name: "Plot map", body: '{"threads": ["the blockade"]}' },
    mine: { name: "Plot map", body: "{}" },
  }]);
  await renderPanel();
  expect(screen.getByRole("button", { name: /Plot map · Plot map/ })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Name" })).not.toBeInTheDocument();
  // JSON is not markdown: it renders verbatim rather than through the renderer.
  const json = screen.getByText('{"threads": ["the blockade"]}');
  expect(json.tagName).toBe("PRE");
});

test("a character conflict renders both columns as labelled card fields", async () => {
  (api.getIncoming as any).mockResolvedValue([SERAPHINE]);
  await renderPanel();
  expect(screen.getByRole("heading", { name: "Description" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "First message" })).toBeInTheDocument();
  expect(screen.getByText("Keeps the tide ledger.")).toBeInTheDocument();
  expect(screen.getByText("Keeps two tide ledgers.")).toBeInTheDocument();
  // One column per side, per compared field (name, description, first message)
  // — and the field both sides agree on is still shown, because a reader
  // checking a conflict needs the whole card.
  expect(screen.getAllByText("From the world")).toHaveLength(3);
  expect(screen.getAllByText("In this campaign")).toHaveLength(3);
  // No card field the card does not fill is framed and left blank.
  expect(screen.queryByRole("heading", { name: "Scenario" })).not.toBeInTheDocument();
});

test("a new item renders persona fields with only the world side", async () => {
  (api.getIncoming as any).mockResolvedValue([MARA]);
  await renderPanel();
  expect(screen.getByRole("heading", { name: "Pronouns" })).toBeInTheDocument();
  expect(screen.getByText("they/them")).toBeInTheDocument();
  // One world column per persona field, and no campaign column at all.
  expect(screen.getAllByText("From the world")).toHaveLength(4);
  expect(screen.queryByText("In this campaign")).not.toBeInTheDocument();
});

test("accepting sends that item's ref and refetches, so the row drops off", async () => {
  (api.getIncoming as any).mockResolvedValueOnce([HARBOR, SERAPHINE])
                          .mockResolvedValueOnce([SERAPHINE]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(api.acceptIncoming).toHaveBeenCalledWith(
    "c1", [{ kind: "locations", id: "saltmarch-harbor" }]));
  await waitFor(() => expect(api.getIncoming).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button", { name: /Saltmarch Harbor/ })).not.toBeInTheDocument();
  // The resolved row took the selection with it, so the panel opens the next one
  // rather than showing an empty body.
  expect(screen.getByRole("heading", { name: /Seraphine/ })).toBeInTheDocument();
});

test("rejecting sends that item's ref to the reject route", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(api.rejectIncoming).toHaveBeenCalledWith(
    "c1", [{ kind: "locations", id: "saltmarch-harbor" }]));
  expect(api.acceptIncoming).not.toHaveBeenCalled();
});

test("the selected row is the one whose detail is shown", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR, SERAPHINE]);
  await renderPanel();
  fireEvent.click(row(/Seraphine · Character conflict/));
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(api.acceptIncoming).toHaveBeenCalledWith(
    "c1", [{ kind: "characters", id: "seraphine" }]));
});

test("accept all confirms first, naming what it would overwrite", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR, SERAPHINE]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Accept all" }));
  // Nothing is sent on the first click: accepting deletes the campaign's copy
  // and no journal entry stands behind that.
  expect(api.acceptIncoming).not.toHaveBeenCalled();
  expect(screen.getByText(/Replace 2 records in this campaign, discarding 1 the campaign changed itself/))
    .toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Yes, accept all" }));
  await waitFor(() => expect(api.acceptIncoming).toHaveBeenCalledWith(
    "c1", [{ kind: "locations", id: "saltmarch-harbor" },
           { kind: "characters", id: "seraphine" }]));
});

test("cancelling a bulk action sends nothing", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR, SERAPHINE]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Reject all" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.rejectIncoming).not.toHaveBeenCalled();
  expect(api.acceptIncoming).not.toHaveBeenCalled();
  // and the bulk actions come back rather than leaving the header stuck
  expect(screen.getByRole("button", { name: "Reject all" })).toBeInTheDocument();
});

test("a conflict with no campaign copy shows one column and says so", async () => {
  (api.getIncoming as any).mockResolvedValue([ORPHAN]);
  await renderPanel();
  expect(screen.queryByText("In this campaign")).not.toBeInTheDocument();
  // Not "both sides changed": there is no copy here to have changed.
  expect(screen.getByText(/no copy of its own/)).toBeInTheDocument();
  expect(screen.queryByText(/both sides changed/)).not.toBeInTheDocument();
});

test("a change outside the compared fields is named, not passed off as no change", async () => {
  (api.getIncoming as any).mockResolvedValue([UNSEEN]);
  await renderPanel();
  expect(screen.getByText(/Every field below is identical/)).toBeInTheDocument();
  expect(screen.getByText(/embedded lorebook/)).toBeInTheDocument();
});

test("an entity whose front matter moved names the front matter, not the card", async () => {
  (api.getIncoming as any).mockResolvedValue([{
    ref: { kind: "lore", id: "the-salt-pact" }, status: "update",
    world: { name: "The Salt Pact", body: "Debts written in salt." },
    mine: { name: "The Salt Pact", body: "Debts written in salt." },
  }]);
  await renderPanel();
  expect(screen.getByText(/keys, owners, or secrecy/)).toBeInTheDocument();
});

test("a world-side rename is visible as a field, not just in the heading", async () => {
  (api.getIncoming as any).mockResolvedValue([{
    ref: { kind: "characters", id: "seraphine" }, status: "update",
    world: { name: "Seraphine of the Tides", version: "v2",
             card: { spec: "chara_card_v3", spec_version: "3.0",
                     data: { name: "Seraphine of the Tides", description: "Keeps the tide ledger." } } },
    mine: { name: "Seraphine", version: "v2", card: card("Keeps the tide ledger.", "") },
  }]);
  await renderPanel();
  // Scoped to the Name field: the new name is in the heading as well, and the
  // point of the test is that the OLD one is on screen to be compared against.
  const field = screen.getByRole("heading", { name: "Name" }).parentElement!;
  expect(within(field).getByText("Seraphine of the Tides")).toBeInTheDocument();
  expect(within(field).getByText("Seraphine")).toBeInTheDocument();
});

test("a failed read can be retried from the banner", async () => {
  (api.getIncoming as any).mockRejectedValueOnce(new Error("campaign not found"))
                          .mockResolvedValueOnce([HARBOR]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /Saltmarch Harbor/ })).toBeInTheDocument();
  // the banner goes with the failure it reported
  expect(screen.queryByText("campaign not found")).not.toBeInTheDocument();
});

test("a single item offers no bulk actions", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR]);
  await renderPanel();
  expect(screen.queryByRole("button", { name: "Accept all" })).not.toBeInTheDocument();
});

test("says the campaign is up to date when nothing is pending", async () => {
  await renderPanel();
  expect(screen.getByText(/up to date with its world/)).toBeInTheDocument();
});

test("a failed read reports the failure instead of claiming to be up to date", async () => {
  (api.getIncoming as any).mockRejectedValue(new Error("campaign not found"));
  await renderPanel();
  expect(screen.getByText("campaign not found")).toBeInTheDocument();
  expect(screen.queryByText(/up to date with its world/)).not.toBeInTheDocument();
});

test("a failed accept reports it and leaves the row in place", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR]);
  (api.acceptIncoming as any).mockRejectedValue(new Error("world is gone"));
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  expect(await screen.findByText("world is gone")).toBeInTheDocument();
  expect(row(/Saltmarch Harbor/)).toBeInTheDocument();
});

test("a read for one campaign cannot land in another campaign's panel", async () => {
  // The panel is mounted by a route that keeps its instance across a `cid`
  // change, so this is the shape of the bug: a slow read for c1 settling after
  // c2 is on screen.
  let settleFirst: (v: unknown) => void = () => {};
  (api.getIncoming as any)
    .mockReturnValueOnce(new Promise((res) => { settleFirst = res; }))
    .mockResolvedValueOnce([SERAPHINE]);

  const { rerender } = render(<IncomingReview cid="c1" />);
  rerender(<IncomingReview cid="c2" />);
  await act(async () => { settleFirst([HARBOR]); });

  expect(api.getIncoming).toHaveBeenNthCalledWith(1, "c1");
  expect(api.getIncoming).toHaveBeenNthCalledWith(2, "c2");
  // c1's answer is discarded rather than shown under c2.
  expect(screen.queryByRole("button", { name: /Saltmarch Harbor/ })).not.toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /Seraphine/ })).toBeInTheDocument();
});
