import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type EntityScope } from "../api/client";
import { CharacterEditor } from "../components/CharacterEditor";
import { PCEditor } from "../components/PCEditor";
import { TagEditor } from "../components/TagEditor";
import { EntityEditor } from "../components/EntityEditor";
import { GreetingEditor } from "../components/GreetingEditor";
import { LorebookImport } from "../components/LorebookImport";

const TABS = [
  { key: "characters", label: "Characters" },
  { key: "pcs", label: "PCs" },
  { key: "tags", label: "Tags" },
  { key: "locations", label: "Locations" },
  { key: "lore", label: "Lore" },
  { key: "greetings", label: "Greetings" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function WorldView({ campaign = false }: { campaign?: boolean }) {
  const { wid: widParam = "", cid = "" } = useParams();
  const navigate = useNavigate();
  const [wid, setWid] = useState(campaign ? "" : widParam);
  const [campaignName, setCampaignName] = useState("");
  const [name, setName] = useState("");
  const [tab, setTab] = useState<TabKey>("characters");
  const [charReset, setCharReset] = useState(0);
  const [loreReset, setLoreReset] = useState(0);
  const [focusChar, setFocusChar] = useState<{ cid: string; vid: string } | null>(null);
  const [focusGreeting, setFocusGreeting] = useState<string | null>(null);
  const [loreNav, setLoreNav] = useState<{ focusEntry?: string; newOwner?: string } | null>(null);

  useEffect(() => {
    if (campaign) {
      api.getCampaign(cid).then((c) => {
        setCampaignName(c.meta.name);
        setWid(c.meta.world);
        api.getWorld(c.meta.world)
          .then((w) => setName(w.meta.name))
          .catch(() => setName(c.meta.world));
      });
    } else {
      setWid(widParam);
      api.getWorld(widParam).then((w) => setName(w.meta.name)).catch(() => setName(widParam));
    }
  }, [campaign, cid, widParam]);

  const scope: EntityScope = campaign ? { kind: "campaign", id: cid } : { kind: "world", id: wid };

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
            ⌦ Campaign copy — this world was forked when the campaign began.
            Changes here belong to the campaign; the original world is untouched.
          </div>
        </>
      ) : (
        <Link to="/worlds" className="back-link">‹ All Worlds</Link>
      )}
      <h1 className="page-h1">{name}</h1>

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={"tab" + (tab === t.key ? " active" : "")}
            onClick={() => { setTab(t.key); if (t.key === "characters") { setCharReset((n) => n + 1); setFocusChar(null); } if (t.key === "greetings") setFocusGreeting(null); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "characters" && <CharacterEditor wid={wid} resetSignal={charReset} focus={focusChar} onOpenLore={openLore} onOpenGreeting={openGreeting} />}
      {tab === "pcs" && <PCEditor wid={wid} onOpenLore={openLore} />}
      {tab === "tags" && <TagEditor wid={wid} />}
      {tab === "locations" && <EntityEditor wid={wid} scope={scope} kind="locations" onOpenLore={openLore} />}
      {tab === "lore" && (
        <>
          <details className="import-section">
            <summary>Import lorebook / world-info</summary>
            <LorebookImport wid={wid} onImported={() => setLoreReset((n) => n + 1)} />
          </details>
          <EntityEditor key={loreReset} wid={wid} scope={scope} kind="lore" nav={loreNav}
                        onNavConsumed={() => setLoreNav(null)} onOpenOwner={openOwner} />
        </>
      )}
      {tab === "greetings" && <GreetingEditor wid={wid} onOpenCharacter={openCharacter} focus={focusGreeting} />}
    </div>
  );
}
