import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getTodo: vi.fn(), rememberedTodo: vi.fn(), setChoreIgnored: vi.fn(), getChoreItems: vi.fn(),
         listCampaigns: vi.fn().mockResolvedValue([]), listScenes: vi.fn().mockResolvedValue([]) },
}));

import { api } from "../api/client";
import TodoView from "./TodoView";
import type { Chore, ChoreItems, TodoPayload } from "../api/types";
import { fixture } from "../testkit/fixture";

const chore = (over: Partial<Chore> = {}): Chore => ({
  id: "sheets", scope: "campaign", group: "Game mechanics", severity: "warn", n: 3,
  what: "3 cast members without a sheet",
  why: "A character with no sheet cannot be rolled for.",
  fix: "/campaigns/run/sheets", fix_label: "Sheet coverage", ...over,
});

function renderTodo(cid: string | null = "run", address = "/todo") {
  return render(<MemoryRouter initialEntries={[address]}><TodoView cid={cid} /></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

beforeEach(() => {
  // Calls, not implementations: two tests below count how many times the page
  // fetched, and a recorded call from the previous test makes "fetched once"
  // unprovable. `clearAllMocks` clears the record and leaves the mocks callable
  // — the implementations are re-set immediately below.
  vi.clearAllMocks();
  vi.mocked(api.listScenes).mockResolvedValue([]);
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [chore()], ignored: [], count: 1 });
  vi.mocked(api.rememberedTodo).mockReturnValue(undefined);
  vi.mocked(api.setChoreIgnored).mockResolvedValue({ ok: true, ignored: [] });
  vi.mocked(api.getChoreItems).mockResolvedValue({
    items: [{ id: "mara", label: "Mara Vance", detail: "characters",
              fix: "/campaigns/run/world" },
            { id: "sera", label: "Seraphine Coll", detail: "characters" }],
    total: 2, truncated: false,
  });
});

test("a chore says what it is, why it matters, and where to fix it", async () => {
  renderTodo();
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
  // The why is not decoration: a count with no consequence attached is one the
  // reader learns to skip.
  expect(screen.getByText(/cannot be rolled for/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /sheet coverage/i }))
    .toHaveAttribute("href", "/campaigns/run/sheets");
});

test("ignoring re-reads the list rather than patching it", async () => {
  // Ignoring is not the only thing that can have changed the counts, and the
  // counts are the whole point of the page.
  vi.mocked(api.getTodo)
    .mockResolvedValueOnce({ chores: [chore()], ignored: [], count: 1 })
    .mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  fireEvent.click(screen.getByRole("button", { name: /^ignore$/i }));
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", true, "run"));
  expect(await screen.findByRole("button", { name: /ignored \(1\)/i })).toBeInTheDocument();
});

test("an ignored chore is kept, with a way back", async () => {
  // A dismissal that cannot be undone is one nobody dares make.
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /ignored \(1\)/i }));
  expect(await screen.findByRole("button", { name: /restore/i })).toBeInTheDocument();
  // ...and it is not offered a Fix while it is waved off: that would be the
  // page still asking for the thing you told it to stop asking about.
  expect(screen.queryByRole("link", { name: /sheet coverage/i })).not.toBeInTheDocument();
});

test("restoring sends the ignore back off", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /ignored \(1\)/i }));
  fireEvent.click(await screen.findByRole("button", { name: /restore/i }));
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", false, "run"));
});

test("nothing outstanding reads as an answer", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [], ignored: [], count: 0 });
  renderTodo();
  expect(await screen.findByText(/nothing outstanding/i)).toBeInTheDocument();
});

