import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import CharacterPage from "../routes/CharacterPage";
import { artImage, artRead } from "./recordArt";
import type { Casefile, CharacterDetail } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      // `?w=` then `?v=`, as the real builder writes them, so a tile's
      // downscale and the original it links to read differently.
      actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string,
                      o?: { w?: number; v?: string | null }) =>
        `/img/${sc.id}/${k}/${a}/${v}/${n}`
        + (o?.w || o?.v ? "?" + [o.w && `w=${o.w}`, o.v && `v=${o.v}`].filter(Boolean).join("&") : ""),
      exportUrl: (w: string, c: string, v: string, f: string) => `/export/${w}/${c}/${v}/${f}`,
      readRecordArt: vi.fn(), appendRecordArt: vi.fn(), promoteRecordArt: vi.fn(), removeRecordArt: vi.fn(),
      setRecordArtFocus: vi.fn(), setRecordArtDescription: vi.fn(), draftRecordArtDescription: vi.fn(), getImageUsage: vi.fn(),
      getActorMembership: vi.fn(), readCharacter: vi.fn(), listCharacters: vi.fn(), deleteCharacter: vi.fn(),
      rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(), countUndescribedImages: vi.fn(),
      updateVersion: vi.fn(), createVersion: vi.fn(), setDefaultVersion: vi.fn(),
      setCharacterName: vi.fn(), setCharacterBirthdate: vi.fn(), getCalendarMonths: vi.fn(),
      getCalendarConfig: vi.fn(),
      getCampaignCharacterTagline: vi.fn().mockResolvedValue({ tagline: "" }),
      setCampaignCharacterTagline: vi.fn(), generateCampaignCapsule: vi.fn(),
      importCharacter: vi.fn(), localizeImages: vi.fn(),

      setCharacterImageDescription: vi.fn(), draftCharacterImageDescription: vi.fn(),
      lorebookParse: vi.fn(), lorebookImport: vi.fn(), entityKinds: vi.fn(),
      importCharacterFromChub: vi.fn(), setCharacterChubSource: vi.fn(),
      clearCharacterChubSource: vi.fn(), downloadCharacterChubGallery: vi.fn(),
      downloadCharacterChubLorebooks: vi.fn(),
      getCharacterTagline: vi.fn(), setCharacterTagline: vi.fn(), generateCharacterTagline: vi.fn(),
      getCharacterVoiceAnchor: vi.fn(), setCharacterVoiceAnchor: vi.fn(),
      generateCharacterVoiceAnchor: vi.fn(),
      getExpressions: vi.fn(), mutateExpressions: vi.fn(), expressionExportUrl: vi.fn(), nativeActorExportUrl: vi.fn(),
      previewExpressionPack: vi.fn(), publishExpressionPack: vi.fn(),
      listImageAppearances: vi.fn(), copyGreetingImage: vi.fn(), listGreetings: vi.fn(), readGreeting: vi.fn(),
      listAppearances: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
      getCampaign: vi.fn(), getCampaignModule: vi.fn(), getCasefile: vi.fn(),
      listEntities: vi.fn(), getConfig: vi.fn(), readModule: vi.fn(),
      getWorldSheetsIndex: vi.fn(), listModules: vi.fn(),
      libraryStatus: vi.fn(), promoteToLibrary: vi.fn(), pushToLibrary: vi.fn(),
    },
  };
});
import { api } from "../api/client";
import { fixture, type DeepPartial } from "./fixture";

const GREG_MONTHS = [
  { key: "01", name: "January", days: 31 }, { key: "02", name: "February", days: 28 },
  { key: "03", name: "March", days: 31 }, { key: "04", name: "April", days: 30 },
  { key: "05", name: "May", days: 31 }, { key: "06", name: "June", days: 30 },
  { key: "07", name: "July", days: 31 }, { key: "08", name: "August", days: 31 },
  { key: "09", name: "September", days: 30 }, { key: "10", name: "October", days: 31 },
  { key: "11", name: "November", days: 30 }, { key: "12", name: "December", days: 31 },
];

export const card = (over: Record<string, unknown> = {}) => ({
  spec: "chara_card_v3", spec_version: "3.0",
  data: {
    name: "Seraphine", description: "keeper of the tide gate",
    personality: "guarded", scenario: "the priory's debt",
    first_mes: "You are early.", alternate_greetings: ["Rain on the shutters."],
    tags: ["priory", "tide"], extensions: {},
    character_book: { entries: [{ keys: ["pact"], content: "x" }] },
    ...over,
  },
});

export const DETAIL = {
  meta: { id: "seraphine", name: "Seraphine", default_version: "default", birthdate: "" },
  // Two versions with DISTINCT labels — the whole point of the label chain. It
  // used to fall back to the card's name, so both of these read "Seraphine".
  versions: [
    { id: "default", name: "default", card: card(), images: ["avatar"],
      image_v: { avatar: "a1" }, image_descriptions: {}, importable_lore: 1 },
    { id: "veiled", name: "veiled", card: card({ description: "veiled" }), images: [],
      image_v: {}, image_descriptions: {}, importable_lore: 0 },
  ],
} satisfies DeepPartial<CharacterDetail>;

