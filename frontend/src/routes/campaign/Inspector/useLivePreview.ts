/**
 * Debounced live-preview hook for the Context Inspector panel.
 *
 * Mirrors the user-typed draft input through a 500ms debounce into a
 * POST /preview call, abandoning in-flight previews if the user keeps
 * typing. Exposes the most recent preview handle + summary plus a
 * `loading` flag for UI affordances.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { inspectorApi, type PreviewSummary } from "../../../api/inspector";

interface Args {
  campaignId: string;
  playerInput: string;
  sessionId: string;
  pcRef?: string;
  debounceMs?: number;
  enabled?: boolean;
}

interface State {
  handle: string | null;
  summary: PreviewSummary | null;
  loading: boolean;
  error: string | null;
}

export function useLivePreview({
  campaignId,
  playerInput,
  sessionId,
  pcRef,
  debounceMs = 500,
  enabled = true,
}: Args): State & { refresh: () => void } {
  const [state, setState] = useState<State>({
    handle: null,
    summary: null,
    loading: false,
    error: null,
  });

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const counterRef = useRef(0);

  const fire = useCallback(() => {
    if (!enabled) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const myId = ++counterRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    inspectorApi
      .preview(campaignId, { playerInput, sessionId, pcRef }, controller.signal)
      .then((res) => {
        if (myId !== counterRef.current) return;
        setState({
          handle: res.handle,
          summary: res.summary,
          loading: false,
          error: null,
        });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (myId !== counterRef.current) return;
        const msg = err instanceof Error ? err.message : String(err);
        setState((s) => ({ ...s, loading: false, error: msg }));
      });
  }, [campaignId, playerInput, sessionId, pcRef, enabled]);

  useEffect(() => {
    if (!enabled) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(fire, debounceMs);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [fire, debounceMs, enabled]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return { ...state, refresh: fire };
}
