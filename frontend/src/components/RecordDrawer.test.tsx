import { render, screen, waitFor } from "@testing-library/react";
import { RecordDrawer } from "./RecordDrawer";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCastDetail: vi.fn(),
      readEntity: vi.fn(),
      actorImageUrl: (_sc: { id: string }, k: string, a: string, v: string) => `/img/${k}/${a}/${v}`,
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
