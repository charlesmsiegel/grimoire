import { MemoryRouter } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import { ArtTab } from "./ArtTab";
import { artAdapter, artImage, artRead, artTarget } from "../../testkit/recordArt";
import * as adapters from "../art/recordArtAdapter";
import { useRecordArt } from "../art/useRecordArt";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { ...actual.api, getImageUsage: vi.fn() } };
});
test("the Character wrapper uses the common panel and preserves placement usage", async () => {
  const target = artTarget();
  const adapter = artAdapter(target, artRead(target, [artImage(target), artImage(target, "gallery_1", { image_id: null })]));
  vi.spyOn(adapters, "createRecordArtAdapter").mockReturnValue(adapter);
  function Harness() {
    const controller = useRecordArt(target, { onSaved: async () => {} });
    return <ArtTab controller={controller} related={{ scope: target.scope, wid: "Realm", record: "Seraphine", version: "main",
      appearances: [], baseVersions: [], manage: controller.manage, busy: !!controller.busy, fallbackFocus: null,
      localizeProg: null, localizeMsg: null, onLocalize: vi.fn(), onCopy: vi.fn(), greetingHref: (gid) => "/g/" + gid }} />;
  }
  render(<MemoryRouter><Harness /></MemoryRouter>);
  const primary = await screen.findByRole("button", { name: "View Primary image" });
  expect(within(primary.closest("article")!).getByRole("button", { name: "Used in…" })).toBeInTheDocument();
  expect(within(screen.getByRole("button", { name: "View Gallery image 1" }).closest("article")!)
    .queryByRole("button", { name: "Used in…" })).not.toBeInTheDocument();
  expect(adapter.read).toHaveBeenCalledOnce();
  vi.restoreAllMocks();
});
