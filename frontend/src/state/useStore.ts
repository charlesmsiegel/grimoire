import { useContext } from "react";

import { StoreContext, type AppState, type StoreContextValue } from "./storeContext";

export function useStore(): StoreContextValue {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used inside StoreProvider");
  return ctx;
}

export function useAppState(): AppState {
  return useStore().state;
}
