import { useEffect, useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ArtNavigationProvider, useArtNavigation } from "./art/ArtNavigationProvider";
import { ImageViewer } from "./art/ImageViewer";
import { useImageViewer } from "./art/useImageViewer";
import { WorldArtPanel } from "./WorldArtPanel";
import { api } from "../api/client";
import { artChanged } from "../appEvents";
vi.mock("../api/client", () => ({ api: {
  listWorldLibrary: vi.fn(), getWorld: vi.fn(), worldCoverUrl: vi.fn(),
  putWorldCover: vi.fn(), deleteWorldCover: vi.fn(), putWorldImage: vi.fn(), deleteWorldImage: vi.fn(),
  setWorldImageDescription: vi.fn(), draftWorldImageDescription: vi.fn(),
  worldImageUrl: (wid: string, name: string, opts: { w?: number; v?: string }) =>
    "/api/worlds/" + wid + "/images/" + name + "?v=" + opts.v + (opts.w ? "&w=" + opts.w : ""),
} }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getWorld).mockResolvedValue({ meta: { cover: "" } } as never);
  vi.mocked(api.listWorldLibrary).mockResolvedValue([{ name: "Saltmarch", ext: "png", v: "bytes", described: true, description: "A wide map." }]);
  vi.mocked(api.setWorldImageDescription).mockResolvedValue({ ok: true });
  vi.mocked(api.putWorldImage).mockResolvedValue({ name: "Saltmarch-2", ext: "png", v: "new" });
});
test("world library uses contained previews and loads only the selected original on request", async () => {
  const view = render(<WorldArtPanel wid="Realm" />);
  const tile = await screen.findByRole("button", { name: /^View World library/ });
  expect(tile.querySelector("img")).toHaveAttribute("loading", "lazy");
  expect(tile.querySelector("img")).toHaveAttribute("src", "/api/worlds/Realm/images/Saltmarch?v=bytes&w=160");
  expect(view.container.querySelector('img[src="/api/worlds/Realm/images/Saltmarch?v=bytes"]')).toBeNull();
  fireEvent.click(tile);
  const dialog = screen.getByRole("dialog", { name: "Image viewer" });
  expect(within(dialog).getByAltText("A wide map.")).toHaveAttribute("src", "/api/worlds/Realm/images/Saltmarch?v=bytes");
  expect(screen.queryByRole("button", { name: /Make primary|Crop preview|Open record/ })).not.toBeInTheDocument();
  expect(dialog.textContent).not.toContain("default");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(tile).toHaveFocus();
});
test("existing caption editing and draft contracts are retained", async () => {
  render(<WorldArtPanel wid="Realm" />);
  fireEvent.click(await screen.findByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWorldImageDescription).toHaveBeenCalledWith("Realm", "Saltmarch", ""));
  expect(api.draftWorldImageDescription).not.toHaveBeenCalled();
});
test("upload still allocates a free library name rather than an actor slot", async () => {
  render(<WorldArtPanel wid="Realm" />);
  await screen.findByRole("button", { name: /^View World library/ });
  const file = new File(["png"], "Saltmarch.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Add an image"), { target: { files: [file] } });
  await waitFor(() => expect(api.putWorldImage).toHaveBeenCalledWith("Realm", "Saltmarch-2", file));
});
test("failed listing is visible and does not claim the library is empty", async () => {
  vi.mocked(api.listWorldLibrary).mockRejectedValue(new Error("Art unreadable"));
  render(<WorldArtPanel wid="Realm" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Art unreadable");
  expect(screen.queryByText("No world images yet.")).not.toBeInTheDocument();
});

test("an earlier same-world listing cannot overwrite a newer post-upload refresh", async () => {
  let finishOld!: (rows: Awaited<ReturnType<typeof api.listWorldLibrary>>) => void;
  vi.mocked(api.listWorldLibrary).mockResolvedValueOnce([{ name: "Saltmarch", ext: "png", v: "bytes" }])
    .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce([{ name: "Saltmarch-2", ext: "png", v: "new" }]);
  render(<WorldArtPanel wid="Realm" />);
  await screen.findByRole("button", { name: /^View World library/ });
  artChanged();
  await waitFor(() => expect(api.listWorldLibrary).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByLabelText("Add an image"), { target: { files: [new File(["png"], "Mara.png")] } });
  await screen.findByRole("button", { name: /^View World library.*Saltmarch-2/ });
  await act(async () => finishOld([{ name: "Saltmarch", ext: "png", v: "old" }]));
  expect(screen.getByRole("button", { name: /^View World library.*Saltmarch-2/ })).toBeVisible();
});

test("a failed library refresh preserves its open viewer and known placements", async () => {
  render(<WorldArtPanel wid="Realm" />);
  fireEvent.click(await screen.findByRole("button", { name: /^View World library/ }));
  vi.mocked(api.listWorldLibrary).mockRejectedValue(new Error("Art unreadable"));
  act(artChanged);
  expect(await screen.findByRole("alert", { hidden: true })).toHaveTextContent("Art unreadable");
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.queryByText("This image is no longer available.")).not.toBeInTheDocument();
});