test("with no campaign open the library's own chores are still listed", async () => {
  // The page used to say "open a campaign first" INSTEAD of the list, because
  // every chore it could compute was about a campaign. The library's own --
  // an undescribed image backlog, a world whose cast has no taglines -- answer
  // before a campaign is chosen, which is exactly when a freshly imported
  // world's backlog is largest. Hiding them behind that line hid a list with
  // entries in it.
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [chore({ id: "world-taglines", scope: "world", group: "Character & voice", n: 4,
                     what: "4 characters with no tagline", fix: "/worlds",
                     fix_label: "The worlds" })],
    ignored: [], count: 1,
  });
  renderTodo(null);
  expect(await screen.findByText("4 characters with no tagline")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "All campaigns" })).toBeInTheDocument();
});

test("two chores with the same sentence are told apart by scope", async () => {
  // `taglines` and `world-taglines` both read "N characters with no tagline".
  // Before the chip the only thing separating them was a clause at the end of
  // the `why` prose, and for a screen reader they were two controls with one
  // accessible name.
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [
      chore({ id: "taglines", scope: "campaign", group: "Character & voice", n: 3,
              what: "3 characters with no tagline" }),
      chore({ id: "world-taglines", scope: "world", group: "Character & voice", n: 4,
              what: "4 characters with no tagline" }),
    ],
    ignored: [], count: 2,
  });
  renderTodo("run");

  // The chip is part of each heading's accessible name, which is what makes the
  // two distinguishable rather than merely different-looking.
  expect(await screen.findByRole("heading", { name: /3 characters with no tagline this campaign/i }))
    .toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /4 characters with no tagline your library/i }))
    .toBeInTheDocument();
});

test("the scope chip stays away when every chore is the same kind", async () => {
  // It exists to tell two rows apart. Where there is nothing to tell apart it
  // is a word on every row, which is a word the reader learns to skip.
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [
      chore({ id: "sheets", scope: "campaign" }),
      chore({ id: "owed", scope: "campaign", group: "Commitments", n: 2,
              what: "2 open commitments with a deadline" }),
    ],
    ignored: [], count: 2,
  });
  renderTodo("run");
  await screen.findByText("3 cast members without a sheet");
  expect(screen.queryByText(/this campaign/i)).toBeNull();
});

test("a failed read is not an empty list", async () => {
  vi.mocked(api.getTodo).mockRejectedValue(new Error("offline"));
  renderTodo();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/nothing outstanding/i)).not.toBeInTheDocument();
});

test("the list and Groups column follow the server's thematic order", async () => {
  const groups = ["Commitments", "Character & voice", "Artwork", "Game mechanics", "Costs & pricing"];
  vi.mocked(api.getTodo).mockResolvedValue({
    // Input order differs from heading order, as it does when a global report
    // combines library chores with campaign chores.
    chores: [
      chore({ id: "unpriced", scope: "library", group: "Costs & pricing", what: "2 unpriced calls" }),
      chore(),
      chore({ id: "cover", group: "Artwork", what: "Campaign without a cover" }),
      chore({ id: "avatars", group: "Character & voice", what: "3 characters without an avatar" }),
      chore({ id: "world-taglines", scope: "world", group: "Character & voice", what: "2 characters with no tagline" }),
      chore({ id: "owed", group: "Commitments", what: "2 open commitments" }),
    ],
    ignored: [], count: 6, groups,
  });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  expect(screen.getAllByRole("heading", { level: 2 }).slice(1).map((h) => h.textContent)).toEqual(groups);
  const links = screen.getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("#"));
  expect(links.map((link) => link.textContent)).toEqual([
    "Active scenes", "Commitments1", "Character & voice2", "Artwork1", "Game mechanics1", "Costs & pricing1",
  ]);
  expect(links.map((link) => link.getAttribute("href"))).toEqual(["#active-scenes", ...groups.map((g) => `#${encodeURIComponent(g)}`)]);
  const characterSection = screen.getByRole("heading", { name: "Character & voice" }).closest("section")!;
  expect(within(characterSection).getByRole("heading", { name: /3 characters without an avatar/i })).toBeInTheDocument();
  expect(within(characterSection).getByRole("heading", { name: /2 characters with no tagline/i })).toBeInTheDocument();
});


