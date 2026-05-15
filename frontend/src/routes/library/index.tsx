import { Navigate, Route, Routes } from "react-router-dom";

import { EntityEditorView } from "./EntityEditorView";
import { EntityListView } from "./EntityListView";
import { ImagePresetsView } from "./ImagePresetsView";
import { LibraryLayout } from "./LibraryLayout";
import { MechanicsView } from "./MechanicsView";
import { PluginsView } from "./PluginsView";
import { WorldDependentsView } from "./WorldDependentsView";
import { WorldDetailView } from "./WorldDetailView";
import { WorldMetaView } from "./WorldMetaView";
import { WorldsListView } from "./WorldsListView";
import { StyleGuidesView } from "./StyleGuidesView";

export function LibraryRoutes() {
  return (
    <Routes>
      <Route element={<LibraryLayout />}>
        <Route index element={<Navigate to="worlds" replace />} />

        <Route path="worlds" element={<WorldsListView />} />
        <Route path="worlds/:worldId" element={<WorldDetailView />}>
          <Route index element={<Navigate to="characters" replace />} />
          <Route path="meta" element={<WorldMetaView />} />
          <Route path="dependents" element={<WorldDependentsView />} />
          <Route path=":kind" element={<EntityListView />} />
        </Route>
        <Route path="worlds/:worldId/:kind/:entityId/*" element={<EntityEditorView />} />

        <Route path="style-guides/*" element={<StyleGuidesView />} />
        <Route path="image-presets/*" element={<ImagePresetsView />} />
        <Route path="mechanics/*" element={<MechanicsView />} />
        <Route path="plugins/*" element={<PluginsView />} />
      </Route>
    </Routes>
  );
}
