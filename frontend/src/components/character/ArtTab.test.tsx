import { MemoryRouter } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import { ArtTab } from "./ArtTab";

vi.mock("../../api/client", () => ({
  api: {
    getImageUsage: vi.fn(),
    actorImageUrl: () => "/img",
    putImage: vi.fn(),
    promoteImage: vi.fn(),
    deleteImage: vi.fn(),
    copyGreetingImage: vi.fn(),
    setCharacterImageDescription: vi.fn(),
    draftCharacterImageDescription: vi.fn(),
  },
}));

function mount() {
  return render(
    <MemoryRouter>
      <ArtTab scope={{ kind: "world", id: "realm" }} wid="realm" cid="seraphine" vid="default"
              hasAvatar galleryImages={["gallery_1"]} imageTokens={{}}
              imageIds={{ avatar: "id-avatar" }}
              descriptions={{ avatar: "A portrait", gallery_1: "A sketch" }}
              appearances={[]} worldScope
              localizeProg={null} localizeMsg={null} onLocalize={() => {}}
              onRefresh={async () => {}} onError={() => {}}
              greetingHref={(g) => `/g/${g}`} />
    </MemoryRouter>,
  );
}

test("an image with an image_id shows the Used in control; a legacy image does not", () => {
  mount();
  // The avatar is placement-backed; gallery_1 is a legacy file with no id.
  const tile = (name: string) => within(screen.getByAltText(name).closest("figure")!);
  expect(tile("avatar").getByRole("button", { name: "Used in…" })).toBeInTheDocument();
  expect(tile("gallery_1").getByRole("button", { name: "Description of gallery_1" }))
    .toBeInTheDocument();
  expect(tile("gallery_1").queryByRole("button", { name: "Used in…" })).toBeNull();
});
