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
  ["library", "Library"],
  ["override", "Override"],
  ["emergent", "Emergent"],
])("a %s cast member is badged %s", async (source, label) => {
  (api.getCastDetail as any).mockResolvedValue(detail(source));
  open();
  const chip = await screen.findByText(label, { selector: ".cast-source" });
  // The class carries the state, so the three are told apart by colour and not
  // only by the word (#99).
  expect(chip).toHaveClass(source);
  expect(chip.getAttribute("title")).toBeTruthy();
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
