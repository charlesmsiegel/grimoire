import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChangesPanel } from "./ChangesPanel";

vi.mock("../api/client", () => ({
  api: { campaignChanges: vi.fn(), campaignJournal: vi.fn(), undoJournalEntry: vi.fn() },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string) { super(detail); }
  },
}));
import { ApiError, api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.campaignChanges as any).mockResolvedValue([]);
  (api.campaignJournal as any).mockResolvedValue([]);
});

const HARBOR = {
  ref: { kind: "locations", id: "harbor" }, name: "Harbor",
  scene: { id: "s1", title: "The blockade", date: "12 Harvestmoon" },
  fields: [{ field: "body", label: "Harbor — locations",
    diff: [{ op: "equal", text: "A busy port town." },
           { op: "insert", text: "Now blockaded." }] }],
};

const ENTRY = {
  id: "j1", ts: "2026-07-11T00:00:00Z", source: "absorb", kind: "lore",
  ref: { kind: "locations", id: "harbor" }, name: "Harbor",
  label: "Harbor — locations", field: "body",
  scene: { id: "s1", title: "The blockade", date: "12 Harvestmoon" },
  diff: [{ op: "delete", text: "A busy port town." },
         { op: "insert", text: "Now blockaded." }],
  undoable: true, why: "", undone: null,
};

/** Render, then let the Records fetch settle before anything else runs — the
 *  panel loads that view on mount whichever tab the test is after. */
async function renderPanel() {
  render(<ChangesPanel cid="c1" />);
  await act(async () => {});
}

async function openHistory() {
  fireEvent.click(screen.getByRole("button", { name: "History" }));
  await act(async () => {});
}

test("lists changed records and shows a field diff on select", async () => {
  (api.campaignChanges as any).mockResolvedValue([HARBOR]);
  await renderPanel();
  fireEvent.click(await screen.findByRole("button", { name: /Harbor/ }));
  expect(screen.getByText("Now blockaded.")).toBeInTheDocument();
  expect(screen.getByText("Now blockaded.").className).toContain("diff-insert");
  expect(screen.getByText("A busy port town.").className).toContain("diff-equal");
});

test("shows an empty state when nothing has changed", async () => {
  (api.campaignChanges as any).mockResolvedValue([]);
  await renderPanel();
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});

test("the history tab lists journalled changes and shows one on select", async () => {
  (api.campaignJournal as any).mockResolvedValue([ENTRY]);
  await renderPanel();
  await openHistory();
  fireEvent.click(await screen.findByRole("button", { name: /Harbor — locations/ }));
  expect(screen.getByText("Now blockaded.").className).toContain("diff-insert");
  expect(screen.getByRole("button", { name: /Undo this change/ })).toBeEnabled();
});

test("the history is only fetched once the tab is opened", async () => {
  await renderPanel();
  await screen.findByText(/No record changes yet/);
  expect(api.campaignJournal).not.toHaveBeenCalled();
  await openHistory();
  await waitFor(() => expect(api.campaignJournal).toHaveBeenCalledWith("c1"));
});

test("undoing re-reads both views rather than patching the row", async () => {
  (api.campaignJournal as any).mockResolvedValue([ENTRY]);
  (api.undoJournalEntry as any).mockResolvedValue({ ok: true, entry: ENTRY });
  await renderPanel();
  await openHistory();
  fireEvent.click(await screen.findByRole("button", { name: /Harbor — locations/ }));
  fireEvent.click(screen.getByRole("button", { name: /Undo this change/ }));
  await waitFor(() => expect(api.undoJournalEntry).toHaveBeenCalledWith("c1", "j1"));
  // A reversal is itself a journal entry, so the list is a different list.
  await waitFor(() => expect(api.campaignJournal).toHaveBeenCalledTimes(2));
  // And the rolling delta moved with it, so that view is re-read too.
  await waitFor(() => expect(api.campaignChanges).toHaveBeenCalledWith("c1", true));
});

test("a change with no reversal offers no button, and says why", async () => {
  (api.campaignJournal as any).mockResolvedValue([{
    ...ENTRY, undoable: false, why: "undoing a created location means deleting it",
  }]);
  await renderPanel();
  await openHistory();
  fireEvent.click(await screen.findByRole("button", { name: /Harbor — locations/ }));
  expect(screen.getByRole("button", { name: /Undo this change/ })).toBeDisabled();
  expect(screen.getByText(/means deleting it/)).toBeInTheDocument();
});

test("an already-undone change is marked in the rail and cannot be undone again", async () => {
  (api.campaignJournal as any).mockResolvedValue([{
    ...ENTRY, undoable: false, undone: { ts: "2026-07-11T01:00:00Z", by: "j2" },
  }]);
  await renderPanel();
  await openHistory();
  const row = await screen.findByRole("button", { name: /Harbor — locations/ });
  expect(row.textContent).toContain("undone");
  fireEvent.click(row);
  expect(screen.getByRole("button", { name: "Undone" })).toBeDisabled();
});

test("a refused undo shows the server's reason", async () => {
  (api.campaignJournal as any).mockResolvedValue([ENTRY]);
  (api.undoJournalEntry as any).mockRejectedValue(
    new ApiError(409, "this record changed after the edit you are undoing"));
  await renderPanel();
  await openHistory();
  fireEvent.click(await screen.findByRole("button", { name: /Harbor — locations/ }));
  fireEvent.click(screen.getByRole("button", { name: /Undo this change/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/changed after the edit/);
});

test("shows an empty state when nothing has ever been changed", async () => {
  await renderPanel();
  await openHistory();
  expect(await screen.findByText(/Nothing has been changed/)).toBeInTheDocument();
});
