import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppSettings } from "./routes/AppSettings";
import { CampaignCreate } from "./routes/CampaignCreate";
import { CampaignSettings } from "./routes/CampaignSettings";
import { CampaignPlayRoute, CampaignView } from "./routes/CampaignView";
import { CampaignsView } from "./routes/CampaignsView";
import { Home } from "./routes/Home";
import { LibraryRoutes } from "./routes/library";
import { NotFound } from "./routes/NotFound";
import { CastView } from "./routes/campaign/CastView";
import { CompositionView } from "./routes/campaign/CompositionView";
import { ImagesView } from "./routes/campaign/ImagesView";
import { MechanicsView } from "./routes/campaign/MechanicsView";
import { TimelineView } from "./routes/campaign/TimelineView";
import { WorldView } from "./routes/campaign/WorldView";
import { AppShell } from "./shell/AppShell";
import { StoreProvider } from "./state/store";
import { ThemeProvider } from "./state/theme";

export function App() {
  return (
    <ThemeProvider>
      <StoreProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Home />} />
              <Route path="library/*" element={<LibraryRoutes />} />
              <Route path="campaigns" element={<CampaignsView />} />
              <Route path="campaigns/new" element={<CampaignCreate />} />
              <Route path="campaigns/:campaignId/settings" element={<CampaignSettings />} />
              <Route path="campaigns/:campaignId" element={<CampaignView />}>
                <Route index element={<CampaignPlayRoute />} />
                <Route path="cast" element={<CastView />} />
                <Route path="world" element={<WorldView />} />
                <Route path="timeline" element={<TimelineView />} />
                <Route path="mechanics" element={<MechanicsView />} />
                <Route path="composition" element={<CompositionView />} />
                <Route path="images" element={<ImagesView />} />
              </Route>
              <Route path="settings" element={<AppSettings />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </StoreProvider>
    </ThemeProvider>
  );
}