test("a chore lists the instances behind its count", async () => {
  // A count says how much is undone; it never says which. The preview is the
  // difference between "3 without a sheet" and knowing whether that matters.
  renderTodo();
  expect(await screen.findByText("Mara Vance")).toBeInTheDocument();
  expect(screen.getAllByText("characters")).toHaveLength(2);
  // An instance that can be gone to is a link; one that cannot is not.
  expect(screen.getByRole("link", { name: "Mara Vance" }))
    .toHaveAttribute("href", "/campaigns/run/world");
  expect(screen.queryByRole("link", { name: "Seraphine Coll" })).not.toBeInTheDocument();
});

test("ignored tasks stay collapsed and never enumerate records", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  await screen.findByText(/nothing outstanding/i);
  expect(screen.queryByRole("button", { name: /^restore$/i })).toBeNull();
  expect(api.getChoreItems).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /ignored \(1\)/i }));
  expect(await screen.findByRole("button", { name: /^restore$/i })).toBeInTheDocument();
  expect(api.getChoreItems).not.toHaveBeenCalled();
});

test("a capped list says it is capped", async () => {
  // A short list nobody labels reads as a complete one.
  vi.mocked(api.getChoreItems).mockResolvedValue({
    items: [{ id: "a", label: "A", detail: "" }], total: 240, truncated: true,
  });
  renderTodo();
  expect(await screen.findByText(/showing 1 of 240/i)).toBeInTheDocument();
});

test("instances that cannot be read say so rather than reading as none", async () => {
  vi.mocked(api.getChoreItems).mockRejectedValue(new Error("offline"));
  renderTodo();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/nothing to list here/i)).not.toBeInTheDocument();
});

// ---- first paint from memory, then revalidate (roadmap 04, 6.2) ----

/** A promise and the hand that settles it. */
function held<T>() {
  let resolve: (v: T) => void = () => {};
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

const REMEMBERED: TodoPayload = {
  chores: [chore()], ignored: [chore({ id: "owed", what: "2 open commitments" })], count: 1,
};

test("a remembered list paints on the first render, busy, with Ignore and Restore held", async () => {
  vi.mocked(api.rememberedTodo).mockReturnValue(REMEMBERED);
  const answer = held<TodoPayload>();
  vi.mocked(api.getTodo).mockReturnValue(answer.promise);
  renderTodo();
  // Synchronously, before any await: the frame painted from memory.
  expect(screen.getByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("button", { name: /^ignore$/i })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /ignored \(1\)/i }));
  expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();
  // Reading records and following their links stay live during revalidation.
  expect(screen.getByRole("link", { name: /sheet coverage/i })).toHaveAttribute("href", "/campaigns/run/sheets");
  expect(api.getTodo).toHaveBeenCalledWith("run");

  answer.resolve({ chores: [chore({ what: "4 cast members without a sheet" })], ignored: [],
                   count: 1 });
  expect(await screen.findByText("4 cast members without a sheet")).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.getByRole("main")).not.toHaveAttribute("aria-busy");
  expect(screen.getByRole("button", { name: /^ignore$/i })).toBeEnabled();
});

test("nothing outstanding is not said before a read has landed", async () => {
  const answer = held<TodoPayload>();
  vi.mocked(api.getTodo).mockReturnValue(answer.promise);
  renderTodo();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
  answer.resolve({ chores: [], ignored: [], count: 0 });
  expect(await screen.findByText(/nothing outstanding/i)).toBeInTheDocument();
});

test("a remembered zero is not said until the visit's own read agrees", async () => {
  vi.mocked(api.rememberedTodo).mockReturnValue({ chores: [], ignored: [], count: 0 });
  const answer = held<TodoPayload>();
  vi.mocked(api.getTodo).mockReturnValue(answer.promise);
  renderTodo();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
  answer.resolve({ chores: [chore()], ignored: [], count: 1 });
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
});

