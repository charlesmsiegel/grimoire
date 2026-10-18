import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

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
    scene: { sid: "0001-pier", title: "The Pier", digest: "e".repeat(64), shown: "w".repeat(16) },
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
    sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
    summary: "", one_line: "", keyless: 0, keyless_posts: [],
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16),
              index: 1, speaker: "Winifred", text: "Winifred whispers the code.",
              hidden: false }],
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
    sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
    summary: "", one_line: "", keyless: 1,
    keyless_posts: [{ index: 0, speaker: "You", text: "An old line." }],
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16),
              index: 1, speaker: "Winifred", text: "Winifred whispers the code.",
              hidden: false }],
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
    expect: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16) }],
    expect_digest: "", expect_shown: "", audience_name: "Mara",
  }));
});

test("+ New statement is in the head like every section's + New, and Cancel leaves the form",
  async () => {
    // It was only a row in the column, which on a phone is behind a toggle,
    // and the blank form it opened had no way out but that column.
    renderLedger("/campaigns/run/ledger/knowledge");
    const head = await screen.findByRole("heading", { level: 1, name: "Knowledge" });
    const shelf = within(head.closest(".shelf-head") as HTMLElement);
    fireEvent.click(shelf.getByRole("button", { name: "+ New statement" }));
    expect(await screen.findByRole("heading", { name: "New statement" })).toBeInTheDocument();
    expect(screen.getByTestId("here")).toHaveTextContent("/campaigns/run/ledger/knowledge/new");
    expect(shelf.getByRole("button", { name: "+ New statement" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "New statement" })).toBeNull());
    expect(screen.getByTestId("here")).toHaveTextContent(/^\/campaigns\/run\/ledger\/knowledge$/);
  });

test.each(["saved", "refused"])("Cancel waits for a pending Knowledge creation to be %s",
  async (outcome) => {
    vi.mocked(api.listScenes).mockResolvedValue([
      { id: "0001-pier", title: "The Pier", model: "", created: "", updated: "", date: "" },
    ]);
    vi.mocked(api.ledgerKnowledgeScene).mockResolvedValue({
      sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
      summary: "", one_line: "", keyless: 0, keyless_posts: [], posts: [],
    });
    let release: () => void = () => { throw new Error("The write has not started"); };
    vi.mocked(api.ledgerCreateKnowledge).mockReturnValueOnce(new Promise((resolve, reject) => {
      release = outcome === "saved" ? () => resolve({ id: "k-00000001" })
        : () => reject(new Error("offline"));
    }));
    renderLedger("/campaigns/run/ledger/knowledge/new");
    fireEvent.change(await screen.findByRole("combobox", { name: "Who" }),
                     { target: { value: "*" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Scene" }),
                     { target: { value: "0001-pier" } });
    fireEvent.click(await screen.findByRole("checkbox", { name: "The whole scene" }));
    fireEvent.click(screen.getByRole("button", { name: "Add statement" }));
    await waitFor(() => expect(api.ledgerCreateKnowledge).toHaveBeenCalledTimes(1));
    try {
      const cancel = screen.getByRole("button", { name: "Cancel" });
      expect(cancel).toBeDisabled();
      fireEvent.click(cancel);
      expect(screen.getByTestId("here")).toHaveTextContent("/campaigns/run/ledger/knowledge/new");
    } finally {
      await act(async () => { release(); });
    }
    if (outcome === "saved") {
      await waitFor(() => expect(screen.getByTestId("here"))
        .toHaveTextContent("/campaigns/run/ledger/knowledge/k-00000001"));
    } else {
      expect(await screen.findByText("Error: offline")).toBeInTheDocument();
      const cancel = screen.getByRole("button", { name: "Cancel" });
      expect(cancel).toBeEnabled();
      fireEvent.click(cancel);
      await waitFor(() => expect(screen.getByTestId("here"))
        .toHaveTextContent(/^\/campaigns\/run\/ledger\/knowledge$/));
    }
  });

test("a stale statement says why and confirms against the text on screen", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    stale: true, stale_reason: "changed",
    posts: [{ key: R1, part: "", index: 1, speaker: "Winifred", text: "Winifred lies.",
              missing: false, variant: "v2", digest: "f".repeat(16), shown: "g".repeat(16) }],
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(await screen.findByText(/The text changed since/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm against current text" }));
  await waitFor(() => expect(api.ledgerSaveKnowledge).toHaveBeenCalledWith(
    "run", "k-0000000a",
    { restamp: true, expect: [{ key: R1, part: "", variant: "v2", digest: "f".repeat(16),
                                shown: "g".repeat(16) }] }));
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

test("a renamed audience is confirmed under its current name", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    audience_now: "Mara Vance", stale: true, stale_reason: "audience",
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  fireEvent.click(await screen.findByRole("button", { name: "Confirm it is still Mara Vance" }));
  await waitFor(() => expect(api.ledgerSaveKnowledge).toHaveBeenCalledWith(
    "run", "k-0000000a", { audience: "characters:mara", audience_name: "Mara Vance" }));
});

test("an unverifiable scene is not offered for removal", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    scene: null, unverifiable: true, stale: true, stale_reason: "unverifiable",
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(await screen.findByText(/could not be read just now \(a synced/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
});

test("turning a never-heard into knows names the posts again, as shown", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({ status: "unaware" })],
                                                   intact: true });
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.ledgerKnowledgeScene as any).mockResolvedValue({
    sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
    summary: "", one_line: "", keyless: 0, keyless_posts: [],
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16),
              index: 1, speaker: "Winifred", text: "Winifred whispers the code.",
              hidden: false }],
  });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByRole("combobox", { name: "Status" }),
                   { target: { value: "knows" } });
  const save = screen.getByRole("button", { name: "Save statement" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  await waitFor(() => expect(api.ledgerSaveKnowledge).toHaveBeenCalledWith(
    "run", "k-0000000a", {
      status: "knows", scene: "0001-pier", posts: [{ key: R1, part: "" }], whole_scene: false,
      expect: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16) }],
      expect_digest: "", expect_shown: "",
    }));
});

