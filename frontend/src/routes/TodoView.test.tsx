import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getTodo: vi.fn(), rememberedTodo: vi.fn(), setChoreIgnored: vi.fn(), getChoreItems: vi.fn(),
         listCampaigns: vi.fn().mockResolvedValue([]) },
}));

import { api } from "../api/client";
import TodoView from "./TodoView";
import type { Chore, TodoPayload } from "../api/types";

const chore = (over: Partial<Chore> = {}): Chore => ({
  id: "sheets", scope: "campaign", group: "Game mechanics", severity: "warn", n: 3,
  what: "3 cast members without a sheet",
  why: "A character with no sheet cannot be rolled for.",
  fix: "/campaigns/run/sheets", fix_label: "Sheet coverage", ...over,
});

function renderTodo(cid: string | null = "run") {
  return render(<MemoryRouter><TodoView cid={cid} /></MemoryRouter>);
}

beforeEach(() => {
  // Calls, not implementations: two tests below count how many times the page
  // fetched, and a recorded call from the previous test makes "fetched once"
  // unprovable. `clearAllMocks` clears the record and leaves the mocks callable
  // — the implementations are re-set immediately below.
  vi.clearAllMocks();
  (api.getTodo as any).mockResolvedValue({ chores: [chore()], ignored: [], count: 1 });
  (api.rememberedTodo as any).mockReturnValue(undefined);
  (api.setChoreIgnored as any).mockResolvedValue({ ok: true, ignored: [] });
  (api.getChoreItems as any).mockResolvedValue({
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
  (api.getTodo as any)
    .mockResolvedValueOnce({ chores: [chore()], ignored: [], count: 1 })
    .mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  fireEvent.click(screen.getByRole("button", { name: /^ignore$/i }));
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", true, "run"));
  expect(await screen.findByText("Ignored")).toBeInTheDocument();
});

test("an ignored chore is kept, with a way back", async () => {
  // A dismissal that cannot be undone is one nobody dares make.
  (api.getTodo as any).mockResolvedValue({
    chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  expect(await screen.findByRole("button", { name: /restore/i })).toBeInTheDocument();
  // ...and it is not offered a Fix while it is waved off: that would be the
  // page still asking for the thing you told it to stop asking about.
  expect(screen.queryByRole("link", { name: /sheet coverage/i })).not.toBeInTheDocument();
});

test("restoring sends the ignore back off", async () => {
  (api.getTodo as any).mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /restore/i }));
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", false, "run"));
});

test("nothing outstanding reads as an answer", async () => {
  (api.getTodo as any).mockResolvedValue({ chores: [], ignored: [], count: 0 });
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
  (api.getTodo as any).mockResolvedValue({
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
  (api.getTodo as any).mockResolvedValue({
    chores: [
      chore({ id: "taglines", scope: "campaign", group: "Character & voice", n: 3,
              what: "3 characters with no tagline" }),
      chore({ id: "world-taglines", scope: "world", group: "Character & voice", n: 4,
              what: "4 characters with no tagline" }),
    ],
    ignored: [], count: 2,
  });
  renderTodo("run");

  // The chip is part of each button's accessible name, which is what makes the
  // two distinguishable rather than merely different-looking.
  expect(await screen.findByRole("button", { name: /3 characters with no tagline this campaign/i }))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: /4 characters with no tagline your library/i }))
    .toBeInTheDocument();
});

test("the scope chip stays away when every chore is the same kind", async () => {
  // It exists to tell two rows apart. Where there is nothing to tell apart it
  // is a word on every row, which is a word the reader learns to skip.
  (api.getTodo as any).mockResolvedValue({
    chores: [
      chore({ id: "sheets", scope: "campaign" }),
      chore({ id: "owed", scope: "campaign", group: "Story & continuity", n: 2,
              what: "2 open commitments with a deadline" }),
    ],
    ignored: [], count: 2,
  });
  renderTodo("run");
  await screen.findByText("3 cast members without a sheet");
  expect(screen.queryByText(/this campaign/i)).toBeNull();
});

test("a failed read is not an empty list", async () => {
  (api.getTodo as any).mockRejectedValue(new Error("offline"));
  renderTodo();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/nothing outstanding/i)).not.toBeInTheDocument();
});

test("the list and Groups column follow the server's thematic order", async () => {
  const groups = ["Story & continuity", "Character & voice", "Artwork", "Game mechanics", "Costs & pricing"];
  (api.getTodo as any).mockResolvedValue({
    // Input order differs from heading order, as it does when a global report
    // combines library chores with campaign chores.
    chores: [
      chore({ id: "unpriced", scope: "library", group: "Costs & pricing", what: "2 unpriced calls" }),
      chore(),
      chore({ id: "cover", group: "Artwork", what: "Campaign without a cover" }),
      chore({ id: "avatars", group: "Character & voice", what: "3 characters without an avatar" }),
      chore({ id: "world-taglines", scope: "world", group: "Character & voice", what: "2 characters with no tagline" }),
      chore({ id: "owed", group: "Story & continuity", what: "2 open commitments" }),
    ],
    ignored: [], count: 6, groups,
  });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(groups);
  const links = screen.getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("#"));
  expect(links.map((link) => link.textContent)).toEqual([
    "Story & continuity1", "Character & voice2", "Artwork1", "Game mechanics1", "Costs & pricing1",
  ]);
  expect(links.map((link) => link.getAttribute("href"))).toEqual(groups.map((g) => `#${encodeURIComponent(g)}`));
  const characterSection = screen.getByRole("heading", { name: "Character & voice" }).closest("section")!;
  expect(within(characterSection).getByRole("button", { name: /3 characters without an avatar/i })).toBeInTheDocument();
  expect(within(characterSection).getByRole("button", { name: /2 characters with no tagline/i })).toBeInTheDocument();
});