test("a failed read drops the remembered list, and Try again re-reads", async () => {
  vi.mocked(api.rememberedTodo).mockReturnValue(REMEMBERED);
  vi.mocked(api.getTodo).mockRejectedValue(new Error("offline"));
  renderTodo();
  expect(await screen.findByText(/the list could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();

  vi.mocked(api.getTodo).mockResolvedValue({ chores: [chore()], ignored: [], count: 1 });
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(api.getTodo).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/could not be read/i)).toBeNull();
});

test("an ignore re-reads through the page's own read, which paints the list after it", async () => {
  vi.mocked(api.getTodo)
    .mockResolvedValueOnce({ chores: [chore()], ignored: [], count: 1 })
    .mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /^ignore$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /ignored \(1\)/i }));
  expect(await screen.findByRole("button", { name: /^restore$/i })).toBeEnabled();
  // One read for the visit, one after the write, and the page never went
  // blank in between: the list stayed up while the second was asked.
  expect(api.getTodo).toHaveBeenCalledTimes(2);
  expect(api.getTodo).toHaveBeenLastCalledWith("run");
});

test("a second visit to the same scope is never blank", async () => {
  const first = renderTodo();
  await screen.findByText("3 cast members without a sheet");
  first.unmount();
  // What the client remembered from the first visit.
  vi.mocked(api.rememberedTodo).mockImplementation((cid) =>
    (cid === "run" ? { chores: [chore()], ignored: [], count: 1 } : undefined));
  vi.mocked(api.getTodo).mockReturnValue(new Promise(() => {}));
  renderTodo();
  expect(screen.getByText("3 cast members without a sheet")).toBeInTheDocument();
  await screen.findByRole("link", { name: /sheet coverage/i });
});

test("switching campaign never shows the last one's chores under the next", async () => {
  vi.mocked(api.rememberedTodo).mockImplementation((cid) =>
    (cid === "run" ? { chores: [chore()], ignored: [], count: 1 } : undefined));
  const view = renderTodo("run");
  await screen.findByText("3 cast members without a sheet");
  const other = held<TodoPayload>();
  vi.mocked(api.getTodo).mockReturnValue(other.promise);
  view.rerender(<MemoryRouter><TodoView cid="tidewrack" /></MemoryRouter>);
  // The render that moves the key is the one that must not draw A's rows.
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.queryByRole("button", { name: /^ignore$/i })).toBeNull();
  other.resolve({ chores: [chore({ what: "1 open commitment" })], ignored: [], count: 1 });
  expect(await screen.findByText("1 open commitment")).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
});

test("records are readable without opening the task, with a short preview and a way to see all", async () => {
  vi.mocked(api.getChoreItems).mockResolvedValue({
    items: ["Mara", "Seraphine", "Winifred", "Realm"].map((label) => ({
      id: label, label, detail: "characters", fix: `/campaigns/run/world?character=${label}`,
    })), total: 4, truncated: false,
  });
  renderTodo();
  expect(await screen.findByRole("link", { name: "Mara" }))
    .toHaveAttribute("href", "/campaigns/run/world?character=Mara");
  expect(screen.queryByRole("link", { name: "Realm" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /show all 4/i }));
  expect(screen.getByRole("link", { name: "Realm" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /show fewer/i }));
  expect(screen.queryByRole("link", { name: "Realm" })).toBeNull();
  expect(api.getChoreItems).toHaveBeenCalledTimes(1);
});

