import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type RoutingBundle } from "../api/client";

/** What went wrong, in the words the server used when it had any. `unknown`
 *  rather than `any`: a rejected fetch is not always an `ApiError`, and typing
 *  it as one is how a "cannot read property of undefined" replaces the message
 *  the reader needed. */
function reason(err: unknown): string {
  return err instanceof ApiError ? err.detail : String(err);
}

/** Where an effective route came from, in the words the response picker uses
 *  for the same idea — one vocabulary for both cascades. */
function scopeLabel(scope: string | undefined): string {
  switch (scope) {
    case "campaign": return "this campaign";
    case "global": return "the global default";
    case "active": return "the active connection";
    default: return "an unknown scope";
  }
}

/** Which connection each kind of generation runs on (#142).
 *
 *  Two scopes, one component: `global` on the Configuration page, `campaign`
 *  in the scene inspector, where the campaign's own knobs already live. A row
 *  per route, each defaulting to "inherit" — so a reader who never opens this
 *  keeps the single active connection they already had. */
export function ModelRoutingPicker({ scope, cid }: { scope: "global" | "campaign"; cid?: string }) {
  const [bundle, setBundle] = useState<RoutingBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  // Nothing orders two responses. Changing two rows quickly issues two writes,
  // and if the first one's bundle lands second the page renders a state the
  // store has already moved past -- the second row snapping back to inherit
  // while the store holds the connection that was chosen. Every read and write
  // takes a ticket and only the newest may render.
  const ticket = useRef(0);

  const load = useCallback(() => {
    const n = (ticket.current += 1);
    const get = scope === "global" ? api.getGlobalRouting() : api.getCampaignRouting(cid!);
    return get.then((r) => {
      if (n !== ticket.current) return;
      // Cleared on success, not only set on failure: a banner that outlives the
      // condition it describes is a page telling you something is wrong while
      // showing you the state that proves it is not.
      setError(null);
      setBundle(r);
    }).catch((err: unknown) => {
      if (n !== ticket.current) return;
      setBundle(null);
      setError(reason(err));
    });
  }, [scope, cid]);

  useEffect(() => { void load(); }, [load]);

  async function choose(route: string, connectionId: string) {
    setError(null);
    setBusy(route);
    const n = (ticket.current += 1);
    try {
      // One route per write, not the whole map: two tabs editing different
      // routes must not clobber each other, and a partial map is what the
      // endpoint takes.
      const next = scope === "global"
        ? await api.setGlobalRouting({ [route]: connectionId })
        : await api.setCampaignRouting(cid!, { [route]: connectionId });
      if (n === ticket.current) setBundle(next);
    } catch (err: unknown) {
      if (n !== ticket.current) return;
      // The recovery read FIRST, the message second, and the order is the
      // whole point: the select is rendered from the bundle, so a failed write
      // would otherwise leave the row showing a choice the store never took --
      // and `load` clears the error on success, so saying why before re-reading
      // would wipe the refusal the reader needs to see.
      await load();
      setError(reason(err));
    } finally {
      setBusy("");
    }
  }

  if (!bundle) {
    return (
      <div className="model-routing">
        {error && <div className="banner">{error}</div>}
        {!error && <div className="field-hint">Loading…</div>}
      </div>
    );
  }

  const nameOf = (id: string) => bundle.connections.find((c) => c.id === id)?.name ?? id;
  const inherited = (route: string) => {
    // `effective` is "" when the cascade reached its base, so the base has to be
    // named from `active_connection_id` -- "inherit (the active connection)"
    // answers a different question than the one the row is asking, which is
    // WHICH model this job runs on.
    const eff = bundle.effective[route] || bundle.active_connection_id;
    const from = scopeLabel(bundle.provenance[route]?.scope);
    return eff ? `— inherit (${nameOf(eff)}, from ${from}) —` : "— inherit —";
  };

  return (
    <div className="model-routing">
      {error && <div className="banner">{error}</div>}
      {/* Only at campaign scope. The Configuration page introduces its own
          section in full, so a second copy of the same sentence renders
          directly under it; the scene inspector has no such copy and needs
          one line saying what this list overrides. */}
      {scope === "campaign" && (
        <div className="field-hint">
          Overrides for this campaign only. Anything left on inherit follows the
          global routing.
        </div>
      )}
      {bundle.catalog.map((route) => (
        // `htmlFor`/`id` rather than a wrapping label, matching the config
        // fields: a hint nested inside the label is read out as part of the
        // control's name, so a screen reader would announce the whole sentence
        // every time the select takes focus. `aria-describedby` says the same
        // thing in the place meant for it.
        <div key={route.key} className="model-routing-row">
          <label htmlFor={`route-${route.key}`}>{route.label}</label>
          <select
            id={`route-${route.key}`}
            aria-describedby={`route-${route.key}-hint`}
            value={bundle.routes[route.key] ?? ""}
            disabled={busy === route.key}
            onChange={(e) => void choose(route.key, e.target.value)}
          >
            <option value="">{inherited(route.key)}</option>
            {bundle.connections.map((c) => (
              <option key={c.id} value={c.id}>{c.model ? `${c.name} — ${c.model}` : c.name}</option>
            ))}
            {/* A scope can name a connection this list has not loaded, or one
                deleted since — show its id rather than falling back to the
                blank option, which would misreport the route as inherited. */}
            {bundle.routes[route.key]
              && !bundle.connections.some((c) => c.id === bundle.routes[route.key]) && (
              <option value={bundle.routes[route.key]}>{bundle.routes[route.key]}</option>
            )}
          </select>
          <p className="field-hint" id={`route-${route.key}-hint`}>{route.hint}</p>
        </div>
      ))}
    </div>
  );
}