test("removed library selection restores focus to the surviving shelf heading", async () => {
  render(<WorldArtPanel wid="Realm" />);
  fireEvent.click(await screen.findByRole("button", { name: /^View World library/ }));
  vi.mocked(api.listWorldLibrary).mockResolvedValue([]);
  act(artChanged);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("heading", { name: "World images" })).toHaveFocus();
});

test.each([false, true])("world library viewer asks before discarding a dirty caption (discard: %s)", async discard => {
  render(<WorldArtPanel wid="Realm" />);
  const tile = await screen.findByRole("button", { name: /^View World library/ });
  fireEvent.click(screen.getByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(discard);
  try {
    fireEvent.click(tile);
    expect(confirm).toHaveBeenCalledOnce();
    if (discard) {
      expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    } else {
      expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveValue("Unsaved");
    }
    expect(api.setWorldImageDescription).not.toHaveBeenCalled();
  } finally { confirm.mockRestore(); }
});

test.each([false, true])("another shelf viewer uses the library draft decision (discard: %s)", async discard => {
  function OtherShelf() {
    const viewer = useImageViewer("Realm/other");
    return <><button onClick={event => viewer.open([{ key: "Mara", label: "Mara", alt: "Mara",
      originalUrl: "/api/art", thumbnailUrl: "/api/art", version: "1" }], "Mara", event.currentTarget)}>View Mara</button>
      {viewer.viewerProps && <ImageViewer {...viewer.viewerProps} />}</>;
  }
  render(<ArtNavigationProvider><WorldArtPanel wid="Realm" /><OtherShelf /></ArtNavigationProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(discard);
  try {
    fireEvent.click(screen.getByRole("button", { name: "View Mara" }));
    expect(confirm).toHaveBeenCalledOnce();
    if (discard) {
      expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    } else {
      expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveValue("Unsaved");
    }
  } finally { confirm.mockRestore(); }
});

test("a later guard refusal keeps the library draft after its own discard confirmation", async () => {
  function OtherShelf() {
    const viewer = useImageViewer("Realm/other");
    return <><button onClick={event => viewer.open([{ key: "Mara", label: "Mara", alt: "Mara",
      originalUrl: "/api/art", thumbnailUrl: "/api/art", version: "1" }], "Mara", event.currentTarget)}>View Mara</button>
      {viewer.viewerProps && <ImageViewer {...viewer.viewerProps} />}</>;
  }
  function Veto() {
    const navigation = useArtNavigation();
    useEffect(() => navigation.registerDraftGuard(() => false), [navigation]);
    return null;
  }
  render(<ArtNavigationProvider><WorldArtPanel wid="Realm" /><OtherShelf /><Veto /></ArtNavigationProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    fireEvent.click(screen.getByRole("button", { name: "View Mara" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("Unsaved");
  } finally { confirm.mockRestore(); }
});

test("a pending library caption save blocks viewer entry without another discard prompt", async () => {
  let finish!: (response: Awaited<ReturnType<typeof api.setWorldImageDescription>>) => void;
  vi.mocked(api.setWorldImageDescription).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<WorldArtPanel wid="Realm" />);
  const tile = await screen.findByRole("button", { name: /^View World library/ });
  fireEvent.click(screen.getByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A map" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /Saving/ })).toBeDisabled());
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    fireEvent.click(tile);
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Image viewer" })).not.toBeInTheDocument();
    await act(async () => finish({ ok: true }));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    fireEvent.click(tile);
    expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  } finally { confirm.mockRestore(); }
});


test.each([false, true])("pending library caption permits a context decision (%s)", async allow => {
  let finish!: (response: Awaited<ReturnType<typeof api.setWorldImageDescription>>) => void;
  vi.mocked(api.setWorldImageDescription).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(allow);
  function Surface() {
    const navigation = useArtNavigation(), [next, setNext] = useState(false);
    return <><button onClick={() => navigation.requestArtTransition(() => setNext(true))}>Next context</button>
      {next ? <p>Next context selected</p> : <WorldArtPanel wid="Realm" />}</>;
  }
  render(<ArtNavigationProvider><Surface /></ArtNavigationProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "Description of Saltmarch" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A map" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /Saving/ })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Next context" }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(!!screen.queryByText("Next context selected")).toBe(allow);
  await act(async () => finish({ ok: true }));
  expect(api.setWorldImageDescription).toHaveBeenCalledOnce();
});
