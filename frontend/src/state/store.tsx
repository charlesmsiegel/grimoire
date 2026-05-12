/**
 * Client-side state store provider.
 *
 * Backend modules own the source of truth (spec 14 §State management). The
 * client mirrors active-campaign + library state through a context-backed
 * store exposing two mutation helpers:
 *
 * - `optimisticMutate(action, commit)` applies the local change immediately
 *   and rolls back if the underlying promise rejects. Use for safe operations
 *   (edits, navigation).
 * - `pessimisticMutate(commit, onSuccess)` waits for the server before
 *   applying. Use for consequential operations (deletes, upgrades).
 */

import { useCallback, useMemo, useReducer, useRef, type ReactNode } from "react";

import {
  StoreContext,
  initialState,
  reducer,
  type Action,
  type StoreContextValue,
} from "./storeContext";

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;

  const optimisticMutate = useCallback(
    async <T,>(action: Action, commit: () => Promise<T>): Promise<T> => {
      const snapshot = stateRef.current;
      dispatch(action);
      try {
        return await commit();
      } catch (err) {
        dispatch({ type: "replace", next: snapshot });
        throw err;
      }
    },
    [],
  );

  const pessimisticMutate = useCallback(
    async <T,>(commit: () => Promise<T>, onSuccess: (result: T) => Action): Promise<T> => {
      const result = await commit();
      dispatch(onSuccess(result));
      return result;
    },
    [],
  );

  const value = useMemo<StoreContextValue>(
    () => ({ state, dispatch, optimisticMutate, pessimisticMutate }),
    [state, optimisticMutate, pessimisticMutate],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
