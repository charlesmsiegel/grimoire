import { useCallback, useState } from "react";
import { ApiError, api } from "../api/client";
import { useTheme } from "./ThemeProvider";
import { normalizeMode } from "./themes";

/** The one owner of "which look is stored", for every control that offers it.
 *
 *  There are three such controls now — Configuration's pinned picker, the
 *  first-run wizard's, and the header toggle — and before this hook they could
 *  disagree with each other and with disk.
 *
 *  The bug that forced it: Configuration used to carry `theme` inside its
 *  deferred draft and write it with everything else on Save. Open Configuration
 *  (its draft holds the old theme), change the look from the header, then save
 *  an unrelated setting — and the stale draft writes the old theme back over
 *  the one you just chose. Making the header toggle client-only instead is
 *  worse in a quieter way: the choice survives until the next reload and then
 *  vanishes, which reads as the app forgetting.
 *
 *  So the look is not a draft field anywhere. Every control persists on pick,
 *  through here.
 *
 *  Applied before it is stored, and rolled back if the store refuses: a look
 *  you cannot see until you commit it is a control you cannot use, and a look
 *  left applied after a failed write looks chosen and is gone at reload.
 *  Configuration's own save already made exactly this trade for the same
 *  reason; this hook is where it now lives for all three callers.
 */
export function useThemeSetting(): {
  mode: string;
  busy: boolean;
  error: string | null;
  pick: (mode: string) => Promise<void>;
} {
  const { mode, setTheme } = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pick = useCallback(async (next: string) => {
    const before = mode;
    setTheme(next);          // apply first: this is the preview
    setBusy(true);
    setError(null);
    try {
      const stored = await api.putConfig({ theme: next });
      // Reconcile with what was actually stored rather than assuming: if the
      // server normalized or refused the value, the screen must stop showing a
      // look nothing on disk agrees with.
      setTheme(normalizeMode(stored.theme));
    } catch (e) {
      setTheme(before);
      // `ApiError` carries the server's own words; anything else is a transport
      // failure with nothing worth quoting.
      setError(e instanceof ApiError && e.detail ? e.detail : "Could not save the theme");
    } finally {
      setBusy(false);
    }
  }, [mode, setTheme]);

  return { mode, busy, error, pick };
}
