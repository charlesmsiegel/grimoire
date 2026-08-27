import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AppRail from "./AppRail";
import type { ShellPayload } from "../api/types";

/** A payload with a campaign open. Every figure is invented; the point of each
 *  test is the *shape* the rail draws it in, not the number. */
const PAYLOAD: ShellPayload = {
  campaigns: 3,
  campaign: {
    id: "c1", name: "A Run In Saltmarch", world_name: "Saltmarch",
    scenes: 15,
    open: [{ sid: "s15", title: "The lower step", turns: null }],
    ledger_open: 4,
    sheets: { sheeted: 4, total: 7 },
    unreviewed: null, pending: [],
    images_undescribed: null,
  },
  todo: null,
};

function renderRail(over: Partial<Parameters<typeof AppRail>[0]> = {}, path = "/") {
  const props = {
    payload: PAYLOAD, status: "ready" as const, cid: "c1",
    dataDir: "~/.grimoire", docked: true, open: false,
    onClose: () => {}, onRetry: () => {}, ...over,
  };
  return render(<MemoryRouter initialEntries={[path]}><AppRail {...props} /></MemoryRouter>);
}

const main = () => screen.getByRole("navigation", { name: /^main$/i });

test("both tiers render, and the campaign line is derived", () => {
  renderRail();
  expect(main()).toBeInTheDocument();
  const camp = screen.getByRole("navigation", { name: /open campaign/i });
  expect(within(camp).getByText("A Run In Saltmarch")).toBeInTheDocument();
  // Not a literal anywhere: world, scene count and open count all come off the
  // payload, so the line cannot agree with the data on the day it was written
  // and drift the moment anything changes.
  expect(within(camp).getByText("Saltmarch · 15 scenes · 1 open")).toBeInTheDocument();
});

test("the campaign tier is absent when nothing is open", () => {
  renderRail({ payload: { campaigns: 3, campaign: null, todo: null }, cid: null });
  expect(screen.queryByRole("navigation", { name: /open campaign/i })).not.toBeInTheDocument();
});

test("a row with nowhere to go is absent from the DOM, not disabled", () => {
  // Disabled would still be a promise that the page exists. It does not.
  // To do has a page now; Wrap-up and Images still do not.
  renderRail();
  expect(within(main()).getByText("To do")).toBeInTheDocument();
  const camp = screen.getByRole("navigation", { name: /open campaign/i });
  for (const gone of ["Wrap-up", "Images"]) {
    expect(within(camp).queryByText(gone)).not.toBeInTheDocument();
  }
});

test("0 renders as 0; unmeasured renders nothing at all", () => {
  const zeroed = {
    ...PAYLOAD,
    campaign: { ...PAYLOAD.campaign!, ledger_open: 0, sheets: null },
  };
  renderRail({ payload: zeroed });
  const camp = screen.getByRole("navigation", { name: /open campaign/i });
  // "Nothing is waiting" is an answer and is shown.
  expect(within(camp).getByRole("link", { name: /ledger & timeline, 0 open threads/i }))
    .toBeInTheDocument();
  // "Nobody computed it" is not, and is not drawn as a zero.
  expect(within(camp).getByRole("link", { name: /^sheets$/i })).toBeInTheDocument();
});

test("a count is in the row's accessible name, not only its position", () => {
  renderRail();
  expect(within(main()).getByRole("link", { name: /campaigns, 3 campaigns/i }))
    .toBeInTheDocument();
});

test("one row per un-ended scene, with no tail while turns are unknown", () => {
  renderRail();
  const open = screen.getByRole("navigation", { name: /open scenes/i });
  const row = within(open).getByRole("link", { name: /the lower step/i });
  expect(row).toHaveAttribute("href", "/campaigns/c1/scenes/s15");
  expect(row).not.toHaveTextContent(/\dt/);
});

test("the active row is marked for a screen reader too", () => {
  renderRail({}, "/campaigns/c1/ledger");
  const camp = screen.getByRole("navigation", { name: /open campaign/i });
  expect(within(camp).getByRole("link", { name: /ledger & timeline/i }))
    .toHaveAttribute("aria-current", "page");
  // ...and Overview is not also lit, which one shared prefix rule would have
  // done: every campaign page lives under the hub's own path.
  expect(within(camp).getByRole("link", { name: /^overview$/i }))
    .not.toHaveAttribute("aria-current");
});

test("a failed read keeps the rail usable and offers a retry", () => {
  // Navigation is the rail's first job and has to survive a server that stopped
  // answering — so the payload is kept and labelled rather than blanked.
  const onRetry = vi.fn();
  renderRail({ status: "failed", onRetry });
  expect(screen.getByText(/counts may be out of date/i)).toBeInTheDocument();
  expect(within(main()).getByText("Configuration")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /retry/i }));
  expect(onRetry).toHaveBeenCalled();
});

describe("as a drawer", () => {
  test("it is a dialog: named, focused on open, and Escape closes it", () => {
    const onClose = vi.fn();
    renderRail({ docked: false, open: true, onClose });
    const dialog = screen.getByRole("dialog", { name: /navigation/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.activeElement).toBe(dialog);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  test("the backdrop closes it", () => {
    const onClose = vi.fn();
    const { container } = renderRail({ docked: false, open: true, onClose });
    fireEvent.click(container.querySelector(".rail-backdrop")!);
    expect(onClose).toHaveBeenCalled();
  });

  test("picking a row closes it", () => {
    const onClose = vi.fn();
    renderRail({ docked: false, open: true, onClose });
    fireEvent.click(within(main()).getByText("Configuration"));
    expect(onClose).toHaveBeenCalled();
  });

  test("Tab is contained rather than walking out behind it", () => {
    // `useHotkeys({modal:true})` gives Escape and the suppression of the view's
    // bindings; it does not stop Tab leaving. Without this the reader tabs
    // through header and page controls that are visually behind the drawer —
    // invisible to a mouse and total to a keyboard.
    renderRail({ docked: false, open: true });
    const dialog = screen.getByRole("dialog", { name: /navigation/i });
    const stops = dialog.querySelectorAll("a[href], button");
    const last = stops[stops.length - 1] as HTMLElement;
    last.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  test("closing returns focus to whatever opened it", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = renderRail({ docked: false, open: true });
    rerender(
      <MemoryRouter>
        <AppRail payload={PAYLOAD} status="ready" cid="c1" dataDir="~/.grimoire"
                 docked={false} open={false} onClose={() => {}} onRetry={() => {}} />
      </MemoryRouter>);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  test("widening does not chase focus to an opener that no longer exists", () => {
    // The ☰ only exists below the breakpoint, so the close that *widening*
    // causes has no opener left. Focusing a detached node sends focus to
    // <body> and silently loses the reader's place; this asserts it does not.
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = renderRail({ docked: false, open: true });
    const chased = vi.spyOn(opener, "focus");
    opener.remove();                                  // the header dropped it
    rerender(
      <MemoryRouter>
        <AppRail payload={PAYLOAD} status="ready" cid="c1" dataDir="~/.grimoire"
                 docked open={false} onClose={() => {}} onRetry={() => {}} />
      </MemoryRouter>);
    expect(chased).not.toHaveBeenCalled();
  });
});
