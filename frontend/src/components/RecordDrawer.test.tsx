import { useState, type ReactNode } from "react";
import { render as rtlRender, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { RecordDrawer } from "./RecordDrawer";
import { api } from "../api/client";
import { artImage, artRead } from "../testkit/recordArt";
import type { RecordArtTarget } from "../api/types";
import { useHotkeys } from "../shortcuts/useHotkeys";

let drawerPath = "";
function LocationSpy() { const location = useLocation(); drawerPath = location.pathname + location.search; return null; }
function Router({ children }: { children: ReactNode }) { return <MemoryRouter><LocationSpy />{children}</MemoryRouter>; }
const render = (ui: ReactNode) => rtlRender(ui, { wrapper: Router });

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCastDetail: vi.fn(),
      readEntity: vi.fn(), readRecordArt: vi.fn(),
      actorImageUrl: (_sc: { id: string }, k: string, a: string, v: string, _n: string,
                      o?: { w?: number; v?: string | null }) =>
        `/img/${k}/${a}/${v}${o?.w ? `?w=${o.w}` : ""}${o?.v ? `${o?.w ? "&" : "?"}v=${o.v}` : ""}`,
    },
  };
});

const ACTOR = { type: "actor" as const, kind: "characters" as const, id: "seraphine" };

function detail(source: string) {
  return { kind: "characters", id: "seraphine", name: "Seraphine", version: "default",
           body: "the drowned keeper", source };
}

function open(target: Parameters<typeof RecordDrawer>[0]["target"] = ACTOR) {
  return render(<RecordDrawer cid="c" sid="s" target={target} onClose={() => {}} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.readRecordArt as any).mockImplementation((target: RecordArtTarget) => Promise.resolve(artRead(target, [
    artImage(target, "avatar", { image_id: null, description: "A portrait", thumb: "/api/preview?w=160&v=bytes", url: "/api/original?v=bytes", v: "bytes" }),
  ])));
  (api.getCastDetail as any).mockResolvedValue(detail("library"));
  (api.readEntity as any).mockResolvedValue({ meta: { name: "The Docks" }, body: "wet" });
});

test.each([
  ["library", "Library", "unedited in this campaign"],
  ["override", "Override", "this campaign's edits on top"],
  ["emergent", "Emergent", "no library record behind it"],
])("a %s cast member is badged %s", async (source, label, hint) => {
  (api.getCastDetail as any).mockResolvedValue(detail(source));
  open();
  const chip = await screen.findByText(label, { selector: ".cast-source" });
  // The state is on the element as well as in the word: `override` and
  // `emergent` take a colour from it, and `library` is the one that stays in
  // the role-chip default on purpose (#99).
  expect(chip).toHaveClass(source);
  // A word each is not enough to act on — "Override" only means something
  // once you know what it overrode — so each badge carries its own sentence.
  expect(chip.getAttribute("title")).toContain(hint);
});

test("the avatar is a downscale, not the original file", async () => {
  // Drawn at up to 180px at its own aspect, so it takes one fixed bucket
  // rather than a srcset (whose descriptors would re-state its natural size):
  // the large one, since the reader opened this to look at one person.
  open();
  const img = await screen.findByAltText("Seraphine avatar");
  expect(img.getAttribute("src")).toBe("/api/preview?w=160&v=bytes");
});

test("the avatar carries the detail's token, so it is cached immutable", async () => {
  (api.getCastDetail as any).mockResolvedValue({ ...detail("library"), avatar_v: "1a-2b" });
  open();
  const img = await screen.findByAltText("Seraphine avatar");
  expect(img.getAttribute("src")).toBe("/api/preview?w=160&v=bytes");
});

test("an unrecognized source renders no badge at all", async () => {
  // A store written by a newer build: better no chip than an empty bordered box.
  (api.getCastDetail as any).mockResolvedValue(detail("promoted"));
  const { container } = open();
  await screen.findByText("the drowned keeper");
  expect(container.querySelector(".cast-source")).toBeNull();
});

test("a location has no provenance badge", async () => {
  open({ type: "location", id: "the-docks" });
  await screen.findByText("The Docks");
  expect(screen.queryByText("Library", { selector: ".cast-source" })).toBeNull();
});

test("switching from an actor to a location clears the badge with the avatar", async () => {
  const { rerender } = open();
  await screen.findByText("Library", { selector: ".cast-source" });
  rerender(<RecordDrawer cid="c" sid="s" target={{ type: "location", id: "the-docks" }} onClose={() => {}} />);
  await screen.findByText("The Docks");
  await waitFor(() => expect(screen.queryByText("Library", { selector: ".cast-source" })).toBeNull());
});

test("a slow read for a character the drawer has left cannot badge her successor", async () => {
  // Two clicks, replies out of order. Losing this race mislabels a rewritten
  // card as the library's, which is the one thing the badge must never say.
  let landFirst = (_d: unknown) => {};
  (api.getCastDetail as any)
    .mockReturnValueOnce(new Promise((res) => { landFirst = res; }))
    .mockResolvedValueOnce({ ...detail("override"), id: "mara", name: "Mara" });
  const { rerender } = open();
  rerender(<RecordDrawer cid="c" sid="s" target={{ ...ACTOR, id: "mara" }} onClose={() => {}} />);
  await screen.findByText("Override", { selector: ".cast-source" });
  landFirst(detail("library"));
  await waitFor(() => expect(screen.getByText("Mara")).toBeInTheDocument());
  expect(screen.queryByText("Library", { selector: ".cast-source" })).toBeNull();
});

// ---- the keyboard (#193) ----
//
// This drawer covers the transcript, and until the shortcut registry landed it
// was the one overlay in the app that answered no key at all: the ✕ was the
// only way out, which the chooser beside it had never asked of anyone.

