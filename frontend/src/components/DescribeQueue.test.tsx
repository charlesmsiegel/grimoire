import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DescribeQueue } from "./DescribeQueue";

vi.mock("../api/client", () => ({
  api: {
    setCharacterImageDescription: vi.fn(),
    setCampaignImageDescription: vi.fn(),
    setPCImageDescription: vi.fn(),
    setEntityImageDescription: vi.fn(),
    draftCharacterImageDescription: vi.fn(),
    draftPCImageDescription: vi.fn(),
    draftEntityImageDescription: vi.fn(),
    draftCampaignImageDescription: vi.fn(),
  },
}));

import { api } from "../api/client";

const QUEUE = [
  { kind: "characters", id: "seraphine", vid: "default", name: "gallery_1",
    record_name: "Seraphine", url: "/img/1" },
  { kind: "locations", id: "harbour", vid: "default", name: "gallery_1",
    record_name: "Saltmarch Harbour", url: "/img/2" },
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.setCharacterImageDescription as any).mockResolvedValue({ ok: true });
  (api.setEntityImageDescription as any).mockResolvedValue({ ok: true });
});

test("steps through the queue, saving each description against its own surface", async () => {
  const onSaved = vi.fn();
  render(<DescribeQueue scope={{ kind: "world", id: "w" }} wid="w" queue={QUEUE} onClose={vi.fn()} onSaved={onSaved} />);

  expect(screen.getByText(/Describing 1 \/ 2 — Seraphine · gallery_1/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }),
                   { target: { value: "Half-plate, rain-soaked." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(api.setCharacterImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", "gallery_1",
    "Half-plate, rain-soaked."));
  expect(onSaved).toHaveBeenCalled();

  // ...and on to the next, whose surface is an entity kind rather than an actor
  expect(await screen.findByText(/Describing 2 \/ 2 — Saltmarch Harbour/)).toBeInTheDocument();
  // the textarea is cleared, not carried over from the previous image
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue("");
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }),
                   { target: { value: "Fishing boats under fog." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(api.setEntityImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "locations", "harbour", "gallery_1",
    "Fishing boats under fog."));
  expect(await screen.findByText(/Every image is described/)).toBeInTheDocument();
});

test("'No description' persists an empty string and retires the image", async () => {
  render(<DescribeQueue scope={{ kind: "world", id: "w" }} wid="w" queue={QUEUE} onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "No description" }));
  await waitFor(() => expect(api.setCharacterImageDescription).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", "gallery_1", ""));
});

test("Skip writes nothing, so the image comes back next time", async () => {
  render(<DescribeQueue scope={{ kind: "world", id: "w" }} wid="w" queue={QUEUE} onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));

  expect(await screen.findByText(/Describing 2 \/ 2/)).toBeInTheDocument();
  expect(api.setCharacterImageDescription).not.toHaveBeenCalled();
  expect(api.setEntityImageDescription).not.toHaveBeenCalled();
});

test("a draft is asked for on whichever surface the image belongs to", async () => {
  (api.draftCharacterImageDescription as any).mockResolvedValue({ description: "A drafted line." });
  (api.draftEntityImageDescription as any).mockResolvedValue({ description: "Another line." });
  render(<DescribeQueue scope={{ kind: "world", id: "w" }} wid="w" queue={QUEUE} onClose={vi.fn()} onSaved={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: /Describe it for me/ }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue("A drafted line."));
  // ...and nothing was saved: a draft is a starting point, not a write.
  expect(api.setCharacterImageDescription).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByText(/Describing 2 \/ 2/);
  fireEvent.click(screen.getByRole("button", { name: /Describe it for me/ }));
  await waitFor(() => expect(api.draftEntityImageDescription).toHaveBeenCalledWith(
    "w", "locations", "harbour", "gallery_1"));
});

test("a campaign queue describes the library by name and drafts campaign-side", async () => {
  (api.setCampaignImageDescription as any).mockResolvedValue({ ok: true });
  (api.draftCampaignImageDescription as any).mockResolvedValue({ description: "A map." });
  const lib = [{ kind: "campaign", id: "", vid: "", name: "coastline",
                 record_name: "Campaign library", url: "/img/lib" }];
  render(<DescribeQueue scope={{ kind: "campaign", id: "run" }} wid="w" queue={lib}
                        onClose={vi.fn()} onSaved={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: /Describe it for me/ }));
  await waitFor(() => expect(api.draftCampaignImageDescription).toHaveBeenCalledWith(
    "run", "coastline"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  // The library hangs off no record, so it addresses by name alone.
  await waitFor(() => expect(api.setCampaignImageDescription).toHaveBeenCalledWith(
    "run", "coastline", "A map."));
});

test("a campaign queue offers no draft for art whose bytes live in the world", async () => {
  render(<DescribeQueue scope={{ kind: "campaign", id: "run" }} wid="w" queue={QUEUE}
                        onClose={vi.fn()} onSaved={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /Describe it for me/ })).toBeNull();
});

test("a failed save keeps the image in the queue and says why", async () => {
  (api.setCharacterImageDescription as any).mockRejectedValue({ detail: "image not found" });
  render(<DescribeQueue scope={{ kind: "world", id: "w" }} wid="w" queue={QUEUE} onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("image not found")).toBeInTheDocument();
  expect(screen.getByText(/Describing 1 \/ 2/)).toBeInTheDocument();
});
