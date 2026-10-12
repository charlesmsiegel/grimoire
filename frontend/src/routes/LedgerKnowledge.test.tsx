import { fireEvent, screen, waitFor, within } from "@testing-library/react";

vi.mock("../api/client", async () =>
  (await import("../testkit/ledgerMocks")).ledgerApiMock());
import { api, type KnowledgeEntry } from "../api/client";
import { installLedgerMocks, renderLedger } from "../testkit/ledgerHarness";
import { ledgerHref, parseLedgerTail } from "../ledgerPaths";

const R1 = "r-" + "1".repeat(32);

/** One statement as `GET .../ledger/knowledge` returns it. */
function statement(over: Partial<KnowledgeEntry> = {}): KnowledgeEntry {
  return {
    id: "k-0000000a", audience: "characters:mara", audience_name: "Mara",
    audience_now: "Mara", status: "knows", note: "Seraphine told her on the walk back.",
    source: "manual", created: "2026-10-12T00:00:00Z",
    subject: { kind: "posts", scene: "a".repeat(32),
               posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16) }] },
    scene: { sid: "0001-pier", title: "The Pier", digest: "e".repeat(64) },
    unverifiable: false,
    posts: [{ key: R1, part: "", index: 1, speaker: "Winifred",
              text: "Winifred whispers the code.", missing: false,
              variant: "v1", digest: "d".repeat(16) }],
    stale: false, stale_reason: null, ...over,
  };
}

const column = () => within(screen.getByRole("complementary", { name: "Ledger sections" }));

beforeEach(() => { installLedgerMocks(); });

test("the section's address round-trips", () => {
  expect(ledgerHref("run", { section: "knowledge", row: "k-0000000a" }))
    .toBe("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(parseLedgerTail("knowledge/k-0000000a"))
    .toEqual({ section: "knowledge", row: "k-0000000a" });
});

test("a row opens the read-only view, with its sidebar and no form", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement()], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge");
  fireEvent.click(await column().findByRole("button", { name: /Mara knows/ }));
  expect(await screen.findByText("Winifred whispers the code.")).toBeInTheDocument();
  expect(screen.getByText("Seraphine told her on the walk back.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByRole("link", { name: "Mara" })).toHaveAttribute(
    "href", "/campaigns/run/world/characters/mara");
  expect(screen.getByRole("link", { name: "The Pier" })).toHaveAttribute(
    "href", "/campaigns/run/scenes/0001-pier");
  const sidebar = document.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(sidebar).getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(within(sidebar).getByText("knows")).toBeInTheDocument();
});

test("Edit reveals the form, and a note edit sends only the note", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement()], intact: true });
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.ledgerKnowledgeScene as any).mockResolvedValue({
    sid: "0001-pier", digest: "e".repeat(64), keyless: 0,
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), index: 1,
              speaker: "Winifred", text: "Winifred whispers the code.", hidden: false }],
  });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const note = await screen.findByRole("textbox", { name: "Note" });
  fireEvent.change(note, { target: { value: "Overheard, more likely." } });
  const save = screen.getByRole("button", { name: "Save statement" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  await waitFor(() => expect(api.ledgerSaveKnowledge).toHaveBeenCalledWith(
    "run", "k-0000000a", { note: "Overheard, more likely." }));
});

test("+ New opens the form directly, and a grant posts what was shown", async () => {
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.ledgerKnowledgeScene as any).mockResolvedValue({
    sid: "0001-pier", digest: "e".repeat(64), keyless: 1,
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), index: 1,
              speaker: "Winifred", text: "Winifred whispers the code.", hidden: false }],
  });
  renderLedger("/campaigns/run/ledger/knowledge");
  fireEvent.click(await column().findByRole("button", { name: "+ New statement" }));
  expect(await screen.findByRole("heading", { name: "New statement" })).toBeInTheDocument();
  fireEvent.change(await screen.findByRole("combobox", { name: "Who" }),
                   { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Scene" }),
                   { target: { value: "0001-pier" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: /Winifred whispers/ }));
  expect(screen.getByText(/1 older post carry no id/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add statement" }));
  await waitFor(() => expect(api.ledgerCreateKnowledge).toHaveBeenCalledWith("run", {
    audience: "characters:mara", status: "knows", note: "", scene: "0001-pier",
    posts: [{ key: R1, part: "" }], whole_scene: false,
    expect: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16) }],
    expect_digest: "",
  }));
});

test("a stale statement says why and confirms against the text on screen", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    stale: true, stale_reason: "changed",
    posts: [{ key: R1, part: "", index: 1, speaker: "Winifred", text: "Winifred lies.",
              missing: false, variant: "v2", digest: "f".repeat(16) }],
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(await screen.findByText(/The text changed since/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm against current text" }));
  await waitFor(() => expect(api.ledgerSaveKnowledge).toHaveBeenCalledWith(
    "run", "k-0000000a",
    { restamp: true, expect: [{ key: R1, part: "", variant: "v2", digest: "f".repeat(16) }] }));
});

test("a statement about a deleted scene offers Remove", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    scene: null, stale: true, stale_reason: "gone",
    posts: [{ key: R1, part: "", index: null, speaker: "", text: "", missing: true }],
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(await screen.findByText("Points at a deleted scene.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(api.ledgerDeleteKnowledge).toHaveBeenCalledWith("run", "k-0000000a"));
});
