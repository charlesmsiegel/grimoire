import { useEffect } from "react";
import type { RunRegistry } from "../../runs/RunRegistryProvider";

export function useComposerRecovery({ restoreParked, cid, activeId, parkedTick, input, directing, restoreDurableOnce, registry }: {
  restoreParked: () => void;
  cid: string;
  activeId: string | null;
  parkedTick: number;
  input: string;
  directing: boolean;
  restoreDurableOnce: () => void;
  registry: RunRegistry;
}) {

  // Hand back anything parked for the scene now on screen. Runs on selection
  // as well as on a fresh park, so a prompt recovered while the player was
  // elsewhere arrives the moment they come back rather than on the next
  // failure.
  useEffect(restoreParked, [cid, activeId, parkedTick, input, directing, restoreParked]);
  useEffect(restoreDurableOnce, [cid, activeId, registry, restoreDurableOnce]);
}
