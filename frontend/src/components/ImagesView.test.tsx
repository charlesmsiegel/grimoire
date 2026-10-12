import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ImagesView as RealImagesView } from "./ImagesView";
import { createMemoryRouter, MemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { GalleryStateProvider } from "./art/galleryState";
function ImagesView(props: Parameters<typeof RealImagesView>[0]) {
  return <MemoryRouter><GalleryStateProvider><RealImagesView {...props} /></GalleryStateProvider></MemoryRouter>;
}

vi.mock("../api/client", () => ({
  api: {
    listWorldImages: vi.fn(), listCharacters: vi.fn(),
    listWorldLibrary: vi.fn(), getWorld: vi.fn(), putWorldCover: vi.fn(),
    deleteWorldCover: vi.fn(), worldCoverUrl: vi.fn(), worldImageUrl: vi.fn(),
    putWorldImage: vi.fn(), deleteWorldImage: vi.fn(),
    setWorldImageDescription: vi.fn(), draftWorldImageDescription: vi.fn(),
    listGreetings: vi.fn(), listUntaggedImages: vi.fn(), setImageSubjects: vi.fn(),
    listAppearances: vi.fn(), listCampaignGallery: vi.fn(),
    listCampaignImages: vi.fn(), campaignImageUrl: (cid: string, name: string) => "/api/campaigns/" + cid + "/images/" + name,
  },
}));
import { api } from "../api/client";
import { artChanged } from "../appEvents";

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
  (api.worldImageUrl as any).mockImplementation((wid: string, name: string, opts?: { w?: number; v?: string }) =>
    "/api/worlds/" + wid + "/images/" + name + "?v=" + opts?.v + (opts?.w ? "&w=" + opts.w : ""));
  (api.listWorldImages as any).mockResolvedValue([AVATAR, SKETCH, QUAY, TAGGED, UNTAGGED]);
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine" }]);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listCampaignImages as any).mockResolvedValue({ images: [{ name: "Saltmarch", ext: "png", v: "bytes" }], hidden: [] });
  (api.listCampaignGallery as any).mockResolvedValue([AVATAR, SKETCH, QUAY, TAGGED, UNTAGGED]);
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
  expect(api.listWorldImages).toHaveBeenCalledWith("realm", false);
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
  expect(screen.getByRole("button", { name: /^View Saltmarch/ })).toBeInTheDocument();
});

test("a tile opens the image read-only, with the full-size art and its metadata", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("In half-plate, at the quay."));
  await act(async () => {});
  const full = within(screen.getByRole("dialog", { name: "Image viewer" })).getByAltText("In half-plate, at the quay.");
  expect(full.getAttribute("src")).toBe("/img/avatar?v=1");   // not the thumbnail
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.getByText("In half-plate, at the quay.")).toBeInTheDocument();
  // Nothing here edits: the two sidecars are written in the editors that own
  // them, so where Edit sits in every other detail view is the way back.
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
  await act(async () => {});
  expect(tiles()).toHaveLength(5);
});

test("a described-but-blank image is finished, and says so rather than reading as a gap", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("Saltmarch — gallery_1")); // described, no text
  await act(async () => {});
  expect(screen.getByText("No description (reviewed)")).toBeInTheDocument();
});

test("an undescribed image says what is still missing", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("Seraphine — gallery_1"));   // SKETCH
  await act(async () => {});
  expect(screen.getByText("No description")).toBeInTheDocument();
  // The asset version, because a character's art is per version and two
  // versions can hold different pictures under the same name.
  expect(within(screen.getByRole("dialog", { name: "Image viewer" })).getByText("Seraphine / older / gallery_1")).toBeInTheDocument();
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

test("a read taken after a write is issued fresh, not shared with an older one", async () => {
  // The client shares an in-flight GET by path. A refresh that follows a
  // subjects PUT is asking what the store holds NOW, and a promise issued
  // before that write answers a question nobody asked -- leaving the tile just
  // tagged marked unfinished until the view is remounted.
  await renderView();
  expect(api.listWorldImages).toHaveBeenLastCalledWith("realm", false);   // mount shares

  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "No subjects" }));
  await act(async () => {});
  expect(api.listWorldImages).toHaveBeenLastCalledWith("realm", true);

  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await act(async () => {});
  expect(api.listWorldImages).toHaveBeenLastCalledWith("realm", true);
  expect(api.listUntaggedImages).toHaveBeenLastCalledWith("realm", true);
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

// ---- the appeared filter, offered only when a campaign is being read for ----

/** One row of the appearance record: this actor has been on stage. */
const appeared = (kind: string, id: string) => (
  { kind, id, version: "default", role: "npc", scenes: ["001--a"] });