test("offscreen record previews wait until they enter view", async () => {
  let enterView: (() => void) | undefined;
  const disconnect = vi.fn();
  vi.stubGlobal("IntersectionObserver", class implements IntersectionObserver {
    readonly root = null;
    readonly rootMargin = "0px";
    readonly thresholds = [0];
    constructor(callback: IntersectionObserverCallback) {
      enterView = () => callback([fixture({ isIntersecting: true })], this);
    }
    observe() {}
    unobserve() {}
    takeRecords(): IntersectionObserverEntry[] { return []; }
    disconnect = disconnect;
  });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  expect(api.getChoreItems).not.toHaveBeenCalled();
  act(() => enterView?.());
  expect(await screen.findByRole("link", { name: "Mara Vance" })).toBeInTheDocument();
  expect(disconnect).toHaveBeenCalled();
});

test("priority filters hide unrelated tasks and keep the group index in agreement", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore(),
    chore({ id: "unreviewed", group: "Wrap-up", severity: "alert", what: "1 unreviewed proposal" }),
    chore({ id: "cover", group: "Artwork", severity: "note", what: "Campaign without a cover" }),
  ], ignored: [], count: 3, groups: ["Wrap-up", "Artwork", "Game mechanics"] });
  renderTodo();
  await screen.findByText("1 unreviewed proposal");
  fireEvent.click(screen.getByRole("button", { name: /needs attention.*1/i }));
  expect(screen.getByText("1 unreviewed proposal")).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.queryByRole("link", { name: /game mechanics/i })).toBeNull();
  expect(screen.getByText(/showing 1 of 3 tasks/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^all.*3/i }));
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
});

test("campaign grouping keeps equal names and equal task ids separate, with library work once", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore({ campaign_id: "realm", campaign_name: "Realm", what: "2 cast members without a sheet" }),
    chore({ campaign_id: "saltmarch", campaign_name: "Realm", what: "3 cast members without a sheet" }),
    chore({ id: "cover", campaign_id: "realm", campaign_name: "Realm", group: "Artwork",
            what: "Campaign without a cover", severity: "note" }),
    chore({ id: "world-anchors", scope: "world", group: "Character & voice", what: "4 characters with no voice anchor" }),
  ], ignored: [], count: 4, groups: ["Character & voice", "Artwork", "Game mechanics"] });
  renderTodo(null);
  await screen.findByText("2 cast members without a sheet");
  fireEvent.change(screen.getByRole("combobox", { name: /group by/i }), { target: { value: "campaign" } });
  const realm = screen.getByRole("heading", { level: 2, name: "Realm (realm)" }).closest("section");
  const saltmarch = screen.getByRole("heading", { level: 2, name: "Realm (saltmarch)" }).closest("section");
  const library = screen.getByRole("heading", { level: 2, name: "Your library" }).closest("section");
  if (!realm || !saltmarch || !library) throw new Error("Expected each task group to have its own section");
  expect(within(realm).getByText("2 cast members without a sheet")).toBeInTheDocument();
  expect(within(realm).getByText("Campaign without a cover")).toBeInTheDocument();
  expect(within(saltmarch).queryByText("Campaign without a cover")).toBeNull();
  expect(within(library).getByText("4 characters with no voice anchor")).toBeInTheDocument();
  fireEvent.click(within(saltmarch).getByRole("button", { name: /^ignore$/i }));
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", true, "saltmarch"));
});

test("grouping and priority in the address survive a fresh visit", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore({ campaign_id: "realm", campaign_name: "Realm" }),
    chore({ id: "cover", campaign_id: "realm", campaign_name: "Realm", severity: "note",
            group: "Artwork", what: "Campaign without a cover" }),
  ], ignored: [], count: 2, groups: ["Artwork", "Game mechanics"] });
  renderTodo(null, "/todo?group=campaign&priority=warn");
  expect(await screen.findByRole("heading", { level: 2, name: "Realm" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /group by/i })).toHaveValue("campaign");
  expect(screen.queryByText("Campaign without a cover")).toBeNull();
  expect(screen.getByRole("button", { name: /recommended.*1/i })).toHaveAttribute("aria-pressed", "true");
});

