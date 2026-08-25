import { act, fireEvent, render, screen } from "@testing-library/react";
import { ImagesView } from "./ImagesView";

vi.mock("../api/client", () => ({
  api: {
    listWorldImages: vi.fn(), listCharacters: vi.fn(),
    listGreetings: vi.fn(), listUntaggedImages: vi.fn(), setImageSubjects: vi.fn(),
  },
}));
import { api } from "../api/client";

const AVATAR = {
  kind: "characters", id: "seraphine", vid: "default", name: "avatar",
  record_name: "Seraphine", url: "/img/avatar?v=1", thumb: "/img/avatar?w=320&v=1",
  ext: "png", described: true, description: "In half-plate, at the quay.",
};
const SKETCH = {
  kind: "characters", id: "seraphine", vid: "older", name: "gallery_1",
  record_name: "Seraphine", url: "/img/sketch?v=1", thumb: "/img/sketch?w=320&v=1",
  ext: "png", described: false, description: "",
};
const QUAY = {
  kind: "locations", id: "saltmarch", vid: "default", name: "gallery_1",
  record_name: "Saltmarch", url: "/img/quay?v=1", thumb: "/img/quay?w=320&v=1",
  ext: "jpg", described: true, description: "",
};
const TAGGED = {
  kind: "greetings", id: "dawn", vid: "default", name: "art_1",
  record_name: "Saltmarch dawn", url: "/img/dawn?v=1", thumb: "/img/dawn?w=320&v=1",
  ext: "png", described: true, description: "Two figures on the quay.",
  subjects: ["seraphine"],
};
const UNTAGGED = {
  kind: "greetings", id: "dusk", vid: "default", name: "art_1",
  record_name: "Saltmarch dusk", url: "/img/dusk?v=1", thumb: "/img/dusk?w=320&v=1",
  ext: "png", described: true, description: "", subjects: null,
};

const QUEUE = [{ gid: "dusk", greeting_name: "Saltmarch dusk", name: "art_1",
                 url: "/img/dusk?v=1" }];

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorldImages as any).mockResolvedValue([AVATAR, SKETCH, QUAY, TAGGED, UNTAGGED]);
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine" }]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "dusk", name: "Saltmarch dusk", present: ["seraphine"] }]);
  (api.listUntaggedImages as any).mockResolvedValue(QUEUE);
  (api.setImageSubjects as any).mockResolvedValue({ ok: true });
});

async function renderView() {
  const { container } = render(<ImagesView wid="realm" />);
  await act(async () => {});
  return container;
}

/** The open image's sidebar — where the metadata is, as opposed to the rail and
 *  the grid, which carry some of the same words. */
const sidebar = (container: HTMLElement) =>
  container.querySelector(".detail-sidebar") as HTMLElement;

const tiles = () => screen.getAllByRole("button").filter((b) => b.className.includes("gallery-tile"));

test("the gallery is one request for the whole world, and shows a tile per image", async () => {
  await renderView();
  expect(api.listWorldImages).toHaveBeenCalledWith("realm");
  expect(tiles()).toHaveLength(5);
  // The tile draws the `?w=` downscale, never the original: a world's art at
  // full resolution is tens of megabytes for pictures rendered at 154px.
  const img = screen.getByAltText("In half-plate, at the quay.");
  expect(img.getAttribute("src")).toBe("/img/avatar?w=320&v=1");
});

test("the rail filters by kind, and names no kind the world has no art for", async () => {
  await renderView();
  expect(screen.getByRole("button", { name: /All images/ }).textContent).toContain("5");
  expect(screen.queryByRole("button", { name: /^PCs/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Locations/ }));
  await act(async () => {});
  expect(tiles()).toHaveLength(1);
  expect(screen.getByText("Saltmarch")).toBeInTheDocument();
});

test("a tile opens the image read-only, with the full-size art and its metadata", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("In half-plate, at the quay."));
  await act(async () => {});
  const full = screen.getByAltText("In half-plate, at the quay.");
  expect(full.getAttribute("src")).toBe("/img/avatar?v=1");   // not the thumbnail
  expect(full.className).toContain("gallery-full");
  expect(screen.getByText("In half-plate, at the quay.")).toBeInTheDocument();
  // Nothing here edits: the two sidecars are written in the editors that own
  // them, so where Edit sits in every other detail view is the way back.
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Back to the gallery" }));
  await act(async () => {});
  expect(tiles()).toHaveLength(5);
});

test("a described-but-blank image is finished, and says so rather than reading as a gap", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("Saltmarch — gallery_1")); // described, no text
  await act(async () => {});
  expect(screen.getByText(/deliberately left blank/)).toBeInTheDocument();
});

test("an undescribed image says what is still missing", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("Seraphine — gallery_1"));   // SKETCH
  await act(async () => {});
  expect(screen.getByText(/Not described yet/)).toBeInTheDocument();
  // The asset version, because a character's art is per version and two
  // versions can hold different pictures under the same name.
  expect(screen.getByText("version older")).toBeInTheDocument();
});

test("greeting subjects distinguish untagged from tagged-as-nobody", async () => {
  (api.listWorldImages as any).mockResolvedValue([
    TAGGED, UNTAGGED,
    { ...TAGGED, id: "noon", record_name: "Saltmarch noon",
      description: "An empty quay at noon.", subjects: [] }]);
  await renderView();
  fireEvent.click(screen.getByAltText("Two figures on the quay."));
  await act(async () => {});
  expect(screen.getByText("Seraphine")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Back to the gallery" }));
  await act(async () => {});
  fireEvent.click(screen.getByAltText("Saltmarch dusk — art_1"));   // UNTAGGED
  await act(async () => {});
  expect(screen.getByText(/Not tagged yet/)).toBeInTheDocument();
});

test("a character's art has no subjects section at all", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("In half-plate, at the quay."));
  await act(async () => {});
  // `undefined` there means "this kind has no such sidecar", not "nobody is in
  // it" — so the section is absent rather than empty.
  expect(screen.queryByText("Subjects")).not.toBeInTheDocument();
});

