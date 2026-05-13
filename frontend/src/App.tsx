import { BrowserRouter, Route, Routes } from "react-router-dom";

import { CampaignView } from "./routes/CampaignView";
import { CampaignsView } from "./routes/CampaignsView";
import { HomeRedirect } from "./routes/HomeRedirect";
import { LibraryRoutes } from "./routes/library";
import { NotFound } from "./routes/NotFound";
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
              <Route index element={<HomeRedirect />} />
              <Route path="library/*" element={<LibraryRoutes />} />
              <Route path="campaigns" element={<CampaignsView />} />
              <Route path="campaigns/:campaignId/*" element={<CampaignView />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </StoreProvider>
    </ThemeProvider>
  );
}
