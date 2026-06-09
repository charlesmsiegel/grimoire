import { useState } from "react";

import { AppearanceTab } from "./AppearanceTab";
import { BackupTab } from "./BackupTab";
import { LibraryTab } from "./LibraryTab";
import { MechanicsTab } from "./MechanicsTab";
import { PluginsTab } from "./PluginsTab";
import { ProvidersTab } from "./ProvidersTab";
import { TemplatesTab } from "./TemplatesTab";
import { Tabs } from "../../components/Tabs";

type Tab =
  | "library"
  | "providers"
  | "templates"
  | "mechanics"
  | "plugins"
  | "backup"
  | "appearance";

const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "providers", label: "Providers" },
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
      <Tabs
        tabs={TABS.map((t) => ({ key: t.id, label: t.label }))}
        active={tab}
        onSelect={setTab}
        ariaLabel="App settings tabs"
        className="tab-bar"
      />

      <div className="tab-panel">
        {tab === "library" && <LibraryTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "templates" && <TemplatesTab />}
        {tab === "mechanics" && <MechanicsTab />}
        {tab === "plugins" && <PluginsTab />}
        {tab === "backup" && <BackupTab />}
        {tab === "appearance" && <AppearanceTab />}
      </div>
    </section>
  );
}
