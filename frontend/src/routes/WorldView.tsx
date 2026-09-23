import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
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
import {
  defaultSection, legacyTarget, parseWorldTail, sectionHref,
  type RecordSection, type Section,
} from "../worldPaths";

type IndexKey =
  | "characters" | "pcs" | "creatures" | "groups"
  | "locations" | "items"
  | "lore" | "greetings" | "tags";

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

/** The rows whose number `GET /worlds/{wid}` already carries in `counts`: every
 *  record kind, counted as a directory tally. Everything but Tags. */
const STORED: readonly IndexKey[] = [
  "characters", "pcs", "creatures", "groups", "locations", "items", "lore", "greetings",
];

/** The stored rows' numbers out of a world read. A kind the read did not
 *  report is `null`, and draws a dash: an older server's missing key is
 *  "unknown", never a section with nothing in it. */
function storedCounts(counts: Record<string, number> | undefined): Record<string, number | null> {
  return Object.fromEntries(STORED.map((k) => {
    const n = counts?.[k];
    return [k, typeof n === "number" ? n : null];
  }));
}

/** How many records a row stands for, read from the row's own list.
 *
 *  What the CAMPAIGN shape counts with, and the world shape only for Tags.
 *  A campaign's fork is an overlay union -- its own records plus everything
 *  it inherits -- which the world's stored tallies know nothing about, so its
 *  numbers are the same list reads the rows' editors make; overlay-aware
 *  tallies would need an endpoint of their own.
 *
 *  The world shape used to count this way too, for one source across both
 *  shapes, and paid for it with nine full lists -- every character card
 *  parsed, every entity token-counted -- on every visit and every click, when
 *  the world read the header already makes carries the counts. The two can
 *  disagree only over a record damaged past listing (a character directory
 *  whose every version file is gone is tallied but not listed), which is a
 *  number one high beside a store that needs repair, not a count that drifts. */
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

/** Read the numbers for `keys` and hand each to `apply` as it lands. A failed
 *  read costs the numbers it was answering -- dashes -- and never the index.
 *
 *  World shape: any stored row asked for is ONE `getWorld`, applied for every
 *  stored row it answers -- a directory tally per kind, far cheaper than the
 *  list it replaces, so it fails or lands for all of them together. Campaign
 *  shape, and Tags: the row's own list, one request each, settling
 *  independently. Started inside a promise so a read that throws
 *  *synchronously* is the same "unknown, show a dash" case as one that
 *  rejects, rather than an exception out of an effect that takes the whole
 *  column down with it. */
function readCounts(keys: readonly IndexKey[], campaign: boolean, scope: EntityScope,
                    wid: string, apply: (patch: Record<string, number | null>) => void) {
  const listed = campaign ? keys : keys.filter((k) => !STORED.includes(k));
  if (!campaign && keys.some((k) => STORED.includes(k))) {
    Promise.resolve()
      .then(() => api.getWorld(wid))
      .then((w) => apply(storedCounts(w.counts)))
      .catch(() => apply(storedCounts(undefined)));
  }
  for (const key of listed) {
    Promise.resolve()
      .then(() => countOf(key, scope, wid))
      .then((n) => apply({ [key]: n }))
      .catch(() => apply({ [key]: null }));
  }
}

