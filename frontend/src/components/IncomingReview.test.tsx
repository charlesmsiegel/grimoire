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

/** A `new` item: the world has it and the campaign has no copy to weigh. */
const MARA = {
  ref: { kind: "pcs", id: "mara" },
  status: "new",
  world: {
    name: "Mara", version: "v1",
    persona: { name: "Mara", pronouns: "they/them", summary: "A cartographer.",
               description: "Draws the coast nobody asked for." },
  },
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
  expect(screen.getByText("From the world")).toBeInTheDocument();
  expect(screen.getByText("In this campaign")).toBeInTheDocument();
  expect(screen.getByText("The harbour is blockaded.")).toBeInTheDocument();
  expect(screen.getByText("A busy port town.")).toBeInTheDocument();
});

test("a character conflict renders both columns as labelled card fields", async () => {
  (api.getIncoming as any).mockResolvedValue([SERAPHINE]);
  await renderPanel();
  expect(screen.getByRole("heading", { name: "Description" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "First message" })).toBeInTheDocument();
  expect(screen.getByText("Keeps the tide ledger.")).toBeInTheDocument();
  expect(screen.getByText("Keeps two tide ledgers.")).toBeInTheDocument();
  // One column per side, per field — and the field both sides agree on is
  // still shown, because a reader checking a conflict needs the whole card.
  expect(screen.getAllByText("From the world")).toHaveLength(2);
  expect(screen.getAllByText("In this campaign")).toHaveLength(2);
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

test("accept all sends every ref in one call", async () => {
  (api.getIncoming as any).mockResolvedValue([HARBOR, SERAPHINE]);
  await renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Accept all" }));
  await waitFor(() => expect(api.acceptIncoming).toHaveBeenCalledWith(
    "c1", [{ kind: "locations", id: "saltmarch-harbor" },
           { kind: "characters", id: "seraphine" }]));
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
