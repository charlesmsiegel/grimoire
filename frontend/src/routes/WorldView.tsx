import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
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

export default function WorldView() {
  const { wid = "" } = useParams();
  const [name, setName] = useState("");
  const [tab, setTab] = useState<TabKey>("characters");
  const [charReset, setCharReset] = useState(0);
  const [loreReset, setLoreReset] = useState(0);
  const [focusChar, setFocusChar] = useState<{ cid: string; vid: string } | null>(null);

  useEffect(() => {
    api.getWorld(wid).then((w) => setName(w.meta.name)).catch(() => setName(wid));
  }, [wid]);

  // a present-character link from the greeting view jumps to that character
  function openCharacter(cid: string, vid: string) {
    setFocusChar({ cid, vid });
    setTab("characters");
  }

  return (
    <div className="view" style={{ maxWidth: 920 }}>
      <Link to="/worlds" className="back-link">‹ Worlds</Link>
      <h2>{name}</h2>

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={"tab" + (tab === t.key ? " active" : "")}
            onClick={() => { setTab(t.key); if (t.key === "characters") { setCharReset((n) => n + 1); setFocusChar(null); } }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "characters" && <CharacterEditor wid={wid} resetSignal={charReset} focus={focusChar} />}
      {tab === "pcs" && <PCEditor wid={wid} />}
      {tab === "tags" && <TagEditor wid={wid} />}
      {tab === "locations" && <EntityEditor wid={wid} kind="locations" />}
      {tab === "lore" && (
        <>
          <details className="import-section">
            <summary>Import lorebook / world-info</summary>
            <LorebookImport wid={wid} onImported={() => setLoreReset((n) => n + 1)} />
          </details>
          <EntityEditor key={loreReset} wid={wid} kind="lore" />
        </>
      )}
      {tab === "greetings" && <GreetingEditor wid={wid} onOpenCharacter={openCharacter} />}
    </div>
  );
}