test("a gallery read for no campaign offers no appeared filter", async () => {
  await act(async () => { render(<ImagesView wid="w" />); });
  expect(screen.queryByRole("button", { name: /appeared/i })).toBeNull();
  // and asks nothing about a campaign it was not given
  expect(api.listAppearances).not.toHaveBeenCalled();
});

test("reading for a campaign filters the gallery to actors that have appeared", async () => {
  // Seraphine has been on stage; the world's other art has not, and locations
  // are not cast at all.
  (api.listAppearances as any).mockResolvedValue([appeared("characters", "seraphine")]);
  await act(async () => { render(<ImagesView wid="w" forCampaign="run" />); });

  // off by default: the gallery is still the world's, and the filter is a
  // narrowing the reader asks for
  expect(screen.getByAltText("In half-plate, at the quay.")).toBeInTheDocument();
  expect(screen.getByAltText("Saltmarch — gallery_1")).toBeInTheDocument();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /appeared in this campaign/i }));
  });
  expect(screen.getByAltText("In half-plate, at the quay.")).toBeInTheDocument();
  // the location is not a member of the cast, so it goes
  expect(screen.queryByAltText("Saltmarch — gallery_1")).toBeNull();
  // and so does greeting art, which no roster entry names
  expect(screen.queryByAltText("Two figures on the quay.")).toBeNull();
});

test("an actor in the world but never on stage is filtered out", async () => {
  (api.listAppearances as any).mockResolvedValue([appeared("characters", "winifred")]);
  await act(async () => { render(<ImagesView wid="w" forCampaign="run" />); });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /appeared in this campaign/i }));
  });
  expect(screen.queryByAltText("In half-plate, at the quay.")).toBeNull();
  expect(screen.getByText("No images match these filters")).toBeInTheDocument();
});

