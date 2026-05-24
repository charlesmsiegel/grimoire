import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../../api/client";

export interface AppConfig {
  library_path: string;
  backup: { schedule: string; retention_days: number; location: string };
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function useAppConfig(): {
  data: AppConfig | null;
  patch: (next: Partial<AppConfig>) => void;
  status: "idle" | "loading" | "saving" | "saved" | "error";
  error: string | null;
} {
  const [data, setData] = useState<AppConfig | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "saving" | "saved" | "error">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<Partial<AppConfig> | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await api.get<AppConfig>("/api/config/app");
        if (!cancelled) {
          setData(result);
          setStatus("idle");
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
          setStatus("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const patch = useCallback((next: Partial<AppConfig>) => {
    setData((prev) => (prev ? { ...prev, ...next } : prev));
    pending.current = { ...(pending.current ?? {}), ...next };
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const body = pending.current;
      pending.current = null;
      timer.current = null;
      if (!body) return;
      setStatus("saving");
      setError(null);
      void (async () => {
        try {
          const result = await api.patch<AppConfig>("/api/config/app", body);
          setData(result);
          setStatus("saved");
        } catch (err) {
          setError(errorMessage(err));
          setStatus("error");
        }
      })();
    }, 500);
  }, []);

  return { data, patch, status, error };
}