const CASEFILE = fixture<Casefile>({
  kind: "characters", id: "seraphine", name: "Seraphine", version: "default", role: "npc",
  scenes: [{ id: "001--the-tide-gate", title: "The Tide Gate" },
           { id: "004--the-priory-door", title: "The Priory Door" }],
  last_seen: "The Priory Door",
  standing: "Guarded. Will not be alone with the Reeve.",
  knows: "The priory's debt.", suspects: "",
  dossier: "A novice who counts the tide instead of the hours.",
  tagline: "", feels_toward: [], standing_facts: [],
});

let storedBirthdate = "";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getActorMembership).mockResolvedValue({ actor: "characters:seraphine", groups: [] });
  storedBirthdate = "";
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => {
    const detail = await vi.mocked(api.readCharacter).mock.results[vi.mocked(api.readCharacter).mock.results.length - 1]?.value as CharacterDetail;
    const version = detail.versions.find((v) => v.id === target.version);
    return artRead(target, (version?.images ?? []).map((name) => {
      const v = version?.image_v?.[name] ?? name;
      const url = api.actorImageUrl(target.scope, "characters", target.record_id, target.version ?? "", name, { v });
      return artImage(target, name, { v, url, thumb: url + "&w=160",
        description: version?.image_descriptions?.[name] ?? "", described: name in (version?.image_descriptions ?? {}),
        provenance: version?.inherited?.includes(name) ? "inherited" : target.scope.kind });
    }));
  });
  vi.mocked(api.appendRecordArt).mockResolvedValue({ name: "gallery_3", ext: "png", v: "new", image_id: "new" });
  vi.mocked(api.promoteRecordArt).mockResolvedValue({ ok: true });
  vi.mocked(api.removeRecordArt).mockResolvedValue({ ok: true });

  vi.mocked(api.readCharacter).mockImplementation(async () => fixture({
    ...DETAIL, meta: { ...DETAIL.meta, birthdate: storedBirthdate },
  }));
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  vi.mocked(api.updateVersion).mockResolvedValue({ ok: true });
  vi.mocked(api.createVersion).mockResolvedValue({ version: "young" });
  vi.mocked(api.setDefaultVersion).mockResolvedValue({ ok: true });
  vi.mocked(api.setCharacterName).mockResolvedValue({ ok: true });
  vi.mocked(api.setCharacterBirthdate).mockImplementation(async (_scope, _cid, value) => {
    storedBirthdate = value;
    return { ok: true };
  });
  vi.mocked(api.deleteCharacter).mockResolvedValue({ ok: true });
  vi.mocked(api.getCharacterTagline).mockResolvedValue({ tagline: "" });
  vi.mocked(api.setCharacterTagline).mockResolvedValue({ ok: true });
  vi.mocked(api.getCharacterVoiceAnchor).mockResolvedValue({ voice_anchor: "" });
  vi.mocked(api.setCharacterVoiceAnchor).mockResolvedValue({ ok: true });
  vi.mocked(api.listGreetings).mockResolvedValue([]);
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({ body: "" }));
  vi.mocked(api.getExpressions).mockResolvedValue({ format: 1, revision: null, effective_revision: "one", default_outfit: null, outfits: {}, hidden_outfits: [], hidden_expressions: {}, standard_labels: [], warnings: [] });
  vi.mocked(api.listImageAppearances).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([]);
  vi.mocked(api.listAppearances).mockResolvedValue([]);
  vi.mocked(api.getCasefile).mockResolvedValue(CASEFILE);
  vi.mocked(api.getCampaign).mockResolvedValue(fixture({ meta: { id: "run", name: "The Long Tide", world: "realm" } }));
  vi.mocked(api.getCampaignModule).mockResolvedValue(fixture({ resolved: null }));
  vi.mocked(api.getConfig).mockResolvedValue(fixture({ voice_anchor_cap: 400 }));
  vi.mocked(api.getWorldSheetsIndex).mockResolvedValue({ default: "", modules: [] });
  vi.mocked(api.listModules).mockResolvedValue([]);
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: GREG_MONTHS });
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({ primary: { provider: "gregorian" } }));
  vi.mocked(api.entityKinds).mockResolvedValue({ kinds: ["locations", "lore", "items"] });
  vi.mocked(api.libraryStatus).mockResolvedValue(
    { in_library: true, diverged: false, can_promote: false, can_push: false });
  vi.mocked(api.localizeImages).mockResolvedValue(undefined);
  vi.mocked(api.importCharacter).mockResolvedValue({ character: "seraphine", version: "young" });
});

/** The address bar, so a test can assert where a click went. */
export let lastLocation = "";
export function Spy() {
  const loc = useLocation();
  lastLocation = loc.pathname + loc.search;
  return null;
}

export async function renderWorld(url = "/worlds/realm/characters/seraphine") {
  lastLocation = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <Spy />
      <Routes>
        <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Seraphine", level: 1 });
}

export async function renderCampaign(url = "/campaigns/run/world/characters/seraphine") {
  lastLocation = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <Spy />
      <Routes>
        <Route path="/campaigns/:cid/world/characters/:eid" element={<CharacterPage campaign />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Seraphine", level: 1 });
}

/** The card field with this label, whether it is being read or edited. */
export function fieldBlock(label: string): HTMLElement {
  const head = screen.getAllByText(label, { selector: ".data-label" })[0];
  return head.closest(".card-field") as HTMLElement;
}

export async function editField(label: string) {
  fireEvent.click(within(fieldBlock(label)).getByRole("button", { name: new RegExp(`^(edit|add) ${label}$`, "i") }));
  return await screen.findByRole("textbox", { name: label });
}
