/**
 * First-run setup status. Backed by a sentinel file at
 * ``{data_root}/.setup-complete`` — so completion persists per-machine,
 * not per-browser.
 */

import { api } from "./client";

export interface SetupStatus {
  completed: boolean;
  data_root: string;
}

export const setupApi = {
  status: () => api.get<SetupStatus>("/api/setup/status"),
  setCompleted: (completed: boolean) =>
    api.post<SetupStatus>("/api/setup/status", { completed }),
};
