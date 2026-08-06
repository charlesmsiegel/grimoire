import { render, screen } from "@testing-library/react";
import { LedgerPanel } from "./LedgerPanel";

vi.mock("../api/client", () => ({ api: { campaignLedger: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => vi.clearAllMocks());

const SCENE = { id: "s1", title: "The Pier at Dusk", date: "12 Harvestmoon" };

const LEDGER = {
  commitments: [
    { id: "the-debt", title: "Repay Winifred", kind: "promise", status: "open",
      due: "before the thaw", last_scene: "s1", latest_beat: "Mara swore it aloud.",
      scene: SCENE },
    { id: "the-deadline", title: "Midnight deadline", kind: "threat", status: "open",
      due: "", last_scene: "s1", latest_beat: "", scene: SCENE },
  ],
  plot: [
    { id: "the-ledger", title: "Find the ledger", status: "advanced", last_scene: "s1",
      latest_beat: "Winifred named it aloud.", scene: SCENE },
  ],
  facts: [
    { id: "f1", text: "The pier is condemned.", date: "the third night", scene: SCENE },
  ],
  chronicle: [
    { id: "s1", one_line: "They argued on the pier.", date: "12 Harvestmoon",
      title: "The Pier at Dusk" },
  ],
};

test("groups commitments by kind and shows their deadline and latest beat", async () => {
  (api.campaignLedger as any).mockResolvedValue(LEDGER);
  render(<LedgerPanel cid="c1" />);

  expect(await screen.findByText("Promises")).toBeInTheDocument();
  expect(screen.getByText("Threats")).toBeInTheDocument();
  expect(screen.queryByText("Foreshadowing")).not.toBeInTheDocument();  // empty group hidden
  expect(screen.getByText("Repay Winifred")).toBeInTheDocument();
  expect(screen.getByText("due before the thaw")).toBeInTheDocument();
  expect(screen.getByText("Mara swore it aloud.")).toBeInTheDocument();
});

test("lists open plot threads and recent facts alongside the commitments", async () => {
  (api.campaignLedger as any).mockResolvedValue(LEDGER);
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText("Open plot threads")).toBeInTheDocument();
  expect(screen.getByText("Find the ledger")).toBeInTheDocument();
  expect(screen.getByText("Recent facts")).toBeInTheDocument();
  expect(screen.getByText("They argued on the pier.")).toBeInTheDocument();
});

test("a commitment with an unrecognized kind is still listed", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    ...LEDGER,
    commitments: [{ id: "odd", title: "A wager", kind: "wager", status: "open", due: "",
                    last_scene: "s1", latest_beat: "", scene: SCENE }],
  });
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText("A wager")).toBeInTheDocument();
});

test("shows an empty state when the campaign owes nothing yet", async () => {
  (api.campaignLedger as any).mockResolvedValue({ plot: [], commitments: [], facts: [], chronicle: [] });
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText(/Nothing on the ledger yet/)).toBeInTheDocument();
});

test("a failed load degrades to the empty state rather than hanging on Loading", async () => {
  (api.campaignLedger as any).mockRejectedValue(new Error("boom"));
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText(/Nothing on the ledger yet/)).toBeInTheDocument();
});

test("a changed refreshKey re-reads the ledger", async () => {
  // The panel stays mounted across an absorb save (`cid` does not change), so
  // without this it would go on showing the state from before the scene that
  // just landed.
  (api.campaignLedger as any).mockResolvedValue({ plot: [], commitments: [], facts: [], chronicle: [] });
  const { rerender } = render(<LedgerPanel cid="c1" refreshKey={0} />);
  await screen.findByText(/Nothing on the ledger yet/);
  expect(api.campaignLedger).toHaveBeenCalledTimes(1);

  rerender(<LedgerPanel cid="c1" refreshKey={0} />);   // same revision: no refetch
  expect(api.campaignLedger).toHaveBeenCalledTimes(1);

  (api.campaignLedger as any).mockResolvedValue({
    plot: [], facts: [], chronicle: [],
    commitments: [{ id: "the-debt", title: "Repay Winifred", kind: "promise", status: "open",
                    due: "", last_scene: "s1", latest_beat: "", scene: SCENE }],
  });
  rerender(<LedgerPanel cid="c1" refreshKey={1} />);
  expect(await screen.findByText("Repay Winifred")).toBeInTheDocument();
  expect(api.campaignLedger).toHaveBeenCalledTimes(2);
});

