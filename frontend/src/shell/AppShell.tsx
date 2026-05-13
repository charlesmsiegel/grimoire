import { useEffect, useMemo } from "react";
import { Outlet, useMatch, useNavigate } from "react-router-dom";

import { SkipLink } from "../components/a11y";
import { useKeyboardShortcuts, type ShortcutBinding } from "../hooks/useKeyboardShortcuts";
import { CampaignStreamProvider } from "../state/campaignStream";
import { useCampaignStreamStatus } from "../state/useCampaignEvent";
import { useStore } from "../state/useStore";
import { useTheme } from "../state/useTheme";
import { NavSidebar } from "./NavSidebar";
import { StatusBar } from "./StatusBar";

const MAIN_ID = "main-content";

export function AppShell() {
  const navigate = useNavigate();
  const { cycle } = useTheme();
  const { state, dispatch } = useStore();

  const campaignMatch = useMatch("/campaigns/:campaignId/*");
  const campaignId = campaignMatch?.params.campaignId ?? null;

  useEffect(() => {
    if (campaignId !== state.activeCampaignId) {
      dispatch({ type: "set-active-campaign", id: campaignId });
    }
  }, [campaignId, state.activeCampaignId, dispatch]);

  const shortcuts = useMemo<ShortcutBinding[]>(
    () => [
      {
        key: "l",
        ctrlOrMeta: true,
        handler: (e) => {
          e.preventDefault();
          navigate("/library");
        },
        description: "Go to Library",
      },
      {
        key: "k",
        ctrlOrMeta: true,
        handler: (e) => {
          e.preventDefault();
          navigate("/campaigns");
        },
        description: "Go to Campaigns",
      },
      {
        key: "t",
        ctrlOrMeta: false,
        handler: (e) => {
          e.preventDefault();
          cycle();
        },
        description: "Cycle theme",
      },
    ],
    [navigate, cycle],
  );

  useKeyboardShortcuts(shortcuts);

  return (
    <CampaignStreamProvider campaignId={campaignId}>
      <div className="app-shell">
        <SkipLink targetId={MAIN_ID} />
        <NavSidebar />
        <main id={MAIN_ID} className="app-main" tabIndex={-1}>
          <Outlet />
        </main>
        <StatusBarBridge />
      </div>
    </CampaignStreamProvider>
  );
}

function StatusBarBridge() {
  const status = useCampaignStreamStatus();
  return <StatusBar wsStatus={status} />;
}