test("a failed appearance read costs the filter, not the gallery", async () => {
  // The gallery is this view's subject and the filter is an extra: one read
  // failing must not blank the other, or take the view down with it.
  (api.listAppearances as any).mockRejectedValue(new Error("nope"));
  await act(async () => { render(<ImagesView wid="w" forCampaign="run" />); });
  expect(screen.getByAltText("In half-plate, at the quay.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /appeared in this campaign/i })).toBeNull();
});

// ---- which gallery is read ----

test("with no campaign the world's gallery is what is read", async () => {
  await act(async () => { render(<ImagesView wid="w" />); });
  expect(api.listWorldImages).toHaveBeenCalledWith("w", false);
  expect(api.listCampaignGallery).not.toHaveBeenCalled();
});

test("reading for a campaign reads that campaign's gallery instead", async () => {
  // The campaign's own art lives in the campaign root: a world sweep cannot
  // see it, so a campaign reader looking at a world listing is being shown
  // pictures the campaign may not even use.
  const OWN = {
    kind: "characters", id: "seraphine", vid: "default", name: "gallery_9",
    record_name: "Seraphine", url: "/img/own?v=1", thumb: "/img/own?w=320&v=1",
    ext: "webp", described: true, description: "Campaign-only art.",
  };
  (api.listCampaignGallery as any).mockResolvedValue([AVATAR, OWN]);
  await act(async () => { render(<ImagesView wid="w" forCampaign="run" />); });

  expect(api.listCampaignGallery).toHaveBeenCalledWith("run", false);
  expect(api.listWorldImages).not.toHaveBeenCalled();
  expect(screen.getByAltText("Campaign-only art.")).toBeInTheDocument();
});


// ---- the World art tab ----------------------------------------------------

test("the World art tab edits the world's own library, and the gallery stays a browser", async () => {
  (api.listWorldLibrary as any).mockResolvedValue([
    { name: "coastline", ext: "png", v: "w1", description: "a rocky shore", described: true },
  ]);
  (api.getWorld as any).mockResolvedValue({ meta: { cover: "" } });
  (api.deleteWorldImage as any).mockResolvedValue({ ok: true });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<ImagesView wid="realm" />);
  fireEvent.click(await screen.findByRole("tab", { name: /World art/ }));

  expect(await screen.findByText("World images")).toBeTruthy();
  expect(screen.getByText("World cover")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Delete coastline" }));
  // The confirmation names the consequence that is not local: these are read
  // through by every campaign on the world.
  expect(confirm.mock.calls[0][0]).toContain("Every campaign");
  await waitFor(() => expect(api.deleteWorldImage).toHaveBeenCalledWith("realm", "coastline"));
  confirm.mockRestore();
});

test("the World art tab is offered even with a campaign open", async () => {
  // `shell/rail.ts` appends `&for=<cid>` to the Images row whenever a campaign
  // is open, so hiding this tab under that flag would put the world's only art
  // editor out of reach for as long as one is being played.
  (api.listCampaignGallery as any).mockResolvedValue([]);
  (api.listWorldLibrary as any).mockResolvedValue([]);
  (api.getWorld as any).mockResolvedValue({ meta: { cover: "" } });

  render(<ImagesView wid="realm" forCampaign="run" />);
  expect(await screen.findByRole("tab", { name: /World art/ })).toBeTruthy();
});

test("search composes with kind, unfinished, and appearance filters", async () => {
  (api.listAppearances as any).mockResolvedValue([{ kind: "characters", id: "seraphine" }]);
  render(<ImagesView wid="realm" forCampaign="c" />);
  await screen.findByRole("button", { name: /Appeared in this campaign/ });
  fireEvent.change(screen.getByRole("searchbox", { name: "Search images" }), { target: { value: "OLDER" } });
  fireEvent.click(screen.getByRole("button", { name: /^Characters/ }));
  fireEvent.click(screen.getByRole("button", { name: /Needs a description/ }));
  fireEvent.click(screen.getByRole("button", { name: /Appeared in this campaign/ }));
  expect(tiles()).toHaveLength(1);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "not present" } });
  expect(screen.getByText("No images match these filters")).toBeVisible();
});
test("the common viewer sequence excludes greetings and follows current filters", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("In half-plate, at the quay."));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.getByText("1 / 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next image" }));
  expect(screen.getByRole("link", { name: "Open original" })).toHaveAttribute("href", SKETCH.url);
  fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
  fireEvent.click(screen.getByRole("button", { name: /^Locations/ }));
  fireEvent.click(screen.getByAltText("Saltmarch — gallery_1"));
  expect(screen.queryByRole("button", { name: "Next image" })).not.toBeInTheDocument();
});
test("gallery record navigation and Back restore filters, selected occurrence, and actual scroll owner", async () => {
  const NativeRequest = globalThis.Request;
  class TestRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      const { signal, ...rest } = init ?? {};
      super(input, rest);
      if (signal) Object.defineProperty(this, "signal", { value: signal });
    }
  }
  vi.stubGlobal("Request", TestRequest);
  const router = createMemoryRouter([{ element: <GalleryStateProvider><Outlet /></GalleryStateProvider>, children: [
    { path: "/worlds/realm/images", element: <main className="shell-main"><RealImagesView wid="realm" /></main> },
    { path: "/worlds/realm/locations/saltmarch", element: <p>Record Art</p> },
  ] }], { initialEntries: ["/worlds/realm/images"] });
  const view = render(<RouterProvider router={router} />);
  try {
    await screen.findByRole("button", { name: /^Locations/ });
    fireEvent.click(screen.getByRole("button", { name: /^Locations/ }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "salt" } });
    const scroll = view.container.querySelector(".shell-main") as HTMLElement;
    scroll.scrollTop = 341; fireEvent.scroll(scroll);
    fireEvent.click(screen.getByAltText("Saltmarch — gallery_1"));
    fireEvent.click(screen.getByRole("button", { name: "Open record" }));
    await screen.findByText("Record Art");
    expect(router.state.location.search).toBe("?tab=art");
    await act(() => router.navigate(-1));
    await screen.findByRole("searchbox");
    expect(screen.getByRole("searchbox")).toHaveValue("salt");
    expect(screen.getByRole("button", { name: /^Locations/ })).toHaveClass("active");
    expect(view.container.querySelector(".shell-main")?.scrollTop).toBe(341);
    expect(screen.getByRole("button", { name: /^View Saltmarch/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
  } finally { router.dispose(); vi.unstubAllGlobals(); }
});
test("a greeting detail links only to its dedicated editor", async () => {
  await renderView();
  fireEvent.click(screen.getByAltText("Two figures on the quay."));
  expect(screen.getByRole("link", { name: "Open greeting" })).toHaveAttribute("href", "/worlds/realm/greetings/dawn");
  expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
});

test("campaign library gallery navigation reaches the existing campaign manager", async () => {
  (api.listCampaignGallery as any).mockResolvedValue([{ ...AVATAR, kind: "campaign", id: "c", record_name: "Library", name: "Saltmarch" }]);
  // The deep link is a known world gallery entry carrying the campaign; it
  // never manufactures a /campaigns/:cid/world/images route.
  const view = render(<MemoryRouter initialEntries={["/worlds/realm/images?for=c"]}>
    <RealImagesView wid="realm" forCampaign="c" /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /^View Library/ }));
  fireEvent.click(screen.getByRole("button", { name: "Manage images" }));
  expect(await screen.findByRole("dialog", { name: "Campaign image library" })).toBeInTheDocument();
  expect(api.listCampaignImages).toHaveBeenCalledWith("c", false);
  view.unmount();
});

