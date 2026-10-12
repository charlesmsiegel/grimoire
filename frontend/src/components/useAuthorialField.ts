import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PromptField } from "../api/types";
import { errorText } from "../api/errors";

/** One explicit edit session over a scope-owned field. Callers key their body
 *  by scope, so neither an old draft nor its async completion crosses navigation.
 *  GET refreshes may update the stored view, never an active edit. PUT's canonical
 *  GET-shaped response is authoritative; it also retires client reads at the
 *  existing request boundary. No second write or new cache is needed. */
export function useAuthorialField<T extends PromptField>(read: () => Promise<T>,
  write: (text: string) => Promise<T>, onSaved?: () => void) {
  const [data, setData] = useState<T | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ticket = useRef(0);
  const live = useRef(false);
  const editingNow = useRef(false);
  const savingNow = useRef(false);
  const saved = useRef(onSaved);
  saved.current = onSaved;

  // Synchronous cleanup closes the commit -> microtask gap, as the existing
  // confirmation write sequence does. The ticket also rejects StrictMode's
  // first mount read after its cleanup/setup cycle.
  useLayoutEffect(() => {
    const generation = ticket;
    live.current = true;
    return () => { live.current = false; generation.current++; };
  }, []);

  const reload = useCallback(() => {
    if (savingNow.current) return;
    const mine = ++ticket.current;
    setLoading(true); setError(null);
    read().then((value) => {
      if (!live.current || mine !== ticket.current) return;
      setData(value);
      if (!editingNow.current) setDraft(value.text);
    }).catch((err: unknown) => {
      if (!live.current || mine !== ticket.current) return;
      setData(null); setError(errorText(err));
    }).finally(() => {
      if (live.current && mine === ticket.current) setLoading(false);
    });
  }, [read]);
  useEffect(() => { reload(); }, [reload]);

  const readable = data !== null && data.status !== "unreadable";
  function edit() {
    if (!readable || loading || savingNow.current) return;
    editingNow.current = true; setEditing(true); setDraft(data.text); setError(null);
  }
  function cancel() {
    if (savingNow.current) return;
    editingNow.current = false; setEditing(false); setDraft(data?.text ?? ""); setError(null);
  }
  async function save() {
    if (!readable || !editingNow.current || loading || savingNow.current) return;
    const mine = ++ticket.current;
    savingNow.current = true; setSaving(true); setError(null);
    try {
      const value = await write(draft);
      if (!live.current || mine !== ticket.current) return;
      setData(value); setDraft(value.text); editingNow.current = false; setEditing(false);
      saved.current?.();
    } catch (err: unknown) {
      if (live.current && mine === ticket.current) setError(errorText(err));
    } finally {
      if (live.current && mine === ticket.current) { savingNow.current = false; setSaving(false); }
    }
  }
  return { data, draft, setDraft, editing, loading, saving, error, readable,
    reload, edit, cancel, save };
}
