import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../../api/client";
import { artAdapter, artImage, artTarget, galleryRow } from "../../testkit/recordArt";
import { ExistingArtPicker } from "./ExistingArtPicker";
afterEach(() => vi.restoreAllMocks());
test("visible source filtering excludes greetings/originals and captures primary intent", async () => {
  const adapter = artAdapter(artTarget("items", "campaign")), source = artImage(adapter.target, "gallery_1");
  vi.spyOn(api, "listCampaignGallery").mockResolvedValue([galleryRow(source),
    { ...galleryRow(source), kind: "greetings", record_name: "Mara" },
    { ...galleryRow(source), name: "original", provenance: "world-original" }]);
  vi.spyOn(api, "listCampaignImages").mockResolvedValue({ images: [], hidden: ["Saltmarch"] });
  const created = vi.fn();
  render(<ExistingArtPicker adapter={adapter} expectedPrimary={null} onCreated={created} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "Select Saltmarch · gallery_1" }));
  expect(screen.queryByText("Mara")).not.toBeInTheDocument();
  expect(screen.queryByRole("radio", { name: /original/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Make primary" }));
  fireEvent.click(screen.getByRole("button", { name: "Add selected image" }));
  await waitFor(() => expect(adapter.reuse).toHaveBeenCalledWith(source.placement, "primary", null));
  expect(created).toHaveBeenCalled();
});
test("reuse failure keeps selection and never retries by itself", async () => {
  const adapter = artAdapter(), source = artImage(adapter.target, "gallery_1");
  vi.spyOn(api, "listWorldImages").mockResolvedValue([galleryRow(source)]);
  vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
  adapter.reuse.mockRejectedValue(new Error("Image changed"));
  render(<ExistingArtPicker adapter={adapter} expectedPrimary="avatar-1" onCreated={vi.fn()} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("radio", { name: "Select Seraphine / main · gallery_1" }));
  fireEvent.click(screen.getByRole("button", { name: "Add selected image" }));
  await screen.findByText(/Image changed/);
  expect(adapter.reuse).toHaveBeenCalledOnce();
  expect(screen.getByRole("radio")).toBeChecked();
});

test.each(["world", "campaign"] as const)("the %s picker fresh-reads reusable art and offers local original downloads", async scope => {
  const adapter = artAdapter(artTarget("items", scope)), source = artImage(adapter.target, "gallery_1");
  vi.spyOn(api, "listWorldImages").mockResolvedValue([galleryRow(source)]);
  vi.spyOn(api, "listCampaignGallery").mockResolvedValue([galleryRow(source)]);
  const world = vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
  const campaign = vi.spyOn(api, "listCampaignImages").mockResolvedValue({ images: [], hidden: [] });
  render(<ExistingArtPicker adapter={adapter} expectedPrimary={null} onCreated={vi.fn()} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /^View Saltmarch/ }));
  expect(scope === "world" ? world : campaign).toHaveBeenCalledWith("Realm", true);
  expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute("href", "/api/art/gallery_1?v=1&download=1");
});

test.each(["characters", "pcs"] as const)("%s reuse candidates disclose and search their actor version", async kind => {
  const adapter = artAdapter(artTarget("items"));
  const main = { ...artTarget(kind), kind, version: "main" }, other = { ...main, version: "other" };
  const first = artImage(main, "gallery_1"), second = artImage(other, "gallery_1");
  vi.spyOn(api, "listWorldImages").mockResolvedValue([galleryRow(first), galleryRow(second)]);
  vi.spyOn(api, "listWorldLibrary").mockResolvedValue([]);
  render(<ExistingArtPicker adapter={adapter} expectedPrimary={null} onCreated={vi.fn()} onClose={vi.fn()} />);
  expect(await screen.findByRole("radio", { name: new RegExp("Select " + main.record_id + ".*main.*gallery_1") })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: new RegExp("Select " + main.record_id + ".*other.*gallery_1") }));
  fireEvent.change(screen.getByLabelText("Search art"), { target: { value: "other" } });
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: new RegExp("View " + main.record_id + ".*other.*gallery_1") }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toHaveTextContent(main.record_id + " / other");
  fireEvent.click(screen.getByRole("button", { name: "Close image viewer" }));
  fireEvent.click(screen.getByRole("button", { name: "Add selected image" }));
  await waitFor(() => expect(adapter.reuse).toHaveBeenCalledWith(second.placement, "gallery", null));
});
