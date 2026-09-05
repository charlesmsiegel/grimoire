import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PostImagePicker, freeName, insertion, nameFromFile } from "./PostImagePicker";

vi.mock("../api/client", () => ({
  api: {
    listCampaignImages: vi.fn(),
    restoreCampaignImage: vi.fn(),
    putCampaignImage: vi.fn(),
    deleteCampaignImage: vi.fn(),
    setCampaignImageDescription: vi.fn(),
    draftCampaignImageDescription: vi.fn(),
    readCharacter: vi.fn(),
    readPC: vi.fn(),
    campaignImageUrl: (cid: string, name: string) => `/api/campaigns/${cid}/images/${name}`,
    actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string) =>
      `/api/campaigns/${sc.id}/${k}/${a}/versions/${v}/images/${n}`,
  },
}));
import { api } from "../api/client";

const file = (name: string) => new File(["png"], name, { type: "image/png" });

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCampaignImages as any).mockResolvedValue({
    images: [
      { name: "coastline", ext: "png", v: "a1" },
      { name: "the-inn", ext: "jpg", v: "b2" },
    ],
    hidden: [],
  });
  (api.putCampaignImage as any).mockResolvedValue({ name: "x", ext: "png", v: "c3" });
  (api.deleteCampaignImage as any).mockResolvedValue({ ok: true });
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "sera", name: "Seraphine", default_version: "default" },
    versions: [
      { id: "default", name: "Now", card: {}, images: ["avatar", "gallery_1"] },
      { id: "young", name: "Younger", card: {}, images: ["avatar"] },
      { id: "bare", name: "No art", card: {}, images: [] },
    ],
  });
  (api.readPC as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", tags: [], default_version: "default" },
    versions: [{ id: "default", name: "Now", persona: {}, images: ["avatar"] }],
  });
});

// ---- the pure pieces -------------------------------------------------------

test("an uploaded file's own name becomes the stored name, minus what a URL cannot carry", () => {
  expect(nameFromFile("Coast at Dusk.png")).toBe("Coast-at-Dusk");
  expect(nameFromFile("map(1).jpeg")).toBe("map-1");
  expect(nameFromFile("海岸線.png")).toBe("海岸線");
  expect(nameFromFile("...png")).toBe("image");   // nothing addressable survived
});

test("a name already taken gets the first free suffix, case folded", () => {
  expect(freeName("map", [])).toBe("map");
  expect(freeName("map", ["map"])).toBe("map-2");
  expect(freeName("map", ["map", "map-2", "map-3"])).toBe("map-4");
  // On Windows and macOS `Coast.png` IS `coast.png`, so a case-sensitive
  // comparison would hand out a name that cannot be claimed without replacing
  // the image already there.
  expect(freeName("Coast", ["coast"])).toBe("Coast-2");
  expect(freeName("Coast", ["coast", "COAST-2"])).toBe("Coast-3");
});

test("the inserted url carries no cache token", () => {
  // A `?v=` URL is answered `immutable, max-age=1y`, and this one is written
  // into a transcript that outlives every cache: replacing the image under the
  // same name would leave the post pinned to bytes that are gone for a year.
  expect(insertion("coastline", "/api/campaigns/run/images/coastline")).not.toMatch(/[?&]v=/);
});

test("the alt text defaults to the image's name, not to nothing", () => {
  // A plain-text export and a text-only reader see the alt text and nothing
  // else, so an empty one reads as though there were no image at all.
  expect(insertion("coastline", "/api/campaigns/run/images/coastline"))
    .toBe("![coastline](/api/campaigns/run/images/coastline)");
});

// ---- the narrator's picker: the campaign's own library ----------------------

test("a narrator post is offered the campaign's images", async () => {
  const onInsert = vi.fn();
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={onInsert} onClose={() => {}} />);
  await screen.findByRole("button", { name: "Insert coastline" });
  expect(api.listCampaignImages).toHaveBeenCalledWith("run");
  expect(api.readCharacter).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Insert the-inn" }));
  expect(onInsert).toHaveBeenCalledWith("![the-inn](/api/campaigns/run/images/the-inn)");
});

