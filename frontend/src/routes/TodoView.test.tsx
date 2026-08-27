import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getTodo: vi.fn(), setChoreIgnored: vi.fn(), getChoreItems: vi.fn() },
}));

import { api } from "../api/client";
import TodoView from "./TodoView";
import type { Chore } from "../api/types";

const chore = (over: Partial<Chore> = {}): Chore => ({
  id: "sheets", scope: "campaign", group: "World content", severity: "warn", n: 3,
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
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", true));
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
  await waitFor(() => expect(api.setChoreIgnored).toHaveBeenCalledWith("sheets", false));
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
    chores: [chore({ id: "world-taglines", scope: "world", n: 4,
                     what: "4 characters with no tagline", fix: "/worlds",
                     fix_label: "The worlds" })],
    ignored: [], count: 1,
  });
  renderTodo(null);
  expect(await screen.findByText("4 characters with no tagline")).toBeInTheDocument();
  // and the campaign half is named as missing rather than left to be inferred
  expect(screen.getByText(/chores about a campaign need one open/i)).toBeInTheDocument();
  // ...but not a scope chip on every row. With no campaign open they are all
  // the library's, and a label repeated down the whole page says nothing.
  expect(screen.queryByText(/your library/i)).toBeNull();
});

test("two chores with the same sentence are told apart by scope", async () => {
  // `taglines` and `world-taglines` both read "N characters with no tagline".
  // Before the chip the only thing separating them was a clause at the end of
  // the `why` prose, and for a screen reader they were two controls with one
  // accessible name.
  (api.getTodo as any).mockResolvedValue({
    chores: [
      chore({ id: "taglines", scope: "campaign", n: 3,
              what: "3 characters with no tagline" }),
      chore({ id: "world-taglines", scope: "world", n: 4,
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
      chore({ id: "owed", scope: "campaign", group: "Continuity", n: 2,
              what: "2 open threads with a deadline" }),
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

test("chores are grouped, and the column counts each group", async () => {
  (api.getTodo as any).mockResolvedValue({
    chores: [chore(), chore({ id: "owed", group: "Continuity", what: "2 open threads" })],
    ignored: [], count: 2,
  });
  renderTodo();
  await screen.findByText("3 cast members without a sheet");
  expect(screen.getByRole("heading", { name: "World content" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Continuity" })).toBeInTheDocument();
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