test("a preview failure can be retried without losing the task or its fix", async () => {
  vi.mocked(api.getChoreItems).mockRejectedValueOnce(new Error("offline"));
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /retry records/i }));
  expect(await screen.findByRole("link", { name: "Mara Vance" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /sheet coverage/i })).toBeInTheDocument();
});

test("a refreshed summary rejects an older preview still on the wire", async () => {
  const old = held<ChoreItems>();
  let sheetReads = 0;
  vi.mocked(api.getTodo)
    .mockResolvedValueOnce({ chores: [chore(), chore({ id: "cover", group: "Artwork", what: "Campaign without a cover" })],
                            ignored: [], count: 2 })
    .mockResolvedValue({ chores: [chore({ n: 1, what: "1 cast member without a sheet" })], ignored: [], count: 1 });
  vi.mocked(api.getChoreItems).mockImplementation((id: string) => {
    if (id === "sheets" && sheetReads++ === 0) return old.promise;
    return Promise.resolve({ items: [{ id: "winifred", label: "Winifred", detail: "characters" }],
                             total: 1, truncated: false });
  });
  renderTodo();
  const cover = (await screen.findByText("Campaign without a cover")).closest("li");
  if (!cover) throw new Error("Expected the cover task in its own list item");
  fireEvent.click(within(cover).getByRole("button", { name: /^ignore$/i }));
  expect(await screen.findByText("1 cast member without a sheet")).toBeInTheDocument();
  expect(await screen.findByText("Winifred")).toBeInTheDocument();
  await act(async () => old.resolve({ items: [{ id: "mara", label: "Mara", detail: "characters" }],
                                     total: 1, truncated: false }));
  expect(screen.queryByText("Mara")).toBeNull();
  expect(screen.getByText("Winifred")).toBeInTheDocument();
});

test("the most important tasks lead within a type, and an empty filter does not claim all work is done", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore({ id: "taglines", severity: "note", group: "Character & voice", what: "3 characters with no tagline" }),
    chore({ id: "anchors", severity: "warn", group: "Character & voice", what: "3 characters with no voice anchor" }),
  ], ignored: [], count: 2, groups: ["Character & voice"] });
  renderTodo();
  await screen.findByText("3 characters with no voice anchor");
  expect(screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent))
    .toEqual(["3 characters with no voice anchor", "3 characters with no tagline"]);
  fireEvent.click(screen.getByRole("button", { name: /needs attention.*0/i }));
  expect(screen.getByText(/no tasks at this priority/i)).toBeInTheDocument();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Showing 0 of 2 tasks");
});

test("switching report campaign preserves grouping and priority", async () => {
  function Address() { return <output>{useLocation().pathname}{useLocation().search}</output>; }
  vi.mocked(api.listCampaigns).mockResolvedValue(fixture([{ id: "realm", name: "Realm" }]));
  render(<MemoryRouter initialEntries={["/todo?group=campaign&priority=warn"]}>
    <TodoView cid={null} /><Address />
  </MemoryRouter>);
  await screen.findByRole("option", { name: "Realm" });
  fireEvent.change(screen.getByRole("combobox", { name: /report campaign/i }), { target: { value: "realm" } });
  expect(screen.getByText("/campaigns/realm/todo?group=campaign&priority=warn")).toBeInTheDocument();
});