test("a chore expands to the instances behind its count", async () => {
  // A count says how much is undone; it never says which. Expanding is the
  // difference between "3 without a sheet" and knowing whether that matters.
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /3 cast members/i }));
  expect(await screen.findByText("Mara Vance")).toBeInTheDocument();
  expect(screen.getAllByText("characters")).toHaveLength(2);
  // An instance that can be gone to is a link; one that cannot is not.
  expect(screen.getByRole("link", { name: "Mara Vance" }))
    .toHaveAttribute("href", "/campaigns/run/world");
  expect(screen.queryByRole("link", { name: "Seraphine Coll" })).not.toBeInTheDocument();
});

test("the instances are fetched when expanded, not when the page loads", async () => {
  // Naming every instance of every chore is the cost the list exists to avoid:
  // `sheets` sweeps the cast and `taglines` walks the roster.
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  expect(api.getChoreItems).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: /3 cast members/i }));
  await waitFor(() => expect(api.getChoreItems).toHaveBeenCalledWith("sheets", "run"));
});

test("collapsing and reopening does not refetch", async () => {
  renderTodo();
  const toggle = await screen.findByRole("button", { name: /3 cast members/i });
  fireEvent.click(toggle);
  await screen.findByText("Mara Vance");
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  await screen.findByText("Mara Vance");
  expect((api.getChoreItems as any).mock.calls).toHaveLength(1);
});

test("a capped list says it is capped", async () => {
  // A short list nobody labels reads as a complete one.
  (api.getChoreItems as any).mockResolvedValue({
    items: [{ id: "a", label: "A", detail: "" }], total: 240, truncated: true,
  });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /3 cast members/i }));
  expect(await screen.findByText(/showing 1 of 240/i)).toBeInTheDocument();
});

test("instances that cannot be read say so rather than reading as none", async () => {
  (api.getChoreItems as any).mockRejectedValue(new Error("offline"));
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /3 cast members/i }));
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
  (api.rememberedTodo as any).mockReturnValue(REMEMBERED);
  const answer = held<TodoPayload>();
  (api.getTodo as any).mockReturnValue(answer.promise);
  renderTodo();
  // Synchronously, before any await: the frame painted from memory.
  expect(screen.getByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("button", { name: /^ignore$/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();
  // Expanding a chore and following its fix act on nothing; both stay live.
  expect(screen.getByRole("button", { name: /3 cast members/i })).toBeEnabled();
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
  (api.getTodo as any).mockReturnValue(answer.promise);
  renderTodo();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
  answer.resolve({ chores: [], ignored: [], count: 0 });
  expect(await screen.findByText(/nothing outstanding/i)).toBeInTheDocument();
});

test("a remembered zero is not said until the visit's own read agrees", async () => {
  (api.rememberedTodo as any).mockReturnValue({ chores: [], ignored: [], count: 0 });
  const answer = held<TodoPayload>();
  (api.getTodo as any).mockReturnValue(answer.promise);
  renderTodo();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
  answer.resolve({ chores: [chore()], ignored: [], count: 1 });
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();
});

test("a failed read drops the remembered list, and Try again re-reads", async () => {
  (api.rememberedTodo as any).mockReturnValue(REMEMBERED);
  (api.getTodo as any).mockRejectedValue(new Error("offline"));
  renderTodo();
  expect(await screen.findByText(/the list could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.queryByText(/nothing outstanding/i)).toBeNull();

  (api.getTodo as any).mockResolvedValue({ chores: [chore()], ignored: [], count: 1 });
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("3 cast members without a sheet")).toBeInTheDocument();
  expect(api.getTodo).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/could not be read/i)).toBeNull();
});

test("an ignore re-reads through the page's own read, which paints the list after it", async () => {
  (api.getTodo as any)
    .mockResolvedValueOnce({ chores: [chore()], ignored: [], count: 1 })
    .mockResolvedValue({ chores: [], ignored: [chore()], count: 0 });
  renderTodo();
  fireEvent.click(await screen.findByRole("button", { name: /^ignore$/i }));
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
  (api.rememberedTodo as any).mockImplementation((cid: string | null) =>
    (cid === "run" ? { chores: [chore()], ignored: [], count: 1 } : undefined));
  (api.getTodo as any).mockReturnValue(new Promise(() => {}));
  renderTodo();
  expect(screen.getByText("3 cast members without a sheet")).toBeInTheDocument();
});

test("switching campaign never shows the last one's chores under the next", async () => {
  (api.rememberedTodo as any).mockImplementation((cid: string | null) =>
    (cid === "run" ? { chores: [chore()], ignored: [], count: 1 } : undefined));
  const view = renderTodo("run");
  await screen.findByText("3 cast members without a sheet");
  const other = held<TodoPayload>();
  (api.getTodo as any).mockReturnValue(other.promise);
  view.rerender(<MemoryRouter><TodoView cid="tidewrack" /></MemoryRouter>);
  // The render that moves the key is the one that must not draw A's rows.
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
  expect(screen.queryByRole("button", { name: /^ignore$/i })).toBeNull();
  other.resolve({ chores: [chore({ what: "1 open commitment" })], ignored: [], count: 1 });
  expect(await screen.findByText("1 open commitment")).toBeInTheDocument();
  expect(screen.queryByText("3 cast members without a sheet")).toBeNull();
});