test("an unchanged edit cannot be saved, and an orphaned never-heard offers Remove", async () => {
  (api.ledgerKnowledge as any).mockResolvedValue({ entries: [statement({
    status: "unaware", scene: null, stale: true, stale_reason: "gone",
    posts: [{ key: R1, part: "", index: null, speaker: "", text: "", missing: true }],
  })], intact: true });
  renderLedger("/campaigns/run/ledger/knowledge/k-0000000a");
  expect(await screen.findByText("No longer applies")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByRole("button", { name: "Save statement" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "x" } });
  expect(screen.getByRole("button", { name: "Save statement" })).toBeEnabled();
});

test("the form shows each post whole, since a grant covers all of it", async () => {
  const long = "Winifred whispers the code. " + "She keeps talking. ".repeat(20) + "THE END.";
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" }]);
  (api.ledgerKnowledgeScene as any).mockResolvedValue({
    sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
    summary: "", one_line: "", keyless: 0, keyless_posts: [],
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16),
              index: 1, speaker: "Winifred", text: long, hidden: false }],
  });
  renderLedger("/campaigns/run/ledger/knowledge/new");
  fireEvent.change(await screen.findByRole("combobox", { name: "Scene" }),
                   { target: { value: "0001-pier" } });
  expect(await screen.findByText(/THE END\.$/)).toBeInTheDocument();
});

test("choosing the whole scene shows everything it covers before it can be granted", async () => {
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.ledgerKnowledgeScene as any).mockResolvedValue({
    sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
    summary: "Winifred poisoned the cup.", one_line: "The cup.", keyless: 1,
    keyless_posts: [{ index: 0, speaker: "You", text: "An old line with no id." }],
    posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16), shown: "s".repeat(16),
              index: 1, speaker: "Winifred", text: "Winifred whispers the code.", hidden: false }],
  });
  renderLedger("/campaigns/run/ledger/knowledge/new");
  fireEvent.change(await screen.findByRole("combobox", { name: "Who" }),
                   { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Scene" }),
                   { target: { value: "0001-pier" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: "The whole scene" }));
  const whole = screen.getByRole("group", { name: "The whole scene" });
  expect(within(whole).getByText("Summary: Winifred poisoned the cup.")).toBeInTheDocument();
  expect(within(whole).getByText("You: An old line with no id.")).toBeInTheDocument();
  expect(within(whole).getByText("Winifred: Winifred whispers the code.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add statement" }));
  await waitFor(() => expect(api.ledgerCreateKnowledge).toHaveBeenCalledWith("run",
    expect.objectContaining({ whole_scene: true, posts: null, expect_digest: "e".repeat(64),
                              expect_shown: "w".repeat(16) })));
});

test("switching scenes drops the last one's text until the new one arrives", async () => {
  (api.listScenes as any).mockResolvedValue([{ id: "0001-pier", title: "The Pier" },
                                             { id: "0002-mill", title: "The Mill" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.ledgerKnowledgeScene as any).mockImplementation((_cid: string, sid: string) =>
    sid === "0001-pier"
      ? Promise.resolve({
          sid: "0001-pier", digest: "e".repeat(64), shown: "w".repeat(16), title: "The Pier",
          summary: "Winifred poisoned the cup.", one_line: "", keyless: 0, keyless_posts: [],
          posts: [{ key: R1, part: "", variant: "v1", digest: "d".repeat(16),
                    shown: "s".repeat(16), index: 1, speaker: "Winifred",
                    text: "Winifred whispers the code.", hidden: false }] })
      : new Promise(() => {}));
  renderLedger("/campaigns/run/ledger/knowledge/new");
  fireEvent.change(await screen.findByRole("combobox", { name: "Who" }),
                   { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }),
                   { target: { value: "unaware" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Scene" }),
                   { target: { value: "0001-pier" } });
  fireEvent.click(await screen.findByRole("checkbox", { name: "The whole scene" }));
  expect(screen.getByText("Summary: Winifred poisoned the cup.")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Scene" }),
                   { target: { value: "0002-mill" } });
  await waitFor(() => expect(screen.queryByText("Summary: Winifred poisoned the cup."))
    .toBeNull());
  expect(screen.queryByText(/Winifred whispers the code/)).toBeNull();
  expect(screen.getByRole("button", { name: "Add statement" })).toBeDisabled();
});