test("an empty library says so, and an upload lands under a name from the file", async () => {
  (api.listCampaignImages as any).mockResolvedValue({ images: [], hidden: [] });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  expect(await screen.findByText(/no campaign images yet/i)).toBeTruthy();

  (api.listCampaignImages as any).mockResolvedValue({ images: [{ name: "Coast-at-Dusk", ext: "png", v: "z" }], hidden: [] });
  fireEvent.change(screen.getByLabelText(/add an image/i),
                   { target: { files: [file("Coast at Dusk.png")] } });
  await waitFor(() => expect(api.putCampaignImage)
    .toHaveBeenCalledWith("run", "Coast-at-Dusk", expect.any(File)));
  // and the listing is re-read, so the new tile is the server's answer rather
  // than a second copy of the library kept in this component
  expect(await screen.findByRole("button", { name: "Insert Coast-at-Dusk" })).toBeTruthy();
});

test("an upload whose name is taken does not overwrite the image already there", async () => {
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  await screen.findByRole("button", { name: "Insert coastline" });
  fireEvent.change(screen.getByLabelText(/add an image/i),
                   { target: { files: [file("coastline.png")] } });
  await waitFor(() => expect(api.putCampaignImage)
    .toHaveBeenCalledWith("run", "coastline-2", expect.any(File)));
});

test("a rejected upload shows the server's reason", async () => {
  (api.putCampaignImage as any).mockRejectedValue({ detail: "unsupported image type" });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  await screen.findByRole("button", { name: "Insert coastline" });
  fireEvent.change(screen.getByLabelText(/add an image/i),
                   { target: { files: [file("notes.png")] } });
  expect(await screen.findByText("unsupported image type")).toBeTruthy();
});

test("a library whose listing failed offers no upload", async () => {
  // An upload names itself by stepping around the names already there, so
  // uploading against a listing that never arrived would propose one that is
  // taken and quietly replace an image the reader still has.
  (api.listCampaignImages as any).mockRejectedValue({ detail: "campaign not found" });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  expect(await screen.findByText("campaign not found")).toBeTruthy();
  expect((screen.getByLabelText(/add an image/i) as HTMLInputElement).disabled).toBe(true);
  // and it does not also claim there are no images: the read is what failed
  expect(screen.queryByText(/no campaign images yet/i)).toBeNull();
});

test("removing a library image is confirmed first, and names what it costs", async () => {
  // One of these can already be linked from forty posts, which a cover never is.
  const ok = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Remove coastline" }));
  expect(ok.mock.calls[0][0]).toMatch(/coastline[\s\S]*broken image/i);
  await waitFor(() => expect(api.deleteCampaignImage).toHaveBeenCalledWith("run", "coastline"));
});

test("declining the removal removes nothing", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Remove coastline" }));
  expect(api.deleteCampaignImage).not.toHaveBeenCalled();
});

