import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ENTITY_KINDS, type EntityKind, type EntityScope, type ModuleDetail } from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { usePublishShellContext } from "../components/ShellStatus";
import { CharacterGrid } from "../components/CharacterGrid";
import { characterHref } from "../components/character/shared";
import { PCEditor } from "../components/PCEditor";
import { TagEditor } from "../components/TagEditor";
import { EntityEditor } from "../components/EntityEditor";
import { GreetingEditor } from "../components/GreetingEditor";
import { PlotMapEditor } from "../components/PlotMapEditor";
import { LorebookImport } from "../components/LorebookImport";
import { ScenarioImport } from "../components/ScenarioImport";
import { WorldOverview } from "../components/WorldOverview";
import { WorldPushPanel } from "../components/WorldPushPanel";
import { ImagesView } from "../components/ImagesView";

type IndexKey =
  | "characters" | "pcs" | "creatures" | "groups"
  | "locations" | "items"
  | "lore" | "greetings" | "tags";

/** Overview is not a kind of record, so it is not in the index: it sits above
 *  the groups, as the world itself rather than as something inside it. Push is
 *  the other one: the campaigns fed by this world are not records in it either,
 *  and it is the only screen here that looks outward. Images is a third: art
 *  hangs off a record of one of eight kinds rather than being a kind of its own,
 *  so it cuts across the index instead of sitting in it (#200). */
type SectionKey = IndexKey | "overview" | "push" | "images";

/** The index that replaced the ten-tab strip.
 *
 *  The strip listed ten kinds in the order the tabs were added — an order
 *  nobody looks for a record in — and had no room left to say how many of
 *  anything there were. Three groups answer the question actually being asked
 *  (who / where & what / writing), and every row carries its own count, so the
 *  shape of a world is readable without opening it. */
const INDEX: { group: string; rows: { key: IndexKey; label: string }[] }[] = [
  { group: "Who", rows: [
    { key: "characters", label: "Characters" },
    { key: "pcs", label: "PCs" },
    { key: "creatures", label: "Creatures" },
    { key: "groups", label: "Groups" },
  ] },
  { group: "Where & what", rows: [
    { key: "locations", label: "Locations" },
    { key: "items", label: "Items" },
  ] },
  { group: "Writing", rows: [
    { key: "lore", label: "Lore" },
    { key: "greetings", label: "Greetings" },
    { key: "tags", label: "Tags" },
  ] },
];

/** How many records a row stands for.
 *
 *  Deliberately the same list read the row's own editor makes when you open it,
 *  rather than the world's stored `counts`: those are world-scoped, and half of
 *  this page's job is showing a *campaign's* fork of the same records. One
 *  source for both shapes also leaves nowhere for the number in the column and
 *  the rows in main to disagree. */
function countOf(key: IndexKey, scope: EntityScope, wid: string): Promise<number> {
  if (key === "characters") return api.listCharacters(scope).then((l) => l.length);
  if (key === "pcs") return api.listPCs(scope).then((l) => l.length);
  if (key === "greetings") return api.listGreetings(scope).then((l) => l.length);
  // The tag vocabulary belongs to a world and never to a campaign, which is
  // also why the Tags row only exists on the world shape -- so `wid` is the
  // right id to ask with here even though everything else takes the scope.
  if (key === "tags") return api.listTags(wid).then((t) => Object.keys(t).length);
  return api.listEntities(scope, key).then((l) => l.length);
}