test("a delayed full-read companion cannot overwrite a newer gallery invalidation", async () => {
  let finishCompanion!: (rows: Awaited<ReturnType<typeof api.listCharacters>>) => void;
  vi.mocked(api.listCharacters).mockImplementationOnce(() => new Promise(resolve => { finishCompanion = resolve; }));
  render(<ImagesView wid="realm" />);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalled());
  vi.mocked(api.listWorldImages).mockResolvedValue([QUAY]);
  artChanged();
  await screen.findByRole("button", { name: /View Saltmarch/ });
  await act(async () => finishCompanion([]));
  expect(screen.queryByRole("button", { name: /View Seraphine/ })).not.toBeInTheDocument();
});

test("failed newer gallery invalidation reports failure while an older full read is pending", async () => {
  vi.mocked(api.listCharacters).mockImplementationOnce(() => new Promise(() => {}));
  render(<ImagesView wid="realm" />);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalled());
  vi.mocked(api.listWorldImages).mockRejectedValue(new Error("Art unreadable"));
  artChanged();
  expect(await screen.findByRole("alert")).toHaveTextContent("Art unreadable");
  expect(screen.queryByText(/no art yet/i)).not.toBeInTheDocument();
});

test("campaign greeting details preserve their effective campaign scope", async () => {
  render(<ImagesView wid="realm" forCampaign="c" />);
  fireEvent.click(await screen.findByAltText("Two figures on the quay."));
  expect(screen.getByRole("link", { name: "Open greeting" })).toHaveAttribute("href", "/campaigns/c/world/greetings/dawn");
});

test("removed combined-gallery selection restores focus to the owning gallery", async () => {
  const view = render(<ImagesView wid="realm" />);
  fireEvent.click(await screen.findByRole("button", { name: /^View Saltmarch/ }));
  vi.mocked(api.listWorldImages).mockResolvedValue([]);
  act(artChanged);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(view.container.querySelector(".images-view")).toHaveFocus();
});


test.each(["listCharacters", "listGreetings", "listUntaggedImages"] as const)("gallery invalidation cannot hide a failed %s companion", async method => {
  let reject!: (error: Error) => void;
  vi.mocked(api[method]).mockImplementationOnce(() => new Promise<never>((_resolve, fail) => { reject = fail; }));
  render(<ImagesView wid="realm" />);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalled());
  vi.mocked(api.listWorldImages).mockResolvedValue([QUAY]);
  act(artChanged);
  await screen.findByRole("button", { name: /View Saltmarch/ });
  await act(async () => reject(new Error("Required tagging data unreadable")));
  expect(screen.getByRole("alert")).toHaveTextContent("Required tagging data unreadable");
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  expect(screen.queryByText("Every greeting image in this world has been tagged.")).not.toBeInTheDocument();
  // A subsequent gallery-only success still cannot repair the failed companion.
  act(artChanged);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalledTimes(3));
  expect(screen.getByRole("alert")).toHaveTextContent("Required tagging data unreadable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText(/Tagging 1 \/ 1 \u2014 Saltmarch dusk/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("an older full gallery failure cannot replace a newer gallery success", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(api.listWorldImages).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  render(<ImagesView wid="realm" />);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalled());
  vi.mocked(api.listWorldImages).mockResolvedValue([QUAY]);
  act(artChanged);
  await screen.findByRole("button", { name: /View Saltmarch/ });
  await act(async () => reject(new Error("Older gallery unreadable")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  expect(screen.getByText(/Tagging 1 \/ 1 \u2014 Saltmarch dusk/)).toBeInTheDocument();
});


test("a refreshed gallery does not label pending tagging companions as empty", async () => {
  let finish!: (rows: Awaited<ReturnType<typeof api.listCharacters>>) => void;
  vi.mocked(api.listCharacters).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<ImagesView wid="realm" />);
  await waitFor(() => expect(api.listWorldImages).toHaveBeenCalled());
  vi.mocked(api.listWorldImages).mockResolvedValue([QUAY]);
  act(artChanged);
  await screen.findByRole("button", { name: /View Saltmarch/ });
  fireEvent.click(screen.getByRole("tab", { name: /Tagging queue/ }));
  expect(screen.queryByText("Every greeting image in this world has been tagged.")).not.toBeInTheDocument();
  await act(async () => finish([]));
  expect(screen.getByText(/Tagging 1 \/ 1 \u2014 Saltmarch dusk/)).toBeInTheDocument();
});
