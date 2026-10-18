import { useTheme } from "./ThemeProvider";

/** The one owner of "which look is stored", for every control that offers it.
 *
 *  Configuration's pinned picker and the header toggle are mounted together,
 *  and before this hook they could disagree with each other and with disk.
 *
 *  The bug that forced it: Configuration used to carry `theme` inside its
 *  deferred draft and write it with everything else on Save. Open Configuration
 *  (its draft holds the old theme), change the look from the header, then save
 *  an unrelated setting — and the stale draft writes the old theme back over
 *  the one you just chose. Making the header toggle client-only instead is
 *  worse in a quieter way: the choice survives until the next reload and then
 *  vanishes, which reads as the app forgetting.
 *
 *  So the look is not a draft field anywhere. These controls persist on pick,
 *  through here. The existing theme provider owns the request as well as the
 *  preview, so controls mounted together share busy, error and rollback state.
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
  const { mode, busy, error, pick } = useTheme();
  return { mode, busy, error, pick };
}
