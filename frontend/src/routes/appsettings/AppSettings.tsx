import { useState } from "react";

import { AppearanceTab } from "./AppearanceTab";
import { BackupTab } from "./BackupTab";
import { LLMDefaultsTab } from "./LLMDefaultsTab";
import { LibraryTab } from "./LibraryTab";
import { MechanicsTab } from "./MechanicsTab";
import { PluginsTab } from "./PluginsTab";
import { ProvidersTab } from "./ProvidersTab";
import { TemplatesTab } from "./TemplatesTab";

type Tab =
  | "library"
  | "providers"
  | "llm-defaults"
  | "templates"
  | "mechanics"
  | "plugins"
  | "backup"
  | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "providers", label: "Providers" },
  { id: "llm-defaults", label: "LLM defaults" },
  { id: "templates", label: "Prompts" },
  { id: "mechanics", label: "Mechanics" },
  { id: "plugins", label: "Plugins" },
  { id: "backup", label: "Backup" },
  { id: "appearance", label: "Appearance" },
];

export function AppSettings() {
  const [tab, setTab] = useState<Tab>("library");
  return (
    <section className="route app-settings" aria-labelledby="app-settings-heading">
      <header>
        <h2 id="app-settings-heading">Settings</h2>
      </header>
      <nav className="tab-bar" aria-label="App settings tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="tab-panel">
        {tab === "library" && <LibraryTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "llm-defaults" && <LLMDefaultsTab />}
        {tab === "templates" && <TemplatesTab />}
        {tab === "mechanics" && <MechanicsTab />}
        {tab === "plugins" && <PluginsTab />}
        {tab === "backup" && <BackupTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </section>
  );
}
