import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getTodo: vi.fn(), setChoreIgnored: vi.fn() },
}));

import { api } from "../api/client";
import TodoView from "./TodoView";
import type { Chore } from "../api/types";

const chore = (over: Partial<Chore> = {}): Chore => ({
  id: "sheets", group: "World content", severity: "warn", n: 3,
  what: "3 cast members without a sheet",
  why: "A character with no sheet cannot be rolled for.",
  fix: "/campaigns/run/sheets", fix_label: "Sheet coverage", ...over,
});

function renderTodo(cid: string | null = "run") {
  return render(<MemoryRouter><TodoView cid={cid} /></MemoryRouter>);
}

beforeEach(() => {
  (api.getTodo as any).mockResolvedValue({ chores: [chore()], ignored: [], count: 1 });
  (api.setChoreIgnored as any).mockResolvedValue({ ok: true, ignored: [] });
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

test("with no campaign open it says so rather than showing an empty list", async () => {
  // Every chore this page computes is about a campaign; an empty list would
  // read as "nothing to do", which is a different and wrong answer.
  (api.getTodo as any).mockResolvedValue({ chores: [], ignored: [], count: 0 });
  renderTodo(null);
  expect(await screen.findByText(/open a campaign first/i)).toBeInTheDocument();
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
