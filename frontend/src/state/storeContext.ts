import { createContext } from "react";

export interface CampaignSummary {
  id: string;
  name: string;
  active_pc?: string | null;
}

export interface DriftAlert {
  character_ref: string;
  score: number;
}

export interface ReviewQueueItem {
  id: string;
  summary: string;
}

export type ImageJobStatus = "queued" | "running" | "complete" | "failed";

export interface ImageJobEntry {
  job_id: string;
  status: ImageJobStatus;
  created_at: number;
  prompt_preview: string;
  scene_id?: string | null;
  reason?: string | null;
}

export interface StatusInfo {
  modelLabel: string | null;
  tokenBudget: { used: number; total: number } | null;
  queueDepth: number;
  driftAlerts: DriftAlert[];
}

export interface AppState {
  campaigns: CampaignSummary[];
  activeCampaignId: string | null;
  reviewQueue: ReviewQueueItem[];
  status: StatusInfo;
  imageJobs: Record<string, ImageJobEntry>;
}

export type Action =
  | { type: "set-campaigns"; campaigns: CampaignSummary[] }
  | { type: "set-active-campaign"; id: string | null }
  | { type: "patch-status"; status: Partial<StatusInfo> }
  | { type: "push-review"; item: ReviewQueueItem }
  | { type: "remove-review"; id: string }
  | { type: "drift-alert"; alert: DriftAlert }
  | { type: "clear-drift"; characterRef: string }
  | { type: "image-job-upsert"; job: ImageJobEntry }
  | { type: "image-job-remove"; jobId: string }
  | { type: "replace"; next: AppState };

export interface StoreContextValue {
  state: AppState;
  dispatch: (action: Action) => void;
  optimisticMutate: <T>(action: Action, commit: () => Promise<T>) => Promise<T>;
  pessimisticMutate: <T>(commit: () => Promise<T>, onSuccess: (result: T) => Action) => Promise<T>;
}

export const StoreContext = createContext<StoreContextValue | null>(null);

export const initialState: AppState = {
  campaigns: [],
  activeCampaignId: null,
  reviewQueue: [],
  status: { modelLabel: null, tokenBudget: null, queueDepth: 0, driftAlerts: [] },
  imageJobs: {},
};

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "set-campaigns":
      return { ...state, campaigns: action.campaigns };
    case "set-active-campaign":
      return { ...state, activeCampaignId: action.id };
    case "patch-status":
      return { ...state, status: { ...state.status, ...action.status } };
    case "push-review":
      return { ...state, reviewQueue: [...state.reviewQueue, action.item] };
    case "remove-review":
      return {
        ...state,
        reviewQueue: state.reviewQueue.filter((r) => r.id !== action.id),
      };
    case "drift-alert":
      return {
        ...state,
        status: {
          ...state.status,
          driftAlerts: [
            ...state.status.driftAlerts.filter(
              (a) => a.character_ref !== action.alert.character_ref,
            ),
            action.alert,
          ],
        },
      };
    case "clear-drift":
      return {
        ...state,
        status: {
          ...state.status,
          driftAlerts: state.status.driftAlerts.filter(
            (a) => a.character_ref !== action.characterRef,
          ),
        },
      };
    case "image-job-upsert":
      return {
        ...state,
        imageJobs: { ...state.imageJobs, [action.job.job_id]: action.job },
      };
    case "image-job-remove": {
      if (!(action.jobId in state.imageJobs)) return state;
      const next = { ...state.imageJobs };
      delete next[action.jobId];
      return { ...state, imageJobs: next };
    }
    case "replace":
      return action.next;
  }
}