test("all active scenes lead the page, independent of priority and ignored reminders", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [chore({ id: "owed", group: "Commitments", what: "1 open commitment with a deadline" })],
    ignored: [chore({ id: "open-scenes", campaign_id: "realm", campaign_name: "Realm", group: "Scenes",
                      severity: "note", n: 205, what: "205 incomplete scenes" })],
    count: 1, groups: ["Commitments"],
  });
  const scenes = Array.from({ length: 205 }, (_, i) => ({
    id: `scene-${i + 1}`, title: `Saltmarch ${i + 1}`, model: "", created: "", updated: "",
    date: "2026-10-16", place: "Saltmarch", done: false,
  }));
  vi.mocked(api.listScenes).mockResolvedValue([...scenes,
    { ...scenes[0], id: "done", title: "Finished scene", done: true },
    { ...scenes[0], id: "branch", title: "Closed branch", closed_by: { sid: "done", title: "Finished scene" } },
  ]);
  renderTodo(null, "/todo?priority=warn");
  const active = await screen.findByRole("region", { name: "Active scenes" });
  expect(await within(active).findByRole("link", { name: "Saltmarch 205" }))
    .toHaveAttribute("href", "/campaigns/realm/scenes/scene-205");
  expect(within(active).getAllByRole("link")).toHaveLength(205);
  expect(within(active).queryByText("Finished scene")).toBeNull();
  expect(within(active).queryByText("Closed branch")).toBeNull();
  expect(screen.getAllByRole("heading", { level: 2 })[0]).toHaveTextContent("Active scenes");
  expect(screen.getByRole("heading", { level: 2, name: "Commitments" })).toBeInTheDocument();
  expect(api.listScenes).toHaveBeenCalledWith("realm");
});

test("active scene failures are retryable and never pass for no active scenes", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({
    chores: [chore({ id: "open-scenes", group: "Scenes", severity: "note", what: "1 incomplete scene" })],
    ignored: [], count: 1, groups: ["Scenes"],
  });
  vi.mocked(api.listScenes).mockRejectedValueOnce(new Error("offline"));
  renderTodo();
  const active = await screen.findByRole("region", { name: "Active scenes" });
  expect(await within(active).findByText(/could not be read/i)).toBeInTheDocument();
  expect(within(active).queryByText(/no active scenes/i)).toBeNull();
  fireEvent.click(within(active).getByRole("button", { name: /retry records/i }));
  expect(await within(active).findByText(/no active scenes/i)).toBeInTheDocument();
});

test("equally named campaigns remain distinguishable in the active scene list", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: ["realm", "saltmarch"].map((campaign_id) =>
    chore({ id: "open-scenes", group: "Scenes", campaign_id, campaign_name: "Realm", severity: "note" })),
    ignored: [], count: 2, groups: ["Scenes"],
  });
  vi.mocked(api.listScenes).mockResolvedValue([{
    id: "scene", title: "Saltmarch", model: "", created: "", updated: "", date: "", done: false,
  }]);
  renderTodo(null);
  const active = await screen.findByRole("region", { name: "Active scenes" });
  expect(await within(active).findByRole("heading", { name: "Realm (realm)" })).toBeInTheDocument();
  expect(within(active).getByRole("heading", { name: "Realm (saltmarch)" })).toBeInTheDocument();
  expect(within(active).getAllByRole("link", { name: "Saltmarch" }).map((link) => link.getAttribute("href")))
    .toEqual(["/campaigns/realm/scenes/scene", "/campaigns/saltmarch/scenes/scene"]);
});

test("active scenes appear once and do not become a second task type or campaign task", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore({ id: "open-scenes", group: "Scenes", severity: "note", what: "1 incomplete scene" }),
    chore({ id: "owed", group: "Commitments", what: "1 open commitment with a deadline" }),
  ], ignored: [], count: 2, groups: ["Scenes", "Commitments"] });
  vi.mocked(api.listScenes).mockResolvedValue([{
    id: "scene", title: "Saltmarch", model: "", created: "", updated: "", date: "", done: false,
  }]);
  renderTodo();
  expect(await screen.findByRole("link", { name: "Saltmarch" }))
    .toHaveAttribute("href", "/campaigns/run/scenes/scene");
  expect(screen.getAllByRole("link", { name: "Saltmarch" })).toHaveLength(1);
  expect(screen.queryByRole("link", { name: /^scenes\d*$/i })).toBeNull();
  expect(screen.queryByRole("heading", { level: 2, name: "Scenes" })).toBeNull();
  expect(screen.queryByText("1 incomplete scene")).toBeNull();
  expect(screen.getByRole("button", { name: /^all.*1/i })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 1 task");
  fireEvent.change(screen.getByRole("combobox", { name: /group by/i }), { target: { value: "campaign" } });
  expect(screen.queryByText("1 incomplete scene")).toBeNull();
  expect(await screen.findAllByRole("link", { name: "Saltmarch" })).toHaveLength(1);
});

