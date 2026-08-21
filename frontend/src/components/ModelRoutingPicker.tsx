import { useCallback, useEffect, useState } from "react";
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

  const load = useCallback(() => {
    const get = scope === "global" ? api.getGlobalRouting() : api.getCampaignRouting(cid!);
    return get.then(setBundle).catch((err: unknown) => {
      setBundle(null);
      setError(reason(err));
    });
  }, [scope, cid]);

  useEffect(() => { void load(); }, [load]);

  async function choose(route: string, connectionId: string) {
    setError(null);
    setBusy(route);
    try {
      // One route per write, not the whole map: two tabs editing different
      // routes must not clobber each other, and a partial map is what the
      // endpoint takes.
      const next = scope === "global"
        ? await api.setGlobalRouting({ [route]: connectionId })
        : await api.setCampaignRouting(cid!, { [route]: connectionId });
      setBundle(next);
    } catch (err: unknown) {
      setError(reason(err));
      // The select is rendered from the bundle, so a failed write would
      // otherwise leave the row showing a choice the store never took.
      await load();
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
    const eff = bundle.effective[route] ?? "";
    const from = scopeLabel(bundle.provenance[route]?.scope);
    return eff ? `— inherit (${nameOf(eff)}, from ${from}) —` : "— inherit (the active connection) —";
  };

  return (
    <div className="model-routing">
      {error && <div className="banner">{error}</div>}
      <div className="field-hint">
        {scope === "global"
          ? "Each kind of generation can run on its own connection. Anything left on inherit uses the active connection."
          : "Overrides for this campaign only. Anything left on inherit follows the global routing."}
      </div>
      {bundle.catalog.map((route) => (
        <label key={route.key} className="model-routing-row">
          {route.label}
          <select
            aria-label={route.label}
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
          <span className="field-hint">{route.hint}</span>
        </label>
      ))}
    </div>
  );
}
