import type { ChatEvent } from "../../api/stream";
import type { RerollRoute } from "../../components/RerollRoute";

/** Immutable identities captured before asynchronous campaign work begins. */
export type SceneKey = Readonly<{ cid: string; sid: string }>;
export type WindowKey = SceneKey & Readonly<{ token: number }>;
export type Current = () => boolean;
export type DirectorNote = SceneKey & { text: string };
export type RunHandle = SceneKey & { attempt: string; id: string | null };
export type Observation = { signal: AbortSignal; token: number };

export type TurnRequest = {
  scope: SceneKey;
  start: (onEvent: (e: ChatEvent) => void, signal: AbortSignal,
    attempt: string, onIndex: (index: number) => void) => Promise<void>;
  recoverText?: (() => void) | undefined;
  durableText: string;
  ephemeral: boolean;
  rerolling: boolean;
  note: DirectorNote | null;
  route: RerollRoute;
};
