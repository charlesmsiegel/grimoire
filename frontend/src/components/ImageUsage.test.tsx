import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import { ImageUsage } from "./ImageUsage";
import type { ImageUsageReport } from "../api/types";

vi.mock("../api/client", () => ({ api: { getImageUsage: vi.fn() } }));
import { api } from "../api/client";

let lastPath = "";
function PathSpy() {
  lastPath = useLocation().pathname;
  return null;
}

const empty: ImageUsageReport = {
  characters: [], pcs: [], entities: [], greetings: [], world_images: [],
  campaign_images: [], covers: [], collections: [],
};
const report = (part: Partial<ImageUsageReport>): ImageUsageReport => ({ ...empty, ...part });

function mount(imageId = "img-1", navigable?: boolean) {
  return render(
    <MemoryRouter initialEntries={["/start"]}>
      <PathSpy />
      <ImageUsage imageId={imageId} navigable={navigable} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  lastPath = "";
});

test("stays collapsed and fetches nothing until opened", async () => {
  (api.getImageUsage as any).mockResolvedValue(empty);
  mount();
  expect(screen.getByRole("button", { name: "Used in…" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Used in" })).toBeNull();
  expect(api.getImageUsage).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  expect(await screen.findByRole("heading", { name: "Used in" })).toBeInTheDocument();
  expect(api.getImageUsage).toHaveBeenCalledTimes(1);
  expect(api.getImageUsage).toHaveBeenCalledWith("img-1");
});

test("lists every bucket's entries as chips after opening", async () => {
  (api.getImageUsage as any).mockResolvedValue(report({
    characters: [{ scope: "world:realm", id: "seraphine", vid: "default", name: "avatar" }],
    pcs: [{ scope: "campaign:saltmarch", id: "mara", vid: "default", name: "avatar" }],
    entities: [{ scope: "world:realm", kind: "locations", id: "harbour", vid: "", name: "gallery_1" }],
    greetings: [{ scope: "world:realm", id: "arrival", name: "avatar" }],
    world_images: [{ wid: "realm", name: "map" }],
    campaign_images: [{ cid: "saltmarch", name: "banner" }],
    covers: [{ scope: "world:realm" }],
    collections: [{ wid: "realm", collection: "Maps" }],
  }));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  for (const label of ["seraphine · avatar", "mara · avatar", "harbour · gallery_1",
                       "arrival · avatar", "map", "banner", "Maps"]) {
    expect(await screen.findByText(label)).toHaveClass("chip");
  }
  expect(screen.getByText("World cover")).toHaveClass("chip");
});

test("two placements of one picture in one record version are two chips", async () => {
  // `avatar` and `gallery_1` of one version are distinct placements: a key that
  // left the placement name out would collide and React would drop one chip.
  const placed = { scope: "world:realm", id: "seraphine", vid: "default" };
  (api.getImageUsage as any).mockResolvedValue(report({
    characters: [{ ...placed, name: "avatar" }, { ...placed, name: "gallery_1" }],
    pcs: [{ ...placed, scope: "campaign:saltmarch", id: "mara", name: "avatar" },
          { ...placed, scope: "campaign:saltmarch", id: "mara", name: "gallery_1" }],
    entities: [{ scope: "world:realm", kind: "locations", id: "harbour", vid: "", name: "avatar" },
               { scope: "world:realm", kind: "locations", id: "harbour", vid: "", name: "gallery_1" }],
    greetings: [{ scope: "world:realm", id: "arrival", name: "avatar" },
                { scope: "world:realm", id: "arrival", name: "gallery_1" }],
  }));
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  for (const id of ["seraphine", "mara", "harbour", "arrival"]) {
    expect(await screen.findByText(`${id} · avatar`)).toHaveClass("chip");
    expect(screen.getByText(`${id} · gallery_1`)).toHaveClass("chip");
  }
  // React reports a duplicate key on the console, and still renders one chip.
  expect(spy).not.toHaveBeenCalled();
  spy.mockRestore();
});

test("an image placed nowhere says so", async () => {
  (api.getImageUsage as any).mockResolvedValue(empty);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  expect(await screen.findByText("Not used anywhere else.")).toHaveClass("field-hint");
});

// One case per bucket that has a page: the path is the route App.tsx declares.
const cases: [string, Partial<ImageUsageReport>, string, string][] = [
  ["characters (world)", { characters: [{ scope: "world:realm", id: "seraphine", vid: "v", name: "avatar" }] },
   "seraphine · avatar", "/worlds/realm/characters/seraphine"],
  ["characters (campaign)", { characters: [{ scope: "campaign:saltmarch", id: "seraphine", vid: "v", name: "avatar" }] },
   "seraphine · avatar", "/campaigns/saltmarch/world/characters/seraphine"],
  ["pcs (world)", { pcs: [{ scope: "world:realm", id: "mara", vid: "v", name: "avatar" }] },
   "mara · avatar", "/worlds/realm/pcs/mara"],
  ["pcs (campaign)", { pcs: [{ scope: "campaign:saltmarch", id: "mara", vid: "v", name: "avatar" }] },
   "mara · avatar", "/campaigns/saltmarch/world/pcs/mara"],
  ["entities (world)", { entities: [{ scope: "world:realm", kind: "locations", id: "harbour", vid: "", name: "gallery_1" }] },
   "harbour · gallery_1", "/worlds/realm/locations/harbour"],
  ["entities (campaign)", { entities: [{ scope: "campaign:saltmarch", kind: "items", id: "lantern", vid: "", name: "gallery_1" }] },
   "lantern · gallery_1", "/campaigns/saltmarch/world/items/lantern"],
  ["greetings (world)", { greetings: [{ scope: "world:realm", id: "arrival", name: "avatar" }] },
   "arrival · avatar", "/worlds/realm/greetings/arrival"],
  ["greetings (campaign)", { greetings: [{ scope: "campaign:saltmarch", id: "arrival", name: "avatar" }] },
   "arrival · avatar", "/campaigns/saltmarch/world/greetings/arrival"],
  ["world_images", { world_images: [{ wid: "realm", name: "map" }] },
   "map", "/worlds/realm/images"],
];
test.each(cases)("each bucket with a page navigates to it: %s", async (_n, part, label, path) => {
  (api.getImageUsage as any).mockResolvedValue(report(part));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  fireEvent.click(await screen.findByRole("button", { name: label }));
  expect(lastPath).toBe(path);
});

test("buckets with no page of their own render as spans", async () => {
  (api.getImageUsage as any).mockResolvedValue(report({
    campaign_images: [{ cid: "saltmarch", name: "banner" }],
    covers: [{ scope: "campaign:saltmarch" }],
    collections: [{ wid: "realm", collection: "Maps" }],
  }));
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  for (const label of ["banner", "Campaign cover", "Maps"]) {
    const chip = await screen.findByText(label);
    expect(chip.tagName).toBe("SPAN");
    expect(chip).toHaveClass("chip", "on");
  }
});

test("non-navigable chips render as spans", async () => {
  (api.getImageUsage as any).mockResolvedValue(report({
    characters: [{ scope: "world:realm", id: "seraphine", vid: "v", name: "avatar" }],
  }));
  mount("img-1", false);
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  const chip = await screen.findByText("seraphine · avatar");
  expect(chip.tagName).toBe("SPAN");
  expect(chip).toHaveClass("chip", "on");
  fireEvent.click(chip);
  expect(lastPath).toBe("/start");
});

test("works outside a router when it is not navigable", async () => {
  (api.getImageUsage as any).mockResolvedValue(report({
    characters: [{ scope: "world:realm", id: "seraphine", vid: "v", name: "avatar" }],
  }));
  render(<ImageUsage imageId="img-1" navigable={false} />);
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  expect(await screen.findByText("seraphine · avatar")).toBeInTheDocument();
});

test("shows an error hint when the request fails", async () => {
  (api.getImageUsage as any).mockRejectedValue({ detail: "usage unavailable" });
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Used in…" }));
  expect(await screen.findByText("usage unavailable")).toHaveClass("field-hint");
});
