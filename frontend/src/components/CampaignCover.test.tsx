import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { CampaignCover } from "./CampaignCover";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    putCampaignCover: vi.fn(),
    deleteCampaignCover: vi.fn(),
    campaignCoverUrl: (cid: string, o?: { v?: string }) =>
      `/api/campaigns/${cid}/cover${o?.v ? `?v=${o.v}` : ""}`,
  },
}));
import { api } from "../api/client";

const file = () => new File(["png"], "cover.png", { type: "image/png" });

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "" }, body: "" });
  (api.putCampaignCover as any).mockResolvedValue({ ext: "png", v: "abc" });
  (api.deleteCampaignCover as any).mockResolvedValue({ ok: true });
});

test("shows the placeholder when there is no cover", async () => {
  render(<CampaignCover cid="run" />);
  expect(await screen.findByText(/no cover/i)).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
});

test("uploading a file stores it and shows it at the new version", async () => {
  render(<CampaignCover cid="run" />);
  const input = await screen.findByLabelText(/cover image/i);
  fireEvent.change(input, { target: { files: [file()] } });
  await waitFor(() => expect(api.putCampaignCover).toHaveBeenCalledWith("run", expect.any(File)));
  const img = await screen.findByRole("img");
  expect(img.getAttribute("src")).toBe("/api/campaigns/run/cover?v=abc");
});

test("shows an existing cover and removes it", async () => {
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  render(<CampaignCover cid="run" />);
  expect((await screen.findByRole("img")).getAttribute("src")).toBe("/api/campaigns/run/cover?v=v1");
  fireEvent.click(screen.getByRole("button", { name: /remove/i }));
  await waitFor(() => expect(api.deleteCampaignCover).toHaveBeenCalledWith("run"));
  expect(await screen.findByText(/no cover/i)).toBeTruthy();
});

test("a rejected upload shows the error and keeps the current cover", async () => {
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  (api.putCampaignCover as any).mockRejectedValue({ detail: "not a readable image" });
  render(<CampaignCover cid="run" />);
  const input = await screen.findByLabelText(/cover image/i);
  fireEvent.change(input, { target: { files: [file()] } });
  expect(await screen.findByText("not a readable image")).toBeTruthy();
  expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src")).toBe("/api/campaigns/run/cover?v=v1");
});

test("a rejected removal shows the error and keeps the current cover", async () => {
  // The backend confirms the unlink before answering, so a failed DELETE
  // means the cover is genuinely still there -- clearing it here would tell
  // the user something false.
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  (api.deleteCampaignCover as any).mockRejectedValue({ detail: "cover could not be removed" });
  render(<CampaignCover cid="run" />);
  await screen.findByRole("img");
  fireEvent.click(screen.getByRole("button", { name: /remove/i }));
  expect(await screen.findByText("cover could not be removed")).toBeTruthy();
  expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src")).toBe("/api/campaigns/run/cover?v=v1");
});

test("uploading resets the file input so the same file can be re-picked", async () => {
  render(<CampaignCover cid="run" />);
  const input = await screen.findByLabelText(/cover image/i) as HTMLInputElement;
  // jsdom never populates a file input's `value` from an assigned FileList
  // the way a real browser fakepath would, so asserting on `input.value`
  // after upload can't tell a reset apart from one that never happened.
  // Spy on the native setter instead, so the assertion actually depends on
  // the component's `input.current.value = ""` line running.
  const setSpy = vi.fn();
  const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!;
  Object.defineProperty(input, "value", {
    configurable: true,
    get: () => desc.get!.call(input),
    set: (v) => { setSpy(v); desc.set!.call(input, v); },
  });
  fireEvent.change(input, { target: { files: [file()] } });
  await waitFor(() => expect(api.putCampaignCover).toHaveBeenCalledWith("run", expect.any(File)));
  await screen.findByRole("img");
  expect(setSpy).toHaveBeenCalledWith("");
});

// ---- a campaign switch mid-request (CampaignView reuses one panel) ----------
// The same bug class as NewSceneChooser's (see commit 63278b37): every await
// here can resolve after the reader has moved to another campaign, and this
// component is not remounted across that move — only its `cid` prop changes.

