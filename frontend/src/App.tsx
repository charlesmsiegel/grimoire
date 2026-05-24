import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom";

import { AppSettings } from "./routes/appsettings";
import { CampaignCreate } from "./routes/CampaignCreate";
import { CampaignSettings } from "./routes/campaign/settings";
import { CampaignPlayRoute, CampaignView } from "./routes/CampaignView";
import { CampaignsView } from "./routes/CampaignsView";
import { Home } from "./routes/Home";
import { LibraryRoutes } from "./routes/library";
import { NotFound } from "./routes/NotFound";
import { HealthPanel, ObservabilityRoutes } from "./routes/observability";
import { BudgetView } from "./routes/campaign/BudgetView";
import { CastView } from "./routes/campaign/CastView";
import { CompositionView } from "./routes/campaign/CompositionView";
import { ImagesView } from "./routes/campaign/ImagesView";
import { LedgerRoute } from "./routes/campaign/LedgerView";
import { MechanicsView } from "./routes/campaign/MechanicsView";
import { PromptDebugView } from "./routes/campaign/PromptDebugView";
import { TimelineView } from "./routes/campaign/TimelineView";
import { WorldView } from "./routes/campaign/WorldView";
import { WhyCharacterPanel } from "./routes/observability";
import { AppShell } from "./shell/AppShell";
import { markEnd, markStart } from "./state/perf";
import { StoreProvider } from "./state/store";
import { ThemeProvider } from "./state/theme";

// Spec 14 §Performance budgets: initial load < 2s.
// Mark as soon as this module evaluates so the span captures bundle parse +
// React mount, not just the first render commit.
markStart("app:initial-load");

export function App() {
  useEffect(() => {
    // Fires once after the first commit; this is the earliest moment the user
    // sees any UI, which is what the budget targets.
    markEnd("app:initial-load");
  }, []);

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
              <Route path="campaigns/:campaignId" element={<CampaignView />}>
                <Route index element={<CampaignPlayRoute />} />
                <Route path="cast" element={<CastView />} />
                <Route path="world" element={<WorldView />} />
                <Route path="timeline" element={<TimelineView />} />
                <Route path="ledger" element={<LedgerRoute />} />
                <Route path="mechanics" element={<MechanicsView />} />
                <Route path="composition" element={<CompositionView />} />
                <Route path="images" element={<ImagesView />} />
                <Route path="debug/prompt" element={<PromptDebugView />} />
                <Route path="debug/prompt/:turnId" element={<PromptDebugView />} />
                <Route path="observability/turns" element={<ObservabilityTurnsRoute />} />
                <Route path="budget" element={<BudgetView />} />
                <Route path="settings" element={<CampaignSettings />} />
              </Route>
              <Route path="observability/*" element={<ObservabilityRoutes />} />
              <Route path="settings" element={<AppSettings />} />
              <Route path="health" element={<HealthPanel />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </StoreProvider>
    </ThemeProvider>
  );
}

function ObservabilityTurnsRoute() {
  const { campaignId } = useParams<{ campaignId: string }>();
  if (!campaignId) return null;
  return <WhyCharacterPanel campaignId={campaignId} />;
}
