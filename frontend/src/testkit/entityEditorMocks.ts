// Hoisted factories cannot import the API they replace. Suites load this factory lazily.
export function entityApiMock() {
  return {
    // The editor branches on `instanceof ApiError` to tell a stale-record 409
    // from any other failure, so the mock has to hand back a real class. Defined
    // inside the factory: `vi.mock` is hoisted above every top-level statement,
    // so a class declared out here would not exist yet when it runs.
    ApiError: class extends Error {
      constructor(public status: number, public detail: string, public kind?: string,
                  public body?: Record<string, unknown>) {
        super(detail);
      }
    },
    SECRECY_LEVELS: ["public", "secret", "gm-only"],
    ENTITY_KINDS: ["locations", "lore", "items", "groups", "creatures"],
    SECRECY_LABELS: { public: "Public", secret: "Secret", "gm-only": "GM-only" },
    ACTIVATION_FIELDS: [
      { key: "secondary_keys", label: "Secondary keys", widget: "text" },
      { key: "key_logic", label: "Key logic", widget: "choice",
        options: ["and_any", "and_all", "not_any", "not_all"] },
      { key: "scan_depth", label: "Scan depth", widget: "number", min: 0, max: 100 },
      { key: "sticky", label: "Sticky", widget: "number", min: 0, max: 50 },
      { key: "cooldown", label: "Cooldown", widget: "number", min: 0, max: 50 },
      { key: "priority", label: "Priority", widget: "number", min: 0, max: 1000 },
      { key: "keep", label: "Keep under budget", widget: "bool" },
      { key: "recursion", label: "Recursion", widget: "choice",
        options: ["both", "pulled_only", "pulls_only", "none"] },
      { key: "known_by", label: "Known by", widget: "refs" },
    ],
    ENTITY_FIELDS: {
      locations: [{ key: "climate", label: "Climate", widget: "choice", source: "climates" },
                  { key: "persistence", label: "Weather persistence", widget: "number",
                    min: 0, max: 1 },
                  { key: "weather_zone", label: "Weather zone", widget: "text" }],
      lore: [],
      items: [{ key: "item_type", label: "Type", widget: "text" },
              { key: "rarity", label: "Rarity", widget: "text" },
              { key: "holder", label: "Held by", widget: "ref",
                kinds: ["characters", "pcs", "groups", "locations"] }],
      groups: [{ key: "group_type", label: "Type", widget: "text" },
               { key: "leader", label: "Leader", widget: "ref", kinds: ["characters", "pcs"] },
               { key: "members", label: "Members", widget: "ref", kinds: ["characters", "pcs"], multi: true },
               { key: "headquarters", label: "Headquarters", widget: "ref", kinds: ["locations"] }],
      creatures: [{ key: "creature_type", label: "Type", widget: "text" },
                  { key: "threat", label: "Threat", widget: "text" },
                  { key: "habitat", label: "Habitat", widget: "ref",
                    kinds: ["locations"], multi: true }],
    },
    api: {
      readRecordArt: vi.fn(), appendRecordArt: vi.fn(), promoteRecordArt: vi.fn(), removeRecordArt: vi.fn(),
      setRecordArtFocus: vi.fn(), setRecordArtDescription: vi.fn(), draftRecordArtDescription: vi.fn(), getImageUsage: vi.fn(),
      listEntities: vi.fn(),
      createEntity: vi.fn(),
      readEntity: vi.fn(),
      updateEntity: vi.fn(),
      deleteEntity: vi.fn(),
      reclassifyEntity: vi.fn(),
      previewAdoptSt: vi.fn(),
      adoptSt: vi.fn(),
      adoptStAll: vi.fn(),
      previewAdoptStAll: vi.fn(),
      listCharacters: vi.fn(),
      listPCs: vi.fn(),
      listClimates: vi.fn(),
      getGroupState: vi.fn().mockResolvedValue({}),
      putGroupState: vi.fn(),
      listEntityImages: vi.fn(),
      setEntityImageDescription: vi.fn(),
      draftEntityImageDescription: vi.fn(),
      // The campaign-scope sidebar's LibraryPanel (#52, #53). "already library
      // content, unedited" is the state that renders no button, so every test
      // here sees the sidebar it always did; LibraryPanel.test.tsx owns the rest.
      libraryStatus: vi.fn().mockResolvedValue(
        { in_library: true, diverged: false, can_promote: false, can_push: false }),
      promoteToLibrary: vi.fn(),
      // World scope reaches DemotePanel; campaign scope reaches LibraryPanel.
      // Both resolve to "nothing to do", so these views render as they always did.
      libraryDependents: vi.fn().mockResolvedValue([]),
      demoteFromLibrary: vi.fn(),
      pushToLibrary: vi.fn(),
      // `?w=` then `?v=`, the order the real builder writes them in, so a test
      // can tell a thumbnail from the original it links to.
      actorImageUrl: (sc: { kind: string; id: string }, k: string, a: string, v: string, n: string,
                      o?: { w?: number; v?: string | null }) =>
        `/img/${sc.id}/${k}/${a}/${v}/${n}`
        + (o?.w || o?.v ? "?" + [o.w && `w=${o.w}`, o.v && `v=${o.v}`].filter(Boolean).join("&") : ""),
      entityImageUrl: (_s: any, k: string, e: string, n: string,
                       o?: { w?: number; v?: string | null }) =>
        `/img/${k}/${e}/${n}`
        + (o?.w || o?.v ? "?" + [o.w && `w=${o.w}`, o.v && `v=${o.v}`].filter(Boolean).join("&") : ""),
      getSheet: vi.fn(),
      putSheet: vi.fn(),
      putSheetCreation: vi.fn(),
      readModuleContent: vi.fn(),
      instantiateContent: vi.fn(),
    },
  };
}
