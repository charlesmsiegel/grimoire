import { DETAIL, lastLocation, Spy } from "../testkit/characterPage";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, type CharacterDetail } from "../api/client";
import { CharacterGrid } from "../components/CharacterGrid";
import { fixture } from "../testkit/fixture";
import CharacterPage from "./CharacterPage";

const WORLD = { kind: "world", id: "realm" } as const;
const warning = "Roster refresh failed — Error: listing unavailable";
const importedDetail = fixture<CharacterDetail>({
  ...DETAIL, meta: { ...DETAIL.meta, id: "winifred", name: "Winifred" },
  versions: DETAIL.versions.map((version) => ({
    ...version, card: { ...version.card, data: { ...version.card.data, name: "Winifred" } },
  })),
});

beforeEach(() => {
  vi.mocked(api.rememberedCharacters).mockReturnValue(undefined);
  vi.mocked(api.countUndescribedImages).mockResolvedValue(0);
  vi.mocked(api.readCharacter).mockResolvedValue(importedDetail);
  vi.mocked(api.importCharacter).mockResolvedValue({ character: "winifred", version: "veiled" });
});

test.each([true, false])("a committed file opens its actual record with refresh warning=%s", async (refreshFails) => {
  render(<MemoryRouter initialEntries={["/worlds/realm"]}>
    <Spy />
    <Routes>
      <Route path="/worlds/:wid" element={<CharacterGrid scope={WORLD} wid="realm" />} />
      <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
    </Routes>
  </MemoryRouter>);
  await screen.findByRole("link", { name: /Mara/ });
  if (refreshFails) vi.mocked(api.listCharacters).mockRejectedValueOnce(new Error("listing unavailable"));
  const file = new File(["{}"], "card.json");
  fireEvent.change(screen.getByLabelText("Import character card"), { target: { files: [file] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  await screen.findByRole("heading", { name: "Winifred", level: 1 });
  expect(lastLocation).toBe("/worlds/realm/characters/winifred?v=veiled");
  expect(screen.queryByLabelText("Import character card")).toBeNull();
  if (refreshFails) expect(screen.getByText(warning)).toBeVisible();
  else expect(screen.queryByText(/Roster refresh failed/)).toBeNull();
  expect(api.importCharacter).toHaveBeenCalledTimes(1);
  expect(api.importCharacter).toHaveBeenCalledWith("realm", file, "json", undefined, undefined);
  expect(api.localizeImages).toHaveBeenCalledTimes(1);
  expect(api.localizeImages).toHaveBeenCalledWith("realm", "winifred", "veiled", expect.any(Function));
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith(WORLD, "winifred"));
});

test.each([
  { scope: { kind: "world", id: "saltmarch" }, character: "winifred", version: "veiled" },
  { scope: { kind: "campaign", id: "realm" }, character: "winifred", version: "veiled" },
  { scope: WORLD, character: "mara", version: "veiled" },
  { scope: WORLD, character: "winifred", version: "default" },
])("a refresh notice for another destination stays out of the visible record: %j", async (destination) => {
  render(<MemoryRouter initialEntries={[{
    pathname: "/worlds/realm/characters/winifred", search: "?v=veiled",
    state: { characterImportNotice: { ...destination, warning } },
  }]}>
    <Routes>
      <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
    </Routes>
  </MemoryRouter>);
  await screen.findByRole("heading", { name: "Winifred", level: 1 });
  expect(screen.queryByText(warning)).toBeNull();
});