test("active scenes alone do not leave an empty scene task or count behind", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [
    chore({ id: "open-scenes", group: "Scenes", severity: "note", what: "1 incomplete scene" }),
  ], ignored: [], count: 1, groups: ["Scenes"] });
  renderTodo();
  await screen.findByRole("region", { name: "Active scenes" });
  expect(screen.queryByRole("heading", { name: "Scenes" })).toBeNull();
  expect(screen.getByRole("button", { name: /^all.*0/i })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(/no other tasks outstanding/i);
});

test("equally titled active scenes use story numbers even when updated order is reversed", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [chore({ id: "open-scenes", group: "Scenes" })],
    ignored: [], count: 1 });
  vi.mocked(api.listScenes).mockResolvedValue([2, 1].map((number) => ({
    id: `00${number}--2026-10-16--new-scene`, title: "New scene", model: "", created: "", updated: "",
    date: "2026-10-16", place: "Saltmarch", done: false,
  })));
  renderTodo();
  const active = await screen.findByRole("region", { name: "Active scenes" });
  await within(active).findByRole("link", { name: "New scene (Scene 2)" });
  expect(within(active).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")]))
    .toEqual([
      ["New scene (Scene 2)", "/campaigns/run/scenes/002--2026-10-16--new-scene"],
      ["New scene (Scene 1)", "/campaigns/run/scenes/001--2026-10-16--new-scene"],
    ]);
  expect(within(active).getAllByText("2026-10-16 · Saltmarch")).toHaveLength(2);
});

test("a unique active title keeps its label even when finished or closed scenes share it", async () => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [chore({ id: "open-scenes", group: "Scenes" })],
    ignored: [], count: 1 });
  const scene = { id: "001--2026-10-16--saltmarch", title: "Saltmarch", model: "", created: "", updated: "",
    date: "", done: false };
  vi.mocked(api.listScenes).mockResolvedValue([
    scene, { ...scene, id: "002--2026-10-16--saltmarch", done: true },
    { ...scene, id: "003--2026-10-16--saltmarch", closed_by: { sid: scene.id, title: scene.title } },
  ]);
  renderTodo();
  const active = await screen.findByRole("region", { name: "Active scenes" });
  expect(await within(active).findByRole("link", { name: "Saltmarch" }))
    .toHaveAttribute("href", "/campaigns/run/scenes/001--2026-10-16--saltmarch");
  expect(within(active).getAllByRole("link")).toHaveLength(1);
  expect(within(active).getByText("No time or place set")).toBeInTheDocument();
});

test.each([
  ["legacy ids", ["saltmarch", "saltmarch-2"]],
  ["branch siblings sharing a story number", ["001--2026-10-16--saltmarch", "001--2026-10-16--saltmarch-2"]],
])("equally titled scenes with %s remain distinguishable", async (_case, ids) => {
  vi.mocked(api.getTodo).mockResolvedValue({ chores: [chore({ id: "open-scenes", group: "Scenes" })],
    ignored: [], count: 1 });
  vi.mocked(api.listScenes).mockResolvedValue(ids.map((id) => ({
    id, title: "New scene", model: "", created: "", updated: "", date: "", done: false,
  })));
  renderTodo();
  const active = await screen.findByRole("region", { name: "Active scenes" });
  for (const id of ids) {
    expect(await within(active).findByRole("link", { name: `New scene (${id})` }))
      .toHaveAttribute("href", `/campaigns/run/scenes/${id}`);
  }
});