const byCover = (covers: Record<string, string>) => (cid: string) =>
  Promise.resolve({ meta: { id: cid, name: "N", cover: covers[cid] ?? "" }, body: "" });

test("a slow load for one campaign is never applied to the next", async () => {
  let releaseA: (v: any) => void = () => {};
  (api.getCampaign as any).mockImplementation((cid: string) =>
    cid === "a" ? new Promise((r) => { releaseA = r; }) : byCover({})(cid));
  const { rerender } = render(<CampaignCover cid="a" />);
  await waitFor(() => expect(api.getCampaign).toHaveBeenCalledWith("a"));

  rerender(<CampaignCover cid="b" />);
  expect(await screen.findByText(/no cover/i)).toBeTruthy();

  await act(async () => { releaseA({ meta: { id: "a", name: "One", cover: "vA" }, body: "" }); });
  // Unguarded, campaign a's version lands on campaign b's panel and the panel
  // renders /api/campaigns/b/cover?v=vA — an image that does not exist.
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText(/no cover/i)).toBeTruthy();
});

test("an upload in flight for one campaign leaves the next one usable", async () => {
  let releasePut: (v: any) => void = () => {};
  (api.putCampaignCover as any).mockReturnValue(new Promise((r) => { releasePut = r; }));
  (api.getCampaign as any).mockImplementation(byCover({ b: "vB" }));
  const { rerender } = render(<CampaignCover cid="a" />);
  fireEvent.change(await screen.findByLabelText(/cover image/i), { target: { files: [file()] } });
  await waitFor(() => expect(api.putCampaignCover).toHaveBeenCalledWith("a", expect.any(File)));

  rerender(<CampaignCover cid="b" />);
  expect((await screen.findByRole("img")).getAttribute("src")).toBe("/api/campaigns/b/cover?v=vB");
  // `busy` must be reset by the cid change itself: the abandoned upload's own
  // setBusy(false) is suppressed by the live-cid guard, so nothing else ever
  // re-enables these — b's panel would stay disabled forever.
  expect((screen.getByLabelText(/replace cover image/i) as HTMLInputElement).disabled).toBe(false);
  expect((screen.getByRole("button", { name: /remove/i }) as HTMLButtonElement).disabled).toBe(false);

  await act(async () => { releasePut({ ext: "png", v: "vA" }); });
  expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src"))
    .toBe("/api/campaigns/b/cover?v=vB");
});

test("a removal in flight for one campaign does not clear the next one's cover", async () => {
  let releaseDel: (v: any) => void = () => {};
  (api.deleteCampaignCover as any).mockReturnValue(new Promise((r) => { releaseDel = r; }));
  (api.getCampaign as any).mockImplementation(byCover({ a: "vA", b: "vB" }));
  const { rerender } = render(<CampaignCover cid="a" />);
  await screen.findByRole("img");
  fireEvent.click(screen.getByRole("button", { name: /remove/i }));
  await waitFor(() => expect(api.deleteCampaignCover).toHaveBeenCalledWith("a"));

  rerender(<CampaignCover cid="b" />);
  await waitFor(() => expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src"))
    .toBe("/api/campaigns/b/cover?v=vB"));

  await act(async () => { releaseDel({ ok: true }); });
  expect((screen.getByRole("img") as HTMLImageElement).getAttribute("src"))
    .toBe("/api/campaigns/b/cover?v=vB");
});

test("a cover that fails to load falls back to the placeholder, like the list does", async () => {
  // Removed in another tab: the panel would otherwise show the browser's
  // broken-image glyph next to a live "Remove cover" button.
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Saltmarch Nights", cover: "v1" }, body: "" });
  render(<CampaignCover cid="run" />);
  fireEvent.error(await screen.findByRole("img"));

  expect(await screen.findByText(/no cover/i)).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();

  // Keyed by version, so a replacement uploaded afterwards is not inherited as
  // broken — the same reason CampaignsView keys its `broken` map that way.
  fireEvent.change(screen.getByLabelText(/cover image/i), { target: { files: [file()] } });
  expect((await screen.findByRole("img")).getAttribute("src")).toBe("/api/campaigns/run/cover?v=abc");
});
