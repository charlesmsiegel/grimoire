import { useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

/** One form visit owns its navigation, error and busy completion after a save.
 * Cancelling cannot undo the server's write; it retires the form waiting for it.
 * A fresh router entry is a fresh visit, even when it names the same form.
 * Forms whose query also carries presentation (a tab or plot-map view) can
 * retain a visit across query changes. Their form key must include any actual
 * record/version selection; an identical-address fresh entry still retires. */
export function useCreationRequest(form: string | null,
                                   { keepOnQueryChange = false }: { keepOnQueryChange?: boolean } = {}) {
  const location = useLocation();
  const visitRef = useRef({ at: location, visit: { form } });
  const previous = visitRef.current;
  if (previous.at.key !== location.key || previous.visit.form !== form) {
    const sameFormQuery = keepOnQueryChange && previous.visit.form === form
      && previous.at.pathname === location.pathname && previous.at.search !== location.search;
    visitRef.current = { at: location, visit: sameFormQuery ? previous.visit : { form } };
  }
  const visit = visitRef.current.visit;
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
    const current = () => live.current === visit && visitRef.current.visit === visit && ticket.current === mine;
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
    if (live.current !== visit || visitRef.current.visit !== visit) return;
    ticket.current++;
    busyRef.current = false;
    setBusy(false);
  }

  return { busy, begin, retire, visit };
}
