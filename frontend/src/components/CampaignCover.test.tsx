import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