test("greeting art is judged on its subjects alone, never on a description", async () => {
  // Nothing in this app describes a greeting image -- no route, and the describe
  // backlog does not walk that base -- so counting one undescribed would file
  // every greeting image in a backlog that can never be worked.
  (api.listWorldImages as any).mockResolvedValue([
    { ...TAGGED, described: false, description: "" }]);
  const container = await renderView();
  expect(screen.getByRole("button", { name: /Needs a description or subjects/ }).textContent)
    .toContain("0");
  fireEvent.click(screen.getByAltText("Saltmarch dawn — art_1"));
  await act(async () => {});
  expect(sidebar(container).textContent).not.toContain("Not described yet");
  expect(sidebar(container).textContent).toContain("Subjects");
});

test("the unfinished filter gathers both backlogs into one answer", async () => {
  await renderView();
  const filter = screen.getByRole("button", { name: /Needs a description or subjects/ });
  expect(filter.textContent).toContain("2");   // one undescribed, one untagged
  fireEvent.click(filter);
  await act(async () => {});
  expect(tiles()).toHaveLength(2);
  expect(filter).toHaveAttribute("aria-pressed", "true");
});

test("the queue tab is the tagging queue itself, over the untagged backlog", async () => {
  await renderView();
  expect(screen.getByRole("tab", { name: /Tagging queue/ }).textContent).toContain("1");
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  expect(screen.getByText(/Tagging 1 \/ 1 — Saltmarch dusk/)).toBeInTheDocument();
});

test("a world whose greeting art is all tagged is told so, not shown an empty stepper", async () => {
  (api.listUntaggedImages as any).mockResolvedValue([]);
  await renderView();
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  expect(screen.getByText(/has been tagged/)).toBeInTheDocument();
});

test("a save in the queue re-reads the gallery, so a tile stops being unfinished", async () => {
  await renderView();
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  (api.listWorldImages as any).mockResolvedValue([
    AVATAR, SKETCH, QUAY, TAGGED, { ...UNTAGGED, subjects: ["seraphine"] }]);
  fireEvent.click(screen.getByRole("button", { name: "No subjects" }));
  await act(async () => {});
  expect(api.listWorldImages).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("tab", { name: /Gallery/ }));
  await act(async () => {});
  expect(screen.getByRole("button", { name: /Needs a description or subjects/ }).textContent)
    .toContain("1");
});

test("a save does not re-read the backlog the queue is still walking", async () => {
  // `TaggingQueue` copies the backlog on mount and measures progress against
  // the prop. Refreshing it underneath a running queue leaves its internal list
  // at the original length while `total` shrinks -- so the second of two images
  // announces itself as "1 / 1", and a skipped image reappears in a prop the
  // local list has already walked past.
  (api.listUntaggedImages as any).mockResolvedValue([
    { gid: "dusk", greeting_name: "Saltmarch dusk", name: "art_1", url: "/img/dusk?v=1" },
    { gid: "noon", greeting_name: "Saltmarch noon", name: "art_1", url: "/img/noon?v=1" },
  ]);
  await renderView();
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  expect(screen.getByText(/Tagging 1 \/ 2 — Saltmarch dusk/)).toBeInTheDocument();

  (api.listUntaggedImages as any).mockResolvedValue([
    { gid: "noon", greeting_name: "Saltmarch noon", name: "art_1", url: "/img/noon?v=1" }]);
  fireEvent.click(screen.getByRole("button", { name: "No subjects" }));
  await act(async () => {});
  expect(screen.getByText(/Tagging 2 \/ 2 — Saltmarch noon/)).toBeInTheDocument();
  expect(api.listUntaggedImages).toHaveBeenCalledTimes(1);

  // Closing is when it IS re-read -- nothing is measuring progress any more.
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await act(async () => {});
  expect(api.listUntaggedImages).toHaveBeenCalledTimes(2);
});

test("a failed read is reported, and reports nothing else", async () => {
  // An empty gallery is the one wrong answer this view must never give, and a
  // failure rendered NEXT TO "this world has no art yet" gives it anyway -- in
  // a second voice, which is the one the reader believes.
  (api.listWorldImages as any).mockRejectedValue(new Error("world not found"));
  await renderView();
  expect(screen.getByRole("alert").textContent).toContain("world not found");
  expect(tiles()).toHaveLength(0);
  expect(screen.queryByText(/no art yet/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Reading the world/)).not.toBeInTheDocument();

  // ...and the queue tab must not claim the backlog is empty either.
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  expect(screen.queryByText(/has been tagged/)).not.toBeInTheDocument();

  (api.listWorldImages as any).mockResolvedValue([AVATAR]);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await act(async () => {});
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /Gallery/ }));
  await act(async () => {});
  expect(tiles()).toHaveLength(1);
});

test("a world with no art says so rather than showing an empty frame", async () => {
  (api.listWorldImages as any).mockResolvedValue([]);
  (api.listUntaggedImages as any).mockResolvedValue([]);
  await renderView();
  expect(screen.getByText(/no art yet/)).toBeInTheDocument();
});