test("Escape closes it", async () => {
  const onClose = vi.fn();
  render(<RecordDrawer cid="c" sid="s" target={ACTOR} onClose={onClose} />);
  await screen.findByText("the drowned keeper");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("the close button still closes it", async () => {
  const onClose = vi.fn();
  render(<RecordDrawer cid="c" sid="s" target={ACTOR} onClose={onClose} />);
  await screen.findByText("the drowned keeper");
  fireEvent.click(screen.getByRole("button", { name: /close/i }));
  expect(onClose).toHaveBeenCalledTimes(1);
});

// It covers what it is opened over, so while it is up it owns the keyboard: a
// scene binding underneath must not fire through it.
test("it holds the keys of whatever it covers", async () => {
  const below = vi.fn();
  function Scene() {
    useHotkeys([{ keys: "n", run: below }]);
    return <RecordDrawer cid="c" sid="s" target={ACTOR} onClose={() => {}} />;
  }
  render(<Scene />);
  await screen.findByText("the drowned keeper");
  fireEvent.keyDown(window, { key: "n" });
  expect(below).not.toHaveBeenCalled();
});

test("closing it gives the keys back", async () => {
  const below = vi.fn();
  function Scene() {
    const [open, setOpen] = useState(true);
    useHotkeys([{ keys: "n", run: below }]);
    return open ? <RecordDrawer cid="c" sid="s" target={ACTOR} onClose={() => setOpen(false)} /> : null;
  }
  render(<Scene />);
  await screen.findByText("the drowned keeper");
  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByText("the drowned keeper")).toBeNull());
  fireEvent.keyDown(window, { key: "n" });
  expect(below).toHaveBeenCalledTimes(1);
});

// The body is markdown, and the drawer is where a dossier is actually read.
test("the body renders as markdown", async () => {
  (api.getCastDetail as any).mockResolvedValue(
    { ...detail("library"), body: "A record of **Seraphine**." });
  render(<RecordDrawer cid="c" sid="s" target={ACTOR} onClose={() => {}} />);
  expect((await screen.findByText("Seraphine", { selector: "strong" })).tagName).toBe("STRONG");
});

test("location drawer reads inherited art through the campaign overlay and uses a gallery fallback", async () => {
  (api.readRecordArt as any).mockImplementation((target: RecordArtTarget) => Promise.resolve(artRead(target, [
    artImage(target, "gallery_1", { image_id: null, provenance: "inherited", thumb: "/api/map?w=160&v=bytes" }),
  ])));
  open({ type: "location", id: "Saltmarch" });
  await waitFor(() => expect(screen.getByRole("button", { name: "View art" })).toBeEnabled());
  expect(api.readRecordArt).toHaveBeenCalledWith({ scope: { kind: "campaign", id: "c" }, kind: "locations", record_id: "Saltmarch" }, expect.objectContaining({ signal: expect.anything() }));
  expect(screen.getByAltText(/art preview/)).toHaveAttribute("src", "/api/map?w=160&v=bytes");
  fireEvent.click(screen.getByRole("button", { name: "View art" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
});
test("drawer art follows the scene's resolved actor version and reads no originals before opening", async () => {
  (api.getCastDetail as any).mockResolvedValue({ ...detail("override"), version: "older" });
  const { container } = open();
  await waitFor(() => expect(screen.getByRole("button", { name: "View art" })).toBeEnabled());
  expect(api.readRecordArt).toHaveBeenCalledWith(expect.objectContaining({ kind: "characters", version: "older" }), expect.anything());
  expect(container.querySelector('img[src="/api/original?v=bytes"]')).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "View art" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", "/api/original?v=bytes");
});
test("an art read failure never suppresses readable prose", async () => {
  (api.readRecordArt as any).mockRejectedValue(new Error("metadata unavailable"));
  open();
  expect(await screen.findByText("the drowned keeper")).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Art could not be read");
  expect(screen.queryByText("No art yet")).not.toBeInTheDocument();
});
test("drawer viewer Escape restores focus and leaves the parent drawer open", async () => {
  const onClose = vi.fn();
  render(<RecordDrawer cid="c" sid="s" target={ACTOR} onClose={onClose} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "View art" })).toBeEnabled());
  const opener = screen.getByRole("button", { name: "View art" });
  opener.focus(); fireEvent.click(opener);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
  expect(screen.getByText("the drowned keeper")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(opener).toHaveFocus();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
});
test("a late previous drawer art reply cannot install a portrait on its successor", async () => {
  let settle = (_data: unknown) => {};
  (api.readRecordArt as any).mockReturnValueOnce(new Promise((resolve) => { settle = resolve; }));
  const view = open();
  await waitFor(() => expect(api.readRecordArt).toHaveBeenCalledOnce());
  view.rerender(<RecordDrawer cid="c" sid="s" target={{ type: "location", id: "Saltmarch" }} onClose={vi.fn()} />);
  await screen.findByRole("button", { name: "View art" });
  const target = { scope: { kind: "campaign", id: "c" }, kind: "characters", record_id: "seraphine", version: "default" } as const;
  settle(artRead(target, [artImage(target, "avatar", { thumb: "/stale.png" })]));
  await waitFor(() => expect(screen.queryByAltText("Seraphine avatar")).not.toBeInTheDocument());
  expect(view.container.querySelector('img[src="/stale.png"]')).toBeNull();
});


test("drawer Open record carries campaign scope and the scene-resolved version to Art", async () => {
  vi.mocked(api.getCastDetail).mockResolvedValue({ ...detail("override"), version: "veiled" } as never);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "View art" }));
  fireEvent.click(screen.getByRole("button", { name: "Open record" }));
  expect(drawerPath).toBe("/campaigns/c/world/characters/seraphine?tab=art&v=veiled");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
