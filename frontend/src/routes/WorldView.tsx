import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type EntityScope, type ModuleDetail } from "../api/client";
import { CharacterEditor } from "../components/CharacterEditor";
import { PCEditor } from "../components/PCEditor";
import { TagEditor } from "../components/TagEditor";
import { EntityEditor } from "../components/EntityEditor";
import { GreetingEditor } from "../components/GreetingEditor";
import { LorebookImport } from "../components/LorebookImport";
import { WorldOverview } from "../components/WorldOverview";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "characters", label: "Characters" },
  { key: "pcs", label: "PCs" },
  { key: "tags", label: "Tags" },
  { key: "locations", label: "Locations" },
  { key: "lore", label: "Lore" },
  { key: "items", label: "Items" },
  { key: "groups", label: "Groups" },
  { key: "creatures", label: "Creatures" },
  { key: "greetings", label: "Greetings" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function WorldView({ campaign = false }: { campaign?: boolean }) {
  const { wid: widParam = "", cid = "" } = useParams();
  const navigate = useNavigate();
  const [wid, setWid] = useState(campaign ? "" : widParam);
  const [campaignName, setCampaignName] = useState("");
  const [name, setName] = useState("");
  const [tab, setTab] = useState<TabKey>(campaign ? "characters" : "overview");
  const [charReset, setCharReset] = useState(0);
  const [loreReset, setLoreReset] = useState(0);
  const [focusChar, setFocusChar] = useState<{ cid: string; vid: string } | null>(null);
  const [focusGreeting, setFocusGreeting] = useState<string | null>(null);
  const [loreNav, setLoreNav] = useState<{ focusEntry?: string; newOwner?: string } | null>(null);
  const [moduleCtx, setModuleCtx] = useState<ModuleDetail | null>(null);
  const [worldMid, setWorldMid] = useState("");

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
      api.getWorld(widParam).then((w) => setName(w.meta.name)).catch(() => setName(widParam));
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
  // tag vocabulary is a world concern; campaign PC tags are free strings
  const tabs = campaign ? TABS.filter((t) => t.key !== "tags" && t.key !== "overview") : TABS;

  // a present-character link from the greeting view jumps to that character
  function openCharacter(cid: string, vid: string) {
    setFocusChar({ cid, vid });
    setTab("characters");
  }

  // a world-greeting link from a character page jumps to that greeting
  function openGreeting(gid: string) {
    setFocusGreeting(gid);
    setTab("greetings");
  }

  // an owner editor's lore panel routes to the Lore tab (open an entry, or start a pre-owned one)
  function openLore(nav: { focusEntry?: string; newOwner?: string }) {
    setLoreNav({ ...nav });
    setTab("lore");
  }

  // an owner chip inside the Lore tab jumps to that record's tab
  function openOwner(ref: string) {
    const i = ref.indexOf(":");
    const kind = ref.slice(0, i);
    const id = ref.slice(i + 1);
    if (kind === "characters") openCharacter(id, ""); // "" -> CharacterEditor falls back to default version
    else if (kind === "pcs") setTab("pcs");
    else if (kind === "locations") setTab("locations");
  }

  if (campaign && !wid) return null;

  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      {campaign ? (
        <>
          <button className="back-link" onClick={() => navigate(`/campaigns/${cid}`)}>
            ‹ {campaignName} / World Copy
          </button>
          <div className="fork-banner">
            ⌦ Campaign view — records follow the world until you change them here;
            edits belong to this campaign and leave the original world untouched.
          </div>
        </>
      ) : (
        <Link to="/worlds" className="back-link">‹ All Worlds</Link>
      )}
      <h1 className="page-h1">{name}</h1>

      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={"tab" + (tab === t.key ? " active" : "")}
            onClick={() => { setTab(t.key); if (t.key === "characters") { setCharReset((n) => n + 1); setFocusChar(null); } if (t.key === "greetings") setFocusGreeting(null); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!campaign && tab === "overview" && <WorldOverview wid={wid} onNavigate={(t) => setTab(t as TabKey)} />}
      {tab === "characters" && <CharacterEditor scope={scope} wid={wid} resetSignal={charReset} focus={focusChar} onOpenLore={openLore} onOpenGreeting={openGreeting} module={moduleCtx} />}
      {tab === "pcs" && <PCEditor scope={scope} wid={wid} onOpenLore={openLore} module={moduleCtx} />}
      {!campaign && tab === "tags" && <TagEditor wid={wid} />}
      {tab === "locations" && <EntityEditor wid={wid} scope={scope} kind="locations" onOpenLore={openLore} module={moduleCtx} />}
      {tab === "lore" && (
        <>
          {!campaign && <details className="import-section">
            <summary>Import lorebook / world-info</summary>
            <LorebookImport wid={wid} onImported={() => setLoreReset((n) => n + 1)} />
          </details>}
          <EntityEditor key={loreReset} wid={wid} scope={scope} kind="lore" nav={loreNav}
                        onNavConsumed={() => setLoreNav(null)} onOpenOwner={openOwner} module={moduleCtx} />
        </>
      )}
      {tab === "items" && <EntityEditor wid={wid} scope={scope} kind="items" module={moduleCtx} />}
      {tab === "groups" && <EntityEditor wid={wid} scope={scope} kind="groups" module={moduleCtx} />}
      {tab === "creatures" && <EntityEditor wid={wid} scope={scope} kind="creatures" module={moduleCtx} />}
      {tab === "greetings" && <GreetingEditor scope={scope} wid={wid} onOpenCharacter={openCharacter} focus={focusGreeting} />}
    </div>
  );
}
