import { useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

/** One form visit owns its navigation, error and busy completion after a save.
 * Cancelling cannot undo the server's write; it retires the form waiting for it.
 * A fresh router entry is a fresh visit, even when it names the same form. */
export function useCreationRequest(form: string | null) {
  const location = useLocation();
  const visitRef = useRef({ route: location.key, form });
  if (visitRef.current.route !== location.key || visitRef.current.form !== form) {
    visitRef.current = { route: location.key, form };
  }
  const visit = visitRef.current;
  const live = useRef<typeof visit | null>(null);
  const ticket = useRef(0);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);

  // Layout cleanup closes the commit-to-microtask gap. Setup also runs again
  // under StrictMode, so its cleanup cannot leave the current visit retired.
  useLayoutEffect(() => {
    const generation = ticket;
    live.current = visit;
    busyRef.current = false;
    setBusy(false);
    return () => { live.current = null; generation.current++; };
  }, [visit]);

  function begin() {
    if (form === null || busyRef.current) return null;
    const mine = ++ticket.current;
    busyRef.current = true;
    setBusy(true);
    const current = () => live.current === visit && visitRef.current === visit && ticket.current === mine;
    return {
      current,
      finish: () => {
        if (current()) { busyRef.current = false; setBusy(false); }
      },
    };
  }

  function retire() {
    // An existing-record Cancel can fail to reload and leave the same form
    // open. Retire its old save now, without releasing a later form's latch.
    if (live.current !== visit || visitRef.current !== visit) return;
    ticket.current++;
    busyRef.current = false;
    setBusy(false);
  }

  return { busy, begin, retire };
}