test("Escape closes the picker", async () => {
  const onClose = vi.fn();
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={onClose} />);
  fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

// Escape mirrors Cancel, which is not disabled mid-upload -- and neither is
// the scrim. A key that refused while the two mouse paths still worked would
// be the one dismissal keyboard users cannot reach (PR #400 review).
test("Escape closes mid-upload, exactly as Cancel and the scrim do", async () => {
  const onClose = vi.fn();
  (api.putCampaignImage as any).mockImplementation(() => new Promise(() => {}));
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={onClose} />);
  const input = await screen.findByLabelText(/add an image/i);
  fireEvent.change(input, { target: { files: [new File(["x"], "reeds.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putCampaignImage).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /^cancel$/i })).not.toBeDisabled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("the file input is a real, focusable control rather than a styled label", async () => {
  // A `display: none` input inside a label looks tidier and is unreachable by
  // keyboard. It also has to offer only what the server will store: `image/*`
  // would offer AVIF and BMP, which the upload refuses.
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  const input = await screen.findByLabelText(/add an image/i) as HTMLInputElement;
  expect(input.type).toBe("file");
  expect(input.accept).toBe("image/png,image/jpeg,image/gif,image/webp");
  expect(getComputedStyle(input).display).not.toBe("none");
});

// ---- an actor's picker -----------------------------------------------------

test("a character post is offered that character's art, the version that spoke first", async () => {
  const onInsert = vi.fn();
  render(<PostImagePicker cid="run"
                          target={{ kind: "characters", id: "sera", version: "young",
                                    name: "Seraphine" }}
                          onInsert={onInsert} onClose={() => {}} />);
  const headings = (await screen.findAllByRole("heading", { level: 4 })).map((h) => h.textContent);
  // the version the roster locked leads, the other is still reachable, and the
  // version holding no art at all is not offered as an empty group
  expect(headings).toEqual(["Younger — spoke here", "Now"]);
  expect(api.listCampaignImages).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Insert gallery_1" }));
  expect(onInsert).toHaveBeenCalledWith(
    "![gallery_1](/api/campaigns/run/characters/sera/versions/default/images/gallery_1)");
});

test("a PC post reads the PC, not the character, surface", async () => {
  render(<PostImagePicker cid="run"
                          target={{ kind: "pcs", id: "mara", version: "default", name: "Mara" }}
                          onInsert={() => {}} onClose={() => {}} />);
  await screen.findByRole("button", { name: "Insert avatar" });
  expect(api.readPC).toHaveBeenCalledWith({ kind: "campaign", id: "run" }, "mara");
  expect(api.readCharacter).not.toHaveBeenCalled();
  // no Add and no Remove: only the campaign's own library is managed from here
  expect(screen.queryByLabelText(/add an image/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
});

test("an actor with no art says so instead of offering an empty grid", async () => {
  (api.readPC as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", tags: [], default_version: "default" },
    versions: [{ id: "default", name: "Now", persona: {}, images: [] }],
  });
  render(<PostImagePicker cid="run"
                          target={{ kind: "pcs", id: "mara", version: "default", name: "Mara" }}
                          onInsert={() => {}} onClose={() => {}} />);
  expect(await screen.findByText(/nothing stored for Mara yet/i)).toBeTruthy();
});

test("a failed read shows the reason rather than an empty picker", async () => {
  (api.readCharacter as any).mockRejectedValue({ detail: "character not found" });
  render(<PostImagePicker cid="run"
                          target={{ kind: "characters", id: "ghost", version: "default",
                                    name: "Ghost" }}
                          onInsert={() => {}} onClose={() => {}} />);
  expect(await screen.findByText("character not found")).toBeTruthy();
});

test("Cancel closes without inserting anything", async () => {
  const onClose = vi.fn();
  const onInsert = vi.fn();
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={onInsert} onClose={onClose} />);
  await screen.findByRole("button", { name: "Insert coastline" });
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(onClose).toHaveBeenCalled();
  expect(onInsert).not.toHaveBeenCalled();
});


test("the alt text is the description when there is one, and the name when there is not", () => {
  // The same choice `context/art.resolve_handles` makes on the model's side: a
  // plain-text export, and a model later sent the transcript as text, get the
  // alt text and nothing else.
  expect(insertion("coastline", "/u", "A hand-drawn map of the coast."))
    .toBe("![A hand-drawn map of the coast.](/u)");
  expect(insertion("coastline", "/u")).toBe("![coastline](/u)");
  expect(insertion("coastline", "/u", "   ")).toBe("![coastline](/u)");
});

test("a description carrying link punctuation cannot break the markdown", () => {
  // A NAME can hold neither `]` nor `)` -- the store refuses both -- but a
  // description is free prose and can hold either.
  const out = insertion("coastline", "/u", "A map [annotated] by the\nharbourmaster.");
  expect(out).toBe("![A map (annotated) by the harbourmaster.](/u)");
});

test("a name a markdown link cannot carry bare is percent-encoded", () => {
  // Only the campaign LIBRARY refuses link punctuation in a name
  // (`campaign_images.addressable`); a character or entity image is named under
  // `assets.storable` alone, which accepts `art(1)`, `my art` and `a#b`.
  // The encoding matches `context.art.url_for` exactly, so a picture inserted
  // by hand and the same one inserted by the narrator are byte-identical -- and
  // `export._resolve_image` reads both back.
  expect(insertion("art(1)", "/api/campaigns/c/locations/harbour/images/art(1)"))
    .toBe("![art(1)](/api/campaigns/c/locations/harbour/images/art%281%29)");
  expect(insertion("my art", "/img/my art")).toBe("![my art](/img/my%20art)");
  expect(insertion("a#b", "/img/a#b")).toBe("![a#b](/img/a%23b)");
  // ...and an ordinary name is left exactly as it was
  expect(insertion("coastline", "/api/campaigns/c/images/coastline"))
    .toBe("![coastline](/api/campaigns/c/images/coastline)");
});


test("the library shows each image's description and saves an edit", async () => {
  (api.listCampaignImages as any).mockResolvedValue({
    images: [
      { name: "coastline", ext: "png", v: "a1",
        description: "A hand-drawn map of the coast.", described: true },
      { name: "the-inn", ext: "jpg", v: "b2", description: "", described: false },
    ],
    hidden: [],
  });
  (api.setCampaignImageDescription as any).mockResolvedValue({ ok: true });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);
  expect(await screen.findByRole("button", { name: /Description of coastline/ }))
    .toHaveTextContent("A hand-drawn map of the coast.");
  expect(screen.getByRole("button", { name: /Description of the-inn/ }))
    .toHaveTextContent("Describe…");

  fireEvent.click(screen.getByRole("button", { name: /Description of the-inn/ }));
  const box = await screen.findByRole("textbox", { name: /Description of the-inn/ });
  fireEvent.change(box, { target: { value: "The common room at night." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setCampaignImageDescription)
    .toHaveBeenCalledWith("run", "the-inn", "The common room at night."));
});

test("inserting a described library image uses the description as alt text", async () => {
  (api.listCampaignImages as any).mockResolvedValue({
    images: [
      { name: "coastline", ext: "png", v: "a1",
        description: "A hand-drawn map of the coast.", described: true },
    ],
    hidden: [],
  });
  const onInsert = vi.fn();
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={onInsert} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Insert coastline" }));
  expect(onInsert).toHaveBeenCalledWith(
    "![A hand-drawn map of the coast.](/api/campaigns/run/images/coastline)");
});


test("an inherited image is offered under the world, and inserted with a campaign URL", async () => {
  // The URL a post carries is campaign-scoped even for a picture the campaign
  // only reads through to (`store/context/art.py:url_for`) -- a world URL is
  // the one shape that does not follow a campaign, which is why the picker
  // refuses greeting art outright.
  (api.listCampaignImages as any).mockResolvedValue({
    images: [
      { name: "handout", ext: "png", v: "a1" },
      { name: "coastline", ext: "png", v: "b2", inherited: true },
    ],
    hidden: [],
  });
  const onInsert = vi.fn();
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={onInsert} onClose={() => {}} />);

  expect(await screen.findByText("From this world")).toBeTruthy();
  expect(screen.getByText("Campaign images")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Insert coastline" }));
  expect(onInsert.mock.calls[0][0]).toContain("/api/campaigns/run/images/coastline");
});

test("removing an inherited image is called hiding, and says the world keeps it", async () => {
  (api.listCampaignImages as any).mockResolvedValue({
    images: [{ name: "coastline", ext: "png", v: "b2", inherited: true }],
    hidden: [],
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);

  fireEvent.click(await screen.findByRole("button", { name: "Hide coastline" }));
  expect(confirm.mock.calls[0][0]).toContain("stays in the world");
  expect(api.deleteCampaignImage).toHaveBeenCalledWith("run", "coastline");
  confirm.mockRestore();
});

test("an inherited image is not describable here", async () => {
  // Describing it belongs to the world, where doing it once serves every
  // campaign -- and the route refuses it here, so the field would always fail.
  (api.listCampaignImages as any).mockResolvedValue({
    images: [
      { name: "handout", ext: "png", v: "a1" },
      { name: "coastline", ext: "png", v: "b2", inherited: true },
    ],
    hidden: [],
  });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);

  await screen.findByText("From this world");
  expect(screen.queryByLabelText("Description of handout")).toBeTruthy();
  expect(screen.queryByLabelText("Description of coastline")).toBeNull();
});

test("a hidden image is listed with a way back", async () => {
  // Hidden pictures are in no group by construction, so this row is the only
  // route back -- and it is what lets the world-side sweep be best-effort.
  (api.listCampaignImages as any).mockResolvedValue({
    images: [], hidden: ["coastline"],
  });
  (api.restoreCampaignImage as any).mockResolvedValue({ ok: true });
  render(<PostImagePicker cid="run" target={{ kind: "campaign", name: "Grimoire" }}
                          onInsert={() => {}} onClose={() => {}} />);

  expect(await screen.findByText("Hidden from this campaign")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Show coastline/ }));
  expect(api.restoreCampaignImage).toHaveBeenCalledWith("run", "coastline");
});
