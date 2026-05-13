import { Navigate, Route, Routes } from "react-router-dom";

import { EntityEditorView } from "./EntityEditorView";
import { EntityListView } from "./EntityListView";
import { ImagePresetsView } from "./ImagePresetsView";
import { LibraryLayout } from "./LibraryLayout";
import { MechanicsView } from "./MechanicsView";
import { PluginsView } from "./PluginsView";
import { SettingDependentsView } from "./SettingDependentsView";
import { SettingDetailView } from "./SettingDetailView";
import { SettingMetaView } from "./SettingMetaView";
import { SettingsListView } from "./SettingsListView";
import { StyleGuidesView } from "./StyleGuidesView";

export function LibraryRoutes() {
  return (
    <Routes>
      <Route element={<LibraryLayout />}>
        <Route index element={<Navigate to="settings" replace />} />

        <Route path="settings" element={<SettingsListView />} />
        <Route path="settings/:settingId" element={<SettingDetailView />}>
          <Route index element={<Navigate to="characters" replace />} />
          <Route path="meta" element={<SettingMetaView />} />
          <Route path="dependents" element={<SettingDependentsView />} />
          <Route path=":kind" element={<EntityListView />} />
        </Route>
        <Route path="settings/:settingId/:kind/:entityId/*" element={<EntityEditorView />} />

        <Route path="style-guides/*" element={<StyleGuidesView />} />
        <Route path="image-presets/*" element={<ImagePresetsView />} />
        <Route path="mechanics/*" element={<MechanicsView />} />
        <Route path="plugins/*" element={<PluginsView />} />
      </Route>
    </Routes>
  );
}
