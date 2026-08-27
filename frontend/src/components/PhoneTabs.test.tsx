import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import type { ShellPayload } from "../api/types";
import PhoneTabs from "./PhoneTabs";

const payload = (over: Partial<ShellPayload> = {}): ShellPayload => ({
  campaigns: 3, library: 6, todo: 14,
  campaign: {
    id: "run", name: "A Campaign", world_name: "Realm", scenes: 15,
    open: [{ sid: "s15", title: "The lower step", turns: null }],
    unreviewed: 8, ledger_open: 4, sheets: { sheeted: 4, total: 7 },
    images_undescribed: 3,
  },
  ...over,
} as ShellPayload);

const at = (path: string, p: ShellPayload | null = payload(), onOpenRail = () => {}) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <PhoneTabs payload={p} cid={p?.campaign?.id ?? null} onOpenRail={onOpenRail} />
    </MemoryRouter>,
  );

test("the five destinations are there and navigable", () => {
  at("/campaigns/run");
  for (const label of ["Hub", "Scenes", "Play", "To do", "More"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("a scene owns its own foot", () => {
  // The composer and the pill that raises the context sheet live there. Two
  // bars stacked at the bottom of a 720px viewport is most of what is left of
  // the transcript.
  const { container } = at("/campaigns/run/scenes/s15");
  expect(container.querySelector(".phone-tabs")).toBeNull();
});

test("the current section is marked for a screen reader, not only coloured", () => {
  at("/todo");
  const todo = screen.getByRole("link", { name: /to do/i });
  expect(todo).toHaveAttribute("aria-current", "page");
});

test("a badge is never the only carrier of its count", () => {
  at("/campaigns/run");
  // "14" is drawn; "14 things noticed" is what is announced.
  expect(screen.getByText("14")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /to do.*14 things noticed/i })).toBeInTheDocument();
});

test("More opens the rail rather than going anywhere", async () => {
  const onOpenRail = vi.fn();
  at("/campaigns/run", payload(), onOpenRail);
  await userEvent.click(screen.getByRole("button", { name: /more/i }));
  expect(onOpenRail).toHaveBeenCalledOnce();
});

test("with no campaign the bar still reaches the app", () => {
  at("/todo", payload({ campaign: null, todo: null }));
  for (const label of ["Campaigns", "Library", "To do", "More"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  expect(screen.queryByText("Play")).toBeNull();
});