test("switching campaigns blanks the panel instead of showing the old one's rows", async () => {
  // The panel stays mounted across a campaign switch, so without this the
  // previous campaign's promises sit under the new campaign's name for as long
  // as the new request takes — one game's secrets attributed to another.
  (api.campaignLedger as any).mockResolvedValueOnce(LEDGER);
  const { rerender } = render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText("Repay Winifred")).toBeInTheDocument();

  let resolveSecond: (v: any) => void = () => {};
  (api.campaignLedger as any).mockImplementationOnce(
    () => new Promise((res) => { resolveSecond = res; }));
  rerender(<LedgerPanel cid="c2" />);
  expect(screen.queryByText("Repay Winifred")).not.toBeInTheDocument();
  expect(screen.getByText(/Loading/)).toBeInTheDocument();

  resolveSecond({ plot: [], commitments: [], facts: [], chronicle: [] });
  expect(await screen.findByText(/Nothing on the ledger yet/)).toBeInTheDocument();
});

test("a refreshKey bump does not blank the rows it is about to replace", async () => {
  // The other half of the rule above: a refresh re-reads the SAME campaign, and
  // dropping to Loading for that flashes the whole view away after every save.
  (api.campaignLedger as any).mockResolvedValueOnce(LEDGER);
  const { rerender } = render(<LedgerPanel cid="c1" refreshKey={0} />);
  expect(await screen.findByText("Repay Winifred")).toBeInTheDocument();

  (api.campaignLedger as any).mockImplementationOnce(() => new Promise(() => {}));
  rerender(<LedgerPanel cid="c1" refreshKey={1} />);
  expect(screen.getByText("Repay Winifred")).toBeInTheDocument();
});

test("a superseded response cannot overwrite a newer one", async () => {
  // Two fetches in flight after a campaign switch or a post-save refresh: without
  // the cleanup guard, whichever settles LAST wins — which is exactly the
  // pre-absorb ledger clobbering the one the save just triggered.
  let resolveFirst: (v: any) => void = () => {};
  (api.campaignLedger as any).mockImplementationOnce(
    () => new Promise((res) => { resolveFirst = res; }));
  const { rerender } = render(<LedgerPanel cid="c1" refreshKey={0} />);

  (api.campaignLedger as any).mockResolvedValueOnce({
    plot: [], facts: [], chronicle: [],
    commitments: [{ id: "the-debt", title: "Repay Winifred", kind: "promise", status: "open",
                    due: "", last_scene: "s1", latest_beat: "", scene: SCENE }],
  });
  rerender(<LedgerPanel cid="c1" refreshKey={1} />);
  expect(await screen.findByText("Repay Winifred")).toBeInTheDocument();

  // the stale first request settles last, with the pre-refresh (empty) ledger
  resolveFirst({ plot: [], commitments: [], facts: [], chronicle: [] });
  await new Promise((r) => setTimeout(r, 0));
  expect(screen.getByText("Repay Winifred")).toBeInTheDocument();   // not clobbered
});

test("lists standing facts with their date and the scene that recorded them", async () => {
  (api.campaignLedger as any).mockResolvedValue(LEDGER);
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText("Standing facts")).toBeInTheDocument();
  expect(screen.getByText("The pier is condemned.")).toBeInTheDocument();
  expect(screen.getByText("the third night")).toBeInTheDocument();
  // "recorded in", not the "last moved in" the sections above use: a fact is
  // written once and retired off the list rather than moved, so naming its
  // scene as a latest movement would misdate every row.
  expect(screen.getByText(/recorded in The Pier at Dusk/)).toBeInTheDocument();
});

test("a campaign with only standing facts is not the empty state", async () => {
  (api.campaignLedger as any).mockResolvedValue({
    plot: [], commitments: [], chronicle: [],
    facts: [{ id: "f1", text: "The pier is condemned.", date: "", scene: SCENE }],
  });
  render(<LedgerPanel cid="c1" />);
  expect(await screen.findByText("The pier is condemned.")).toBeInTheDocument();
  expect(screen.queryByText(/Nothing on the ledger yet/)).not.toBeInTheDocument();
});
