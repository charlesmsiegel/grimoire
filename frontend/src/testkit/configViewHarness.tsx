import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type { Config } from "../api/types";
import ConfigView from "../routes/ConfigView";
import { ThemeProvider } from "../theme/ThemeProvider";
import { fixture } from "./fixture";

export const cfg = fixture<Config>({
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
  data_dir: "/home/u/.grimoire",
  llm_timeout: "120", absorb_budget: "600", llm_call_budget: "300",
  llm_retries: "2",
  context_budget: "0", context_scan_depth: "6", lore_recursion_depth: "0", archive_depth: "3",
  prompt_log_depth: "50", offscene_known_limit: "40",
  rolling_summary_every: "10",
  scene_break_every: "20", replay_fork_threshold: "10",
  advance_fork_threshold: "30",
  semantic_recall_depth: "0",
  semantic_recall_threshold: "0.4",
  history_recall_depth: "0", history_recall_budget: "1200",
  history_recall_threshold: "0.35", history_recall_rerank: "off",
  history_plan: "off", history_plan_repair: "on",
  prompt_layout_enabled: "off", speaker_turn_taking: "off",
  backup_enabled: "off", backup_interval_hours: "24", backup_keep: "7", backup_dir: "",
  // The roles as `GET /config` names them (`settings.summary`). Decision
  // selects nothing of its own here, so the summary reports what it falls
  // through to -- Fast's choice -- and null only when nothing selects one.
  inference: {
    roles: {
      primary: { provider_name: "OpenRouter", model: "vendor/model-x", preset_name: "Warm" },
      fast: { provider_name: "Local vectors", model: "small-1", preset_name: "" },
      decision: { provider_name: "Local vectors", model: "small-1", preset_name: "" },
    },
    embedding_on: false,
  },
});
export const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
export function installConfigMocks() {
  vi.clearAllMocks();
  vi.mocked(api.getConfig).mockResolvedValue(cfg);
  vi.mocked(api.putConfig).mockResolvedValue(cfg);
  vi.mocked(api.getDataDir).mockResolvedValue(dataDir);
  vi.mocked(api.putDataDir).mockResolvedValue(dataDir);
  vi.mocked(api.getStoreConflicts).mockResolvedValue({ conflicts: [], truncated: false });
  vi.mocked(api.listStyles).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
  vi.mocked(api.listBackups).mockResolvedValue({
    dir: "/home/u/.grimoire/backups", backups: [], image_backups: [],
  });
  // The context bar's source: no campaigns unless a test says otherwise, which
  // is also the "nothing to draw" case.
  vi.mocked(api.listCampaigns).mockResolvedValue([]);
  vi.mocked(api.listScenes).mockResolvedValue([]);
  vi.mocked(api.listScenePrompts).mockResolvedValue({ entries: [] });
}

// ConfigView renders a <Link to="/connections">, which throws outside a
// Router context — wrap every render the same way CampaignsView.test.tsx does.
export function renderView() {
  render(
    <MemoryRouter>
      <ThemeProvider initial={cfg.theme}><ConfigView /></ThemeProvider>
    </MemoryRouter>,
  );
}

/** Main shows one section at a time, so every field test opens its section
 *  first. The row's accessible name can carry a trailing state word ("unsaved",
 *  "off", "ready"), hence the anchored patterns. */
export async function open(name: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name }));
}

export const save = () => fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

