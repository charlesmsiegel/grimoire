import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../../api/client";

export interface CampaignRecord {
  id: string;
  name?: string;
  description?: string | null;
  mechanics_module?: string | null;
  style_guide_id?: string | null;
  image_preset_id?: string | null;
  inline_style_guide?: string | null;
  content_boundaries?: string | null;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export function useAutoSavedResource<T>(
  campaignId: string | undefined,
  path: string,
  initial: T,
  transformForSave?: (value: T) => T,
): {
  value: T;
  setValue: (next: T | ((prev: T) => T)) => void;
  status: SaveStatus;
  error: string | null;
  ready: boolean;
} {
  const [value, setValueState] = useState<T>(initial);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(false);
  const lastSent = useRef<string>("");

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    setReady(false);
    setError(null);
    void (async () => {
      try {
        const data = await api.get<T>(`/api/campaigns/${encodeURIComponent(campaignId)}${path}`);
        if (!cancelled) {
          setValueState(data);
          const seed = transformForSave ? transformForSave(data) : data;
          lastSent.current = JSON.stringify(seed);
          setReady(true);
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, path, transformForSave]);

  useEffect(() => {
    if (!campaignId || !ready) return;
    if (!dirty.current) return;
    const payload = transformForSave ? transformForSave(value) : value;
    const serialized = JSON.stringify(payload);
    if (serialized === lastSent.current) return;
    const handle = window.setTimeout(() => {
      void (async () => {
        setStatus("saving");
        setError(null);
        try {
          await api.put(`/api/campaigns/${encodeURIComponent(campaignId)}${path}`, payload);
          lastSent.current = serialized;
          setStatus("saved");
        } catch (err) {
          setError(errorMessage(err));
          setStatus("error");
        }
      })();
    }, 400);
    return () => window.clearTimeout(handle);
  }, [campaignId, path, value, ready, transformForSave]);

  const setValue = useCallback((next: T | ((prev: T) => T)) => {
    dirty.current = true;
    setValueState(next);
  }, []);

  return { value, setValue, status, error, ready };
}