export default function WorldView({ campaign = false }: { campaign?: boolean }) {
  const { wid: widParam = "", cid = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [wid, setWid] = useState(campaign ? "" : widParam);
  const [campaignName, setCampaignName] = useState("");
  const [name, setName] = useState("");
  // The world's cover token, for the header thumbnail. "" when it has none and
  // when the campaign branch below is what set the name -- that branch reads an
  // embedded meta rather than fetching the world, and a header picture is not
  // worth a second request on a path that deliberately avoids one.
  const [cover, setCover] = useState("");
  const [coverBroken, setCoverBroken] = useState(false);
  const [section, setSection] = useState<SectionKey>(campaign ? "characters" : "overview");
  const [counts, setCounts] = useState<Record<string, number | null>>({});
  const [campaignCount, setCampaignCount] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  /** Bumped by a scenario import, which is the one action here that creates
   *  records in half a dozen sections at once — so it has to re-ask for every
   *  count rather than leave the index reading the world as it was. */
  const [populated, setPopulated] = useState(0);
  const [charReset, setCharReset] = useState(0);
  const [loreReset, setLoreReset] = useState(0);
  /** The greeting a cross-navigation asked for, with a nonce: opening the same
   *  node twice has to reach the editor twice. */
  const [focusGreeting, setFocusGreeting] = useState<{ gid: string; n: number } | null>(null);
  /** A PC to open once the PC section is showing — see `openOwner`. Nonce'd
   *  for GreetingEditor's reason: following the same chip twice is two events.
   *
   *  Carries the scope it was captured in, and the prop below is DERIVED from
   *  that rather than cleared by an effect. An effect would be a render too
   *  late: child effects run before the parent's, so `PCEditor` would already
   *  have been handed the stale id and already have scheduled `select` against
   *  the new scope — opening a stranger who happens to share the id, or
   *  leaving the previous scope's PC on screen. Deciding it during render is
   *  what makes the scope change atomic.
   *
   *  `select` still clears it outright: choosing PCs from the column means the
   *  list, not whoever a chip pointed at earlier. */
  const [focusPC, setFocusPC] =
    useState<{ pid: string; n: number; scope: string } | null>(null);
  /** Which way the Greetings section is showing its records: the chip-list
   *  editor, or the same edges as a graph (#9). A view of one set of records
   *  rather than a second place to keep them -- both write through
   *  `api.setEdges`, so neither is the source of truth for the other. */
  const [greetingView, setGreetingView] = useState<"list" | "graph">("list");
  /** Bumped when the chip-list editor writes, so the graph re-reads. The two
   *  views draw the same records, and the switch does not wait for a save. */
  const [greetingEpoch, setGreetingEpoch] = useState(0);
  /** ...and the other way: the graph writes the same edges the chip list holds
   *  a copy of, and that copy stays mounted behind it. */
  const [mapEpoch, setMapEpoch] = useState(0);
  /** True while the chip-list editor is mid-write. Both views send whole edge
   *  arrays, so the map holds still until that save settles. */
  const [listSaving, setListSaving] = useState(false);
  /** True while the chip-list editor holds unsaved edge changes: its save
   *  replaces the greeting's whole arrays, so the map must not write in the
   *  meantime -- the draft would undo it on the next Save. */
  const [listEdgeDraft, setListEdgeDraft] = useState(false);
  /** ...and the mirror image: the map's own write may still be on the wire
   *  after the reader has switched back to the list. */
  const [mapWriting, setMapWriting] = useState(false);
  /** A pending "open this entity" for whichever EntityEditor is mounted. Keyed
   *  by kind so a nav aimed at Lore cannot be consumed by Items: all six
   *  editors are the same component, and only the kind tells them apart. */
  const [entityNav, setEntityNav] =
    useState<{ kind: IndexKey; focusEntry?: string; newOwner?: string } | null>(null);
  const [moduleCtx, setModuleCtx] = useState<ModuleDetail | null>(null);
  const [worldMid, setWorldMid] = useState("");
  const [params] = useSearchParams();

  /** The pending nav, but only for the editor that was aimed at. Every
   *  EntityEditor gets this rather than the raw state, so the first one to
   *  mount cannot swallow a nav meant for another kind. */
  const navFor = (kind: IndexKey) =>
    (entityNav && entityNav.kind === kind ? entityNav : null);

  // Editing a campaign's world is still being in that campaign, but it is a
  // different route: CampaignView unmounts and clears the context, so without
  // this the bar drops the campaign for the whole workflow. No scene -- the
  // one open in CampaignView is not open here, and naming it would be a claim
  // about a page the reader has left.
  usePublishShellContext(campaign && campaignName ? { campaign: campaignName, scene: "" } : null);

  useEffect(() => {
    if (campaign) {
      api.getCampaign(cid).then((c) => {
        setCampaignName(c.meta.name);
        setWid(c.meta.world);
        setName(c.meta.world_name ?? c.meta.world); // embedded: no second fetch
      });
      api.getCampaignModule(cid)
        .then(({ resolved }) => (resolved ? api.readModule(resolved) : null))
        .then((m) => setModuleCtx(m))
        .catch(() => setModuleCtx(null));
    } else {
      setWid(widParam);
      api.getWorld(widParam)
        .then((w) => { setName(w.meta.name); setCover(w.meta.cover ?? ""); })
        .catch(() => { setName(widParam); setCover(""); });
      Promise.all([api.getWorldSheetsIndex(widParam), api.listModules()])
        .then(([index, installed]) =>
          setWorldMid(index.default || index.modules[0] || installed[0]?.id || ""))
        .catch(() => setWorldMid(""));
    }
  }, [campaign, cid, widParam]);

  // world path: re-resolve the module context whenever the picked module id changes
  useEffect(() => {
    if (campaign) return;
    if (!worldMid) { setModuleCtx(null); return; }
    api.readModule(worldMid).then((m) => setModuleCtx(m)).catch(() => setModuleCtx(null));
  }, [campaign, worldMid]);

  const scope: EntityScope = campaign ? { kind: "campaign", id: cid } : { kind: "world", id: wid };
  /** A character whose page sent the reader back here. A campaign grid opens on
   *  its own cast, so a character reached by a link and never played would
   *  vanish on the way back and read as deleted — the page hands their id over
   *  in `location.state` and the grid widens the filter for them. */
  const reveal = (location.state as { reveal?: string } | null)?.reveal ?? null;
  const scopeKey = `${scope.kind}:${scope.id}`;
  // A focused PC id belongs to the scope it was read in, so it is offered only
  // back in that scope. See `focusPC` on why this is derived, not cleared.
  const pcFocus = focusPC?.scope === scopeKey ? focusPC : null;
  // The tag vocabulary is a world concern (campaign PC tags are free strings)
  // and the overview is a world's setup checklist -- neither is something a
  // campaign's fork of the world has, so neither is offered on that shape.
  const groups = useMemo(
    () => INDEX
      .map((g) => ({ group: g.group, rows: g.rows.filter((r) => !(campaign && r.key === "tags")) }))
      .filter((g) => g.rows.length > 0),
    [campaign],
  );

  // One request per row, the way the library column does it: they settle
  // independently, and a row whose read fails costs its own number rather than
  // blanking the index.
  //
  // Re-run when the section changes, because that is the first moment the
  // column can hear about a record created in the section being left: the
  // editors own their own lists and have no way to say they added to one. A
  // count that arrives a click late is worth more than one that is quietly
  // wrong.
  useEffect(() => {
    if (!wid) return; // campaign shape: the world id arrives with the campaign
    let live = true;
    for (const row of groups.flatMap((g) => g.rows)) {
      // Started inside a promise so a read that throws *synchronously* is the
      // same "unknown, show a dash" case as one that rejects, rather than an
      // exception out of an effect that takes the whole column down with it.
      Promise.resolve()
        .then(() => countOf(row.key, scope, wid))
        .then((n) => { if (live) setCounts((c) => ({ ...c, [row.key]: n })); })
        .catch(() => { if (live) setCounts((c) => ({ ...c, [row.key]: null })); });
    }
    return () => { live = false; };
    // `scope` is rebuilt every render; the values it is made of are the real
    // dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign, cid, wid, groups, section, populated]);

  // How many campaigns are played in this world: the one fact about a world
  // that is not a record inside it. Only on the world shape -- a campaign
  // already knows which campaign it is, and this read scans every campaign.
  useEffect(() => {
    if (campaign || !wid) return;
    let live = true;
    api.listCampaigns()
      .then((cs) => { if (live) setCampaignCount(cs.filter((c) => c.world === wid).length); })
      .catch(() => { if (live) setCampaignCount(null); });
    return () => { live = false; };
  }, [campaign, wid]);

  /** Picking a section out of the index. Distinct from the cross-navigation
   *  callbacks below, which arrive at a section *carrying* a record to focus:
   *  choosing Characters from the column means the grid, not whoever happened
   *  to be open in it last. */
  function select(key: SectionKey) {
    setSection(key);
    if (key === "characters") setCharReset((n) => n + 1);
    if (key === "greetings") setFocusGreeting(null);
    if (key === "pcs") setFocusPC(null);
    // ...and any entity nav, for the same reason the two above are cleared:
    // choosing a section from the column means the list, not whoever happened
    // to be open in it. No path reaching here today leaves one pending -- every
    // setter sets the section in the same breath, and the editor consumes it on
    // mount -- so this is the invariant stated rather than a hole plugged.
    setEntityNav(null);
  }

  // A present-character link from the greeting view, an owner chip, or a search
  // hit. A character has a page of its own now, so this LEAVES this route
  // rather than opening a pane inside it -- `characterHref` is the one place
  // that knows where that page lives in each scope.
  function openCharacter(cid: string, vid: string) {
    navigate(characterHref(scope, cid, vid || undefined), { replace: true });
  }

  // a world-greeting link from a character page jumps to that greeting -- as
  // does a node on the plot map, which is why this also drops back to the list:
  // opening a greeting means its editor, and the graph has no detail pane.
  function openGreeting(gid: string) {
    setFocusGreeting((prev) => ({ gid, n: (prev?.n ?? 0) + 1 }));
    setGreetingView("list");
    setSection("greetings");
  }

  // an owner editor's lore panel routes to Lore (open an entry, or start a pre-owned one)
  function openLore(nav: { focusEntry?: string; newOwner?: string }) {
    setEntityNav({ kind: "lore", ...nav });
    setSection("lore");
  }

  // a search hit, or any other deep link, opens the record it names
  function openEntity(kind: IndexKey, id: string) {
    setEntityNav({ kind, focusEntry: id });
    setSection(kind);
  }

  // A `<kind>:<id>` chip — a lore owner, or the target of a ref-valued entity
  // field (#222) — jumps to the record it names. Split on the FIRST colon:
  // `paths.safe_id` rejects a colon in an id, so the head is always the kind.
  function openOwner(ref: string) {
    const i = ref.indexOf(":");
    const kind = ref.slice(0, i);
    const id = ref.slice(i + 1);
    if (kind === "characters") openCharacter(id, ""); // "" -> the page opens the default version
    else if (kind === "pcs") {
      setFocusPC((p) => ({ pid: id, n: (p?.n ?? 0) + 1, scope: scopeKey }));
      setSection("pcs");
    }
    // The entity kinds land on the record itself, not merely its section: a
    // ref names one record, and dropping the reader in a list to find it again
    // would be answering the question with the index.
    else if (ENTITY_KINDS.includes(kind as EntityKind)) openEntity(kind as IndexKey, id);
  }

  /** Open what the URL names: `?section=lore&id=the-salt-pact`, plus `&v=` for
   *  a character's card version.
   *
   *  This is what makes a search hit followable — a result is a record, and
   *  landing on the section it lives in and leaving the reader to find it
   *  again would be answering a question with the index. Query params rather
   *  than path segments because the section and the open record are this
   *  page's *state*, not a deeper resource: everything else here changes them
   *  without moving the route, and a deep link has to arrive at the same
   *  place, not at a second one.
   *
   *  Re-runs when the params change, and only then: picking a section from the
   *  column leaves the URL alone, so nothing here undoes it. */
  useEffect(() => {
    const section = params.get("section") ?? "";
    const id = params.get("id") ?? "";
    if (!section) return;
    // `?section=characters` alone is a request for the GRID — the rail's own
    // row, and the link the hub's cast card carries. Only an `id` is a request
    // for one character, and only that redirects to their page; without this
    // guard the bare section navigated to an empty character id.
    if (section === "characters") {
      if (id) openCharacter(id, params.get("v") ?? "");
      else setSection("characters");
      return;
    }
    if (section === "greetings") { openGreeting(id); return; }
    // Images is a section like any other to the column, but it is not in
    // `INDEX` -- that list is the world's record kinds, and images are a view
    // across all of them rather than one more kind. Without this branch
    // `?section=images` fell through and did nothing, which is what the rail's
    // Images row addresses.
    if (section === "images") { setSection("images"); return; }
    if (INDEX.some((g) => g.rows.some((r) => r.key === section && r.key !== "tags"))) {
      // `pcs` has no per-record focus of its own yet; the section is as close
      // as this can land, which is still nearer than the page it started on.
      if (section === "pcs") setSection("pcs");
      else openEntity(section as IndexKey, id);
    }
    // The openers are redeclared every render and close over nothing that
    // outlives one -- `params` is the only real dependency. Listing them would
    // re-run this on every render, which for `openCharacter` means navigating
    // to the character named in the URL again after the reader has left them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  /** What this page contributes to ⌘K: its own index, so a section can be
   *  reached by name from anywhere in the world rather than only by finding
   *  its row. */
  const paletteSource = useCallback((): PaletteItem[] => {
    const out: PaletteItem[] = [];
    if (!campaign) {
      out.push({ id: "world-section:overview", group: "IN THIS WORLD", label: "Overview",
                 meta: `${name} · setup`, run: () => select("overview") });
      out.push({ id: "world-section:push", group: "IN THIS WORLD", label: "Push to campaigns",
                 meta: `${name} · pending changes`, run: () => select("push") });
      out.push({ id: "world-section:images", group: "IN THIS WORLD", label: "Images",
                 meta: `${name} · art`, run: () => select("images") });
    }
    for (const g of groups) {
      for (const r of g.rows) {
        out.push({ id: `world-section:${r.key}`, group: "IN THIS WORLD", label: r.label,
                   meta: `${name} · ${g.group.toLowerCase()}`, run: () => select(r.key) });
      }
    }
    return out;
    // `select` is redeclared every render and closes over nothing that outlives
    // one, so it is deliberately not a dependency.
  }, [campaign, groups, name]);
  usePaletteSource(paletteSource);

  if (campaign && !wid) return null;

  const rows = groups.flatMap((g) => g.rows);
  const groupOf = (key: SectionKey) =>
    groups.find((g) => g.rows.some((r) => r.key === key))?.group ?? "World";
  const labelOf = (key: SectionKey) => {
    const row = rows.find((r) => r.key === key)?.label;
    if (row) return row;
    if (key === "push") return "Push to campaigns";
    return key === "images" ? "Images" : "Overview";
  };
  // Undefined is "still loading", null is "that read failed" — both genuinely
  // unknown, and a dash says so where a 0 would claim the section is empty.
  const dash = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n);

  const column = (
    <>
      {campaign ? (
        <button className="column-back" onClick={() => navigate(`/campaigns/${cid}`)}>
          ‹ {campaignName} / World Copy
        </button>
      ) : (
        <Link className="column-back" to="/worlds">‹ All worlds</Link>
      )}
      <div className="world-ident">
        <div className="eyebrow">World</div>
        <h2 className="world-ident-name">{name}</h2>
        {/* The facts about a world that are not records in it. A campaign's
            fork answers the same slot with whose copy this is. */}
        <div className="world-facts">
          {campaign
            ? `copy · ${campaignName}`
            : `${dash(counts.tags)} tags · ${dash(campaignCount)} campaigns`}
        </div>
      </div>

      {!campaign && (
        <button className={"column-row" + (section === "overview" ? " active" : "")}
                onClick={() => select("overview")}>
          <span className="column-row-label">Overview</span>
          <span className="column-row-count" aria-hidden>→</span>
        </button>
      )}

      {/* World shape only: a campaign's fork of a world feeds nothing, and its
          own pending changes are reviewed in the campaign, not here. */}
      {!campaign && (
        <button className={"column-row" + (section === "push" ? " active" : "")}
                onClick={() => select("push")}>
          <span className="column-row-label">Push to campaigns</span>
          <span className="column-row-count">{dash(campaignCount)}</span>
        </button>
      )}

      {/* World shape only, for the same reason the greeting tagger it carries
          is: the subjects sidecar is written world-side, and a campaign's fork
          browses its own diverged art in the editor that owns it. No count —
          "how many pictures" is not a number anyone navigates by, and the two
          reads behind it are the ones this view exists to make once. */}
      {!campaign && (
        <button className={"column-row" + (section === "images" ? " active" : "")}
                onClick={() => select("images")}>
          <span className="column-row-label">Images</span>
        </button>
      )}

      {groups.map((g) => (
        <ColumnSection key={g.group} label={g.group}>
          {g.rows.map((r) => (
            <button key={r.key}
                    className={"column-row" + (section === r.key ? " active" : "")}
                    onClick={() => select(r.key)}>
              <span className="column-row-label">{r.label}</span>
              <span className="column-row-count">{dash(counts[r.key])}</span>
            </button>
          ))}
        </ColumnSection>
      ))}
    </>
  );

  const footer = campaign ? (
    // A fork's way back to what it forked from.
    <Link className="column-link" to={`/worlds/${wid}`}>
      The source world <span aria-hidden>→</span>
    </Link>
  ) : (
    <>
      <button className="column-link" onClick={() => { setImportOpen(true); setSection("lore"); }}>
        Import lorebook <span aria-hidden>→</span>
      </button>
      <button className="column-link"
              onClick={() => { setScenarioOpen(true); setSection("overview"); }}>
        Import scenario card <span aria-hidden>→</span>
      </button>
    </>
  );

  return (
    <PageShell column={column} footer={footer} columnLabel="World index">
      <div className="page-wide view-anim">
        {campaign && (
          <div className="fork-banner">
            ⌦ Campaign view — records follow the world until you change them here;
            edits belong to this campaign and leave the original world untouched.
          </div>
        )}
        <div className="shelf-head">
          {/* The cover, when the world has one and it loads. Dropped entirely
              rather than shown as a placeholder: on the worlds shelf the empty
              box is what tells you a world has no picture yet, but here the
              header already has a name and a section, and an empty frame beside
              them says nothing a reader wanted. */}
          {cover && !coverBroken && (
            <div className="shelf-cover">
              <img src={api.worldCoverUrl(wid, { w: 208, v: cover })}
                   alt={`${name} cover`} onError={() => setCoverBroken(true)} />
            </div>
          )}
          <div>
            <div className="eyebrow">{name} · {groupOf(section)}</div>
            <h1 className="screen-title">{labelOf(section)}</h1>
          </div>
        </div>

        {/* Each editor owns its own list and detail (the list/detail pattern),
            so opening a record is a swap inside main: the column is a sibling
            that nothing here re-renders, and it keeps its selection and its
            scroll for free. */}
        {!campaign && section === "overview" && (
          <>
            {/* The one importer that populates a whole world rather than one
                section, so it lives on the setup screen rather than inside
                Lore the way the lorebook importer does. Controlled for the
                same reason: the column's footer row opens it. */}
            <details className="import-section" open={scenarioOpen}
                     onToggle={(e) => setScenarioOpen(e.currentTarget.open)}>
              <summary>Import scenario card</summary>
              <ScenarioImport wid={wid} onImported={() => setPopulated((n) => n + 1)} />
            </details>
            <WorldOverview key={populated} wid={wid} onNavigate={(t) => select(t as SectionKey)}
                           worldMid={worldMid} onPickMid={setWorldMid} />
          </>
        )}
        {!campaign && section === "push" && <WorldPushPanel wid={wid} />}
        {/* Keyed by wid: `/worlds/:wid` keeps this route's instance across a
            world switch, so an unkeyed gallery would go on showing the previous
            world's art -- and its tagging queue -- until four reads settle, or
            indefinitely if one stalls. */}
        {!campaign && section === "images"
          && <ImagesView key={wid} wid={wid} forCampaign={params.get("for")} />}
        {section === "characters" && <CharacterGrid scope={scope} wid={wid} resetSignal={charReset} reveal={reveal} module={moduleCtx} />}
        {section === "pcs" && <PCEditor scope={scope} wid={wid} onOpenLore={openLore}
                                       focus={pcFocus?.pid ?? null} focusNonce={pcFocus?.n ?? 0}
                                       module={moduleCtx} />}
        {!campaign && section === "tags" && <TagEditor wid={wid} />}
        {section === "locations" && <EntityEditor wid={wid} scope={scope} kind="locations" nav={navFor("locations")}
                                          onNavConsumed={() => setEntityNav(null)} onReclassified={openEntity} onOpenLore={openLore} module={moduleCtx} />}
        {section === "lore" && (
          <>
            {/* Controlled so the column's pinned import row can open it: the
                importer is a lore-shaped action, and a footer row that only put
                you near it would leave you hunting for the disclosure. */}
            {!campaign && <details className="import-section" open={importOpen}
                                   onToggle={(e) => setImportOpen(e.currentTarget.open)}>
              <summary>Import lorebook / world-info</summary>
              <LorebookImport wid={wid} onImported={() => setLoreReset((n) => n + 1)} />
            </details>}
            <EntityEditor key={loreReset} wid={wid} scope={scope} kind="lore" nav={navFor("lore")}
                          onNavConsumed={() => setEntityNav(null)} onReclassified={openEntity} onOpenOwner={openOwner} module={moduleCtx} />
          </>
        )}
        {section === "items" && <EntityEditor wid={wid} scope={scope} kind="items" nav={navFor("items")}
                                          onNavConsumed={() => setEntityNav(null)} onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "groups" && <EntityEditor wid={wid} scope={scope} kind="groups" nav={navFor("groups")}
                                          onNavConsumed={() => setEntityNav(null)} onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "creatures" && <EntityEditor wid={wid} scope={scope} kind="creatures" nav={navFor("creatures")}
                                          onNavConsumed={() => setEntityNav(null)} onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "greetings" && (
          <>
            <div className="chips section-views" role="group" aria-label="Greetings view">
              {([["list", "List"], ["graph", "Plot map"]] as const).map(([key, label]) => (
                <button key={key} className={"chip" + (greetingView === key ? " on" : "")}
                        aria-pressed={greetingView === key}
                        onClick={() => setGreetingView(key)}>{label}</button>
              ))}
            </div>
            {/* Hidden rather than unmounted: the editor holds a half-written
                greeting in component state, and switching to the graph used to
                take it with no Save, no Cancel and no warning. */}
            <div hidden={greetingView !== "list"}>
              <GreetingEditor scope={scope} wid={wid} onOpenCharacter={openCharacter}
                              onOpenLocation={(id) => openEntity("locations", id)}
                              focus={focusGreeting?.gid ?? null} focusNonce={focusGreeting?.n ?? 0}
                              onChanged={() => setGreetingEpoch((n) => n + 1)}
                              onBusy={setListSaving} onEdgeDraft={setListEdgeDraft}
                              hold={mapWriting ? "The plot map is still writing these links. Wait for it before saving." : null}
                              refreshKey={mapEpoch} />
            </div>
            {greetingView === "graph" && (
              <PlotMapEditor scope={scope} onOpenGreeting={openGreeting}
                             reloadKey={greetingEpoch}
                             // `mapWriting` is here as well as on the editor: a
                             // map unmounted mid-write keeps its PUT alive, and
                             // switching straight back mounts a NEW map with an
                             // empty queue of its own, free to write over it.
                             hold={listSaving
                               ? "The greeting editor is saving. Its save writes these same links, so the map waits for it."
                               : listEdgeDraft
                                 ? "The greeting editor has unsaved link changes. Save or cancel them first — its save replaces these same links."
                                 : mapWriting
                                   ? "A link from this map is still being written. Wait for it before changing another."
                                   : null}
                             onBusy={setMapWriting}
                             onChanged={() => setMapEpoch((n) => n + 1)} />
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}