export default function WorldView({ campaign = false }: { campaign?: boolean }) {
  const { wid: widParam = "", cid = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const shape = campaign ? "campaign" : "world";
  // The scope this page's addresses are built under. `widParam`, not the
  // async `wid` state below: for the world shape they are the same value, and
  // for the campaign shape this needs no world id at all, so it is available
  // on the very first render -- the redirects further down, and every
  // cross-navigation callback, depend on that.
  const scopeForPaths: EntityScope = campaign
    ? { kind: "campaign", id: cid }
    : { kind: "world", id: widParam };
  const params = useSearchParams()[0];
  // `location.pathname` is raw. `useParams()["*"]` is not, and an id
  // containing a slash is exactly what the difference loses.
  const tail = location.pathname.split("/").filter(Boolean).slice(campaign ? 3 : 2).join("/");
  const parsed = parseWorldTail(tail, shape);
  const section: Section = parsed.ok ? parsed.section : defaultSection(shape);
  const rid = parsed.ok ? parsed.rid : null;
  /** `?owner=` is only meaningful on a recordless Lore screen: it pre-owns the
   *  blank form. Read nowhere else, so it cannot ride along to Items or sit
   *  beside a record that already has owners of its own. */
  const newOwner = section === "lore" && !rid ? (params.get("owner") ?? "") : "";
  const [wid, setWid] = useState(campaign ? "" : widParam);
  const [campaignName, setCampaignName] = useState("");
  const [name, setName] = useState("");
  // The world's cover token, for the header thumbnail. "" when it has none and
  // when the campaign branch below is what set the name -- that branch reads an
  // embedded meta rather than fetching the world, and a header picture is not
  // worth a second request on a path that deliberately avoids one.
  const [cover, setCover] = useState("");
  const [coverBroken, setCoverBroken] = useState(false);
  const [counts, setCounts] = useState<Record<string, number | null>>({});
  const [campaignCount, setCampaignCount] = useState<number | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  /** Bumped by a scenario import, which is the one action here that creates
   *  records in half a dozen sections at once — so it has to re-ask for every
   *  count rather than leave the index reading the world as it was. */
  const [populated, setPopulated] = useState(0);
  const [loreReset, setLoreReset] = useState(0);
  /** Bumped when the chip-list editor writes, so the graph re-reads. The two
   *  views draw the same records, and the switch does not wait for a save. */
  const [greetingEpoch, setGreetingEpoch] = useState(0);
  /** ...and the other way: the graph writes the same edges a record open in
   *  the chip list already holds a copy of. That record (or a half-written
   *  draft) stays selected across the switch -- `?view=graph` keeps whatever
   *  record segment was already in the address -- so it is still there,
   *  hidden behind the graph, to re-read. */
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
  const [moduleCtx, setModuleCtx] = useState<ModuleDetail | null>(null);
  const [worldMid, setWorldMid] = useState("");

  // Editing a campaign's world is still being in that campaign, but it is a
  // different route: CampaignView unmounts and clears the context, so without
  // this the bar drops the campaign for the whole workflow. No scene -- the
  // one open in CampaignView is not open here, and naming it would be a claim
  // about a page the reader has left.
  usePublishShellContext(campaign && campaignName ? { campaign: campaignName, scene: "" } : null);

  /** Which world, or which campaign's copy of one, the index is counting. */
  const scopeKey = campaign ? `campaign:${cid}` : `world:${widParam}`;
  /** Bumped when `scopeKey` changes, so a count still in flight for the world
   *  being left cannot land in the one being entered -- the route keeps this
   *  instance across a world switch. A generation rather than a per-effect
   *  `live` flag because the counts are started from three effects, and a
   *  section click's cleanup must not drop the previous click's answer, only a
   *  scope change may. */
  const countGen = useRef(0);
  /** An `apply` for `readCounts`, bound to the generation that started it. */
  const countsFor = useCallback((gen: number) => (patch: Record<string, number | null>) => {
    if (countGen.current === gen) setCounts((c) => ({ ...c, ...patch }));
  }, []);
  // Before the world read below, which captures the generation this starts.
  useEffect(() => {
    countGen.current += 1;
    // Dashes rather than the previous scope's numbers while the new ones load.
    setCounts((c) => (Object.keys(c).length ? {} : c));
  }, [scopeKey]);

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
      // The header's read carries the index's numbers too: every record row
      // is a stored tally on it, so a visit lists nothing to count it.
      const apply = countsFor(countGen.current);
      api.getWorld(widParam)
        .then((w) => { setName(w.meta.name); setCover(w.meta.cover ?? ""); apply(storedCounts(w.counts)); })
        .catch(() => { setName(widParam); setCover(""); apply(storedCounts(undefined)); });
      Promise.all([api.getWorldSheetsIndex(widParam), api.listModules()])
        .then(([index, installed]) =>
          setWorldMid(index.default || index.modules[0] || installed[0]?.id || ""))
        .catch(() => setWorldMid(""));
    }
  }, [campaign, cid, widParam, countsFor]);

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
  // The tag vocabulary is a world concern (campaign PC tags are free strings)
  // and the overview is a world's setup checklist -- neither is something a
  // campaign's fork of the world has, so neither is offered on that shape.
  const groups = useMemo(
    () => INDEX
      .map((g) => ({ group: g.group, rows: g.rows.filter((r) => !(campaign && r.key === "tags")) }))
      .filter((g) => g.rows.length > 0),
    [campaign],
  );

  // The first count of a scope. The world shape's record rows came with the
  // world read above; what is left is Tags, whose vocabulary no world read
  // carries. The campaign shape lists every row, one request each so they
  // settle independently -- and does not wait for the campaign's world id to
  // do it, since none of its lists is addressed by one.
  useEffect(() => {
    const apply = countsFor(countGen.current);
    if (campaign) {
      readCounts(groups.flatMap((g) => g.rows.map((r) => r.key)), true,
                 { kind: "campaign", id: cid }, "", apply);
    } else {
      readCounts(["tags"], false, { kind: "world", id: widParam }, widParam, apply);
    }
  }, [campaign, cid, widParam, groups, countsFor]);

  /** What the effect below last saw, so it can tell what moved. */
  const lastSeen = useRef<{ scope: string; section: Section; populated: number } | null>(null);

  // A section change is the first moment the column can hear about a record
  // created in the section being left: the editors own their own lists and
  // have no way to say they added to one. A count that arrives a click late is
  // worth more than one that is quietly wrong.
  //
  // What is re-read is what the click could have moved, and no more -- it
  // used to be every row, nine full lists per click. The section being left
  // is where a record was added or removed. On the world shape that is one
  // world read, which tallies every kind at once and so also covers a
  // reclassify's destination. On the campaign shape it is that row's list,
  // plus the row being entered, where a reclassify lands -- a read its editor
  // is making at the same moment, which the api client's in-flight sharing
  // turns into one request.
  useEffect(() => {
    const prev = lastSeen.current;
    lastSeen.current = { scope: scopeKey, section, populated };
    // A new scope is counted by the effect above; the same inputs seen again
    // are StrictMode's rehearsal, or a render in which nothing moved.
    if (!prev || prev.scope !== scopeKey) return;
    const rows = groups.flatMap((g) => g.rows.map((r) => r.key));
    const isRow = (s: Section): s is IndexKey => (rows as Section[]).includes(s);
    const touched: IndexKey[] =
      // A scenario import writes half a dozen sections at once.
      populated !== prev.populated ? rows
      : section !== prev.section ? (campaign ? [prev.section, section] : [prev.section]).filter(isRow)
      : [];
    if (!touched.length) return;
    readCounts(touched, campaign,
               campaign ? { kind: "campaign", id: cid } : { kind: "world", id: widParam },
               widParam, countsFor(countGen.current));
  }, [scopeKey, section, populated, campaign, cid, widParam, groups, countsFor]);

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

  // A present-character link from the greeting view, an owner chip, or a search
  // hit. A character has a page of its own now, so this LEAVES this route
  // rather than opening a pane inside it -- `characterHref` is the one place
  // that knows where that page lives in each scope.
  function openCharacter(cid: string, vid: string) {
    navigate(characterHref(scope, cid, vid || undefined), { replace: true });
  }

  // a world-greeting link from a character page jumps to that greeting -- as
  // does a node on the plot map. A record segment forces the list view (the
  // graph has no detail pane), so navigating here is the whole story.
  function openGreeting(gid: string) {
    navigate(sectionHref(scopeForPaths, { kind: "record", at: "greetings", rid: gid }));
  }

  // a search hit, or any other deep link, opens the record it names
  function openEntity(kind: RecordSection, id: string) {
    navigate(sectionHref(scopeForPaths, { kind: "record", at: kind, rid: id }));
  }

  // A `<kind>:<id>` chip — a lore owner, or the target of a ref-valued entity
  // field (#222) — jumps to the record it names. Split on the FIRST colon:
  // `paths.safe_id` rejects a colon in an id, so the head is always the kind.
  function openOwner(ref: string) {
    const i = ref.indexOf(":");
    const kind = ref.slice(0, i);
    const id = ref.slice(i + 1);
    if (kind === "characters") openCharacter(id, ""); // "" -> the page opens the default version
    else if (kind === "pcs") openEntity("pcs", id);
    // The entity kinds land on the record itself, not merely its section: a
    // ref names one record, and dropping the reader in a list to find it again
    // would be answering the question with the index.
    else if (ENTITY_KINDS.includes(kind as EntityKind)) openEntity(kind as RecordSection, id);
  }

  /** Where a row, a tile or a palette item points. The scope this page is
   *  *about* — a campaign's fork addresses under its own base. */
  const hrefFor = (at: Section) =>
    sectionHref(scopeForPaths, { kind: "section", at });

  /** What this page contributes to ⌘K: its own index, so a section can be
   *  reached by name from anywhere in the world rather than only by finding
   *  its row. */
  const paletteSource = useCallback((): PaletteItem[] => {
    const out: PaletteItem[] = [];
    if (!campaign) {
      out.push({ id: "world-section:overview", group: "IN THIS WORLD", label: "Overview",
                 meta: `${name} · setup`, run: () => navigate(hrefFor("overview")) });
      out.push({ id: "world-section:push", group: "IN THIS WORLD", label: "Push to campaigns",
                 meta: `${name} · pending changes`, run: () => navigate(hrefFor("push")) });
      out.push({ id: "world-section:images", group: "IN THIS WORLD", label: "Images",
                 meta: `${name} · art`, run: () => navigate(hrefFor("images")) });
    }
    for (const g of groups) {
      for (const r of g.rows) {
        out.push({ id: `world-section:${r.key}`, group: "IN THIS WORLD", label: r.label,
                   meta: `${name} · ${g.group.toLowerCase()}`, run: () => navigate(hrefFor(r.key)) });
      }
    }
    return out;
    // `hrefFor` is redeclared every render; `scopeForPaths` -- the thing it
    // closes over -- is rebuilt from these same primitives, so depending on
    // them is depending on it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign, cid, widParam, groups, name, navigate]);
  usePaletteSource(paletteSource);

  // FIRST: an old `?section=X&id=Y` link, translated once and replaced.
  //
  // Ahead of everything below, and that ordering is the point rather than an
  // accident of where it was written. `/campaigns/:cid/world?section=lore&id=x`
  // satisfies the campaign root's default redirect too, and firing that one
  // would throw the destination away and land on Characters.
  //
  // Only at the legacy roots. A URL that already names a record by path is
  // addressing it, and a stray `?section=` riding along does not outrank the
  // path -- obeying it would send a reader somewhere they did not ask to go.
  const legacy = tail === "" ? legacyTarget(params, shape) : null;
  if (legacy) {
    return <Navigate replace to={sectionHref(scopeForPaths, legacy)} />;
  }

  if (!parsed.ok) {
    return <Navigate replace
                     to={sectionHref(scopeForPaths,
                                     { kind: "section", at: defaultSection(shape) })} />;
  }
  if (campaign && tail === "") {
    return <Navigate replace
                     to={sectionHref(scopeForPaths, { kind: "section", at: "characters" })} />;
  }
  // `/worlds/w/items/` means Items and so does `/worlds/w/it%65ms` -- but
  // neither is how `sectionHref` spells it, and "exactly one address per
  // screen" is the rule this page is being rebuilt around. So the canonical
  // string is BUILT and compared, rather than the tail being inspected for the
  // particular ways it might be odd: a trailing slash, a doubled one, an
  // over-escaped segment and an id whose own escaping differs all come out the
  // same way, and no list of cases has to be kept complete.
  //
  // Loop-safe by construction: the destination's pathname IS `canonicalPath`,
  // so the next render's comparison succeeds. The query is carried through
  // verbatim -- `?owner=` and `?v=` are the screen's modifiers and dropping
  // them here would be a redirect that loses what the link asked for.
  const canonicalPath = rid
    ? sectionHref(scopeForPaths, { kind: "record", at: section as RecordSection, rid })
    : sectionHref(scopeForPaths, { kind: "section", at: section });
  if (location.pathname !== canonicalPath) {
    return <Navigate replace to={canonicalPath + location.search} />;
  }

  /** Which rendering of the greetings the route asks for.
   *
   *  Independent of whether a record is also named: the graph draws every
   *  greeting regardless of `rid`, and a record open in the (hidden) chip
   *  list when the reader switches to the graph -- a saved one being viewed,
   *  or a half-written draft -- has to stay open across the switch, so the
   *  chip's own navigation keeps the record segment and only adds or drops
   *  `?view=graph`. Derived rather than held, or the chips and the Back
   *  button disagree within three clicks -- push ?view=graph, click List,
   *  press Back, and a graph URL would have to render the list. */
  const greetingView: "list" | "graph" =
    params.get("view") === "graph" ? "graph" : "list";

  if (campaign && !wid) return null;

  const rows = groups.flatMap((g) => g.rows);
  const groupOf = (key: Section) =>
    groups.find((g) => g.rows.some((r) => r.key === key))?.group ?? "World";
  const labelOf = (key: Section) => {
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
        <Link className="column-back" to={`/campaigns/${cid}`}>
          ‹ {campaignName} / World Copy
        </Link>
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
        <Link className={"column-row" + (section === "overview" ? " active" : "")}
              aria-current={section === "overview" ? "page" : undefined}
              to={hrefFor("overview")}>
          <span className="column-row-label">Overview</span>
          <span className="column-row-count" aria-hidden>→</span>
        </Link>
      )}

      {/* World shape only: a campaign's fork of a world feeds nothing, and its
          own pending changes are reviewed in the campaign, not here. */}
      {!campaign && (
        <Link className={"column-row" + (section === "push" ? " active" : "")}
              aria-current={section === "push" ? "page" : undefined}
              to={hrefFor("push")}>
          <span className="column-row-label">Push to campaigns</span>
          <span className="column-row-count">{dash(campaignCount)}</span>
        </Link>
      )}

      {/* World shape only, for the same reason the greeting tagger it carries
          is: the subjects sidecar is written world-side, and a campaign's fork
          browses its own diverged art in the editor that owns it. No count —
          "how many pictures" is not a number anyone navigates by, and the two
          reads behind it are the ones this view exists to make once. */}
      {!campaign && (
        <Link className={"column-row" + (section === "images" ? " active" : "")}
              aria-current={section === "images" ? "page" : undefined}
              to={hrefFor("images")}>
          <span className="column-row-label">Images</span>
        </Link>
      )}

      {groups.map((g) => (
        <ColumnSection key={g.group} label={g.group}>
          {g.rows.map((r) => (
            <Link key={r.key}
                  className={"column-row" + (section === r.key ? " active" : "")}
                  aria-current={section === r.key ? "page" : undefined}
                  to={hrefFor(r.key)}>
              <span className="column-row-label">{r.label}</span>
              <span className="column-row-count">{dash(counts[r.key])}</span>
            </Link>
          ))}
        </ColumnSection>
      ))}
    </>
  );

  const footer = campaign ? (
    // A fork's way back to what it forked from.
    <Link className="column-link" to={sectionHref({ kind: "world", id: wid }, { kind: "section", at: "overview" })}>
      The source world <span aria-hidden>→</span>
    </Link>
  ) : (
    <>
      <button className="column-link" onClick={() => { setImportOpen(true); navigate(hrefFor("lore")); }}>
        Import lorebook <span aria-hidden>→</span>
      </button>
      <button className="column-link"
              onClick={() => { setScenarioOpen(true); navigate(hrefFor("overview")); }}>
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
            <WorldOverview key={populated} wid={wid} hrefFor={(t) => hrefFor(t as Section)}
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
        {section === "characters" && <CharacterGrid scope={scope} wid={wid} reveal={reveal} module={moduleCtx} />}
        {section === "pcs" && <PCEditor scope={scope} wid={wid}
                                       selected={rid}
                                       recordHref={(r) => sectionHref(scopeForPaths,
                                                                      { kind: "record", at: "pcs", rid: r })}
                                       module={moduleCtx} />}
        {!campaign && section === "tags" && <TagEditor wid={wid} />}
        {section === "locations" && <EntityEditor wid={wid} scope={scope} kind="locations"
                                          selected={section === "locations" ? rid : null}
                                          sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "locations" })}
                                          recordHref={(r) => sectionHref(scopeForPaths,
                                                                         { kind: "record", at: "locations", rid: r })}
                                          onReclassified={openEntity} module={moduleCtx} />}
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
            <EntityEditor key={loreReset} wid={wid} scope={scope} kind="lore"
                          selected={section === "lore" ? rid : null}
                          newOwner={newOwner}
                          sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "lore" })}
                          recordHref={(r) => sectionHref(scopeForPaths,
                                                         { kind: "record", at: "lore", rid: r })}
                          onReclassified={openEntity} onOpenOwner={openOwner} module={moduleCtx} />
          </>
        )}
        {section === "items" && <EntityEditor wid={wid} scope={scope} kind="items"
                                          selected={section === "items" ? rid : null}
                                          sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "items" })}
                                          recordHref={(r) => sectionHref(scopeForPaths,
                                                                         { kind: "record", at: "items", rid: r })}
                                          onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "groups" && <EntityEditor wid={wid} scope={scope} kind="groups"
                                          selected={section === "groups" ? rid : null}
                                          sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "groups" })}
                                          recordHref={(r) => sectionHref(scopeForPaths,
                                                                         { kind: "record", at: "groups", rid: r })}
                                          onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "creatures" && <EntityEditor wid={wid} scope={scope} kind="creatures"
                                          selected={section === "creatures" ? rid : null}
                                          sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "creatures" })}
                                          recordHref={(r) => sectionHref(scopeForPaths,
                                                                         { kind: "record", at: "creatures", rid: r })}
                                          onReclassified={openEntity}
                                          onOpenOwner={openOwner} module={moduleCtx} />}
        {section === "greetings" && (
          <>
            <div className="chips section-views" role="group" aria-label="Greetings view">
              {([["list", "List"], ["graph", "Plot map"]] as const).map(([key, label]) => (
                <button key={key} className={"chip" + (greetingView === key ? " on" : "")}
                        aria-pressed={greetingView === key}
                        // The current address, not a freshly-built one: whatever record
                        // segment is already there (or none) survives the switch, and
                        // only the `?view=graph` query is added or dropped.
                        onClick={() => navigate(location.pathname
                          + (key === "graph" ? "?view=graph" : ""))}>{label}</button>
              ))}
            </div>
            {/* Hidden rather than unmounted: the editor holds a half-written
                greeting in component state, and switching to the graph used to
                take it with no Save, no Cancel and no warning. */}
            <div hidden={greetingView !== "list"}>
              <GreetingEditor scope={scope} wid={wid} onOpenCharacter={openCharacter}
                              onOpenLocation={(id) => openEntity("locations", id)}
                              selected={section === "greetings" ? rid : null}
                              sectionPath={sectionHref(scopeForPaths, { kind: "section", at: "greetings" })}
                              recordHref={(r) => sectionHref(scopeForPaths,
                                                             { kind: "record", at: "greetings", rid: r })}
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
