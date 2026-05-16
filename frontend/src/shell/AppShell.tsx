import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useMatch, useNavigate } from "react-router-dom";

import { SkipLink } from "../components/a11y";
import { useKeyboardShortcuts, type ShortcutBinding } from "../hooks/useKeyboardShortcuts";
import { useNavCollapsed } from "../hooks/useNavCollapsed";
import { useSetupStatus } from "../hooks/useSetupStatus";
import { StartupWizard } from "../routes/StartupWizard";
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
  const { collapsed, toggle } = useNavCollapsed();
  const setup = useSetupStatus();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  // Once the user dismisses, the auto-open effect must not re-fire when a
  // refetch produces a new (still-incomplete) status object. The manual
  // re-open path clears this so /settings → Run setup wizard still works.
  const dismissedRef = useRef(false);

  useEffect(() => {
    if (setup.loading) return;
    if (dismissedRef.current) return;
    if (setup.status && !setup.status.completed) setWizardOpen(true);
  }, [setup.loading, setup.status]);

  // Surface a global handler so AppSettings (mounted as a child route) can
  // re-trigger the wizard without prop drilling through the router.
  useEffect(() => {
    const handler = () => {
      dismissedRef.current = false;
      setManualOpen(true);
      setWizardOpen(true);
    };
    window.addEventListener("grimoire:open-startup-wizard", handler);
    return () => window.removeEventListener("grimoire:open-startup-wizard", handler);
  }, []);

  const closeWizard = useCallback(() => {
    dismissedRef.current = true;
    setWizardOpen(false);
    setManualOpen(false);
  }, []);

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
        key: "b",
        ctrlOrMeta: true,
        handler: (e) => {
          e.preventDefault();
          toggle();
        },
        description: "Toggle sidebar",
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
    [navigate, cycle, toggle],
  );

  useKeyboardShortcuts(shortcuts);

  return (
    <CampaignStreamProvider campaignId={campaignId}>
      <div className={collapsed ? "app-shell nav-collapsed" : "app-shell"}>
        <SkipLink targetId={MAIN_ID} />
        <NavSidebar collapsed={collapsed} onToggle={toggle} />
        <main id={MAIN_ID} className="app-main" tabIndex={-1}>
          <Outlet />
        </main>
        <StatusBarBridge />
        {wizardOpen && (
          <StartupWizard
            onClose={closeWizard}
            title={manualOpen ? "Setup wizard" : "Welcome to Grimoire"}
          />
        )}
      </div>
    </CampaignStreamProvider>
  );
}

function StatusBarBridge() {
  const status = useCampaignStreamStatus();
  return <StatusBar wsStatus={status} />;
}
