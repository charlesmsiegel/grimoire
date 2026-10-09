import { useEffect, useState } from "react";
import { api, type PricingEntry } from "../../api/client";
import { perMillionRate } from "../cost";
import { PricingEditor } from "../PricingEditor";

type Read = { rates: Record<string, PricingEntry>; unreadable: boolean } | null;

/** The fallback rate table on the Models page (spec 3.5): read-only, with an
 *  explicit Edit that mounts the existing editor, which saves itself.
 *  `addModel` (from Set rate) opens straight into the editor on that model. */
export function TokenRates({ addModel, onSaved }: { addModel?: string; onSaved: () => void }) {
  const [mode, setMode] = useState<"view" | "edit">(addModel ? "edit" : "view");
  const [read, setRead] = useState<Read>(null);
  const [asked, setAsked] = useState(0);

  useEffect(() => { if (addModel) setMode("edit"); }, [addModel]);
  useEffect(() => {
    let live = true;
    api.getPricing()
      .then((t) => { if (live) setRead({ rates: t.rates, unreadable: !!t.unreadable }); })
      .catch(() => { if (live) setRead({ rates: {}, unreadable: true }); });
    return () => { live = false; };
  }, [asked]);

  const rows = read ? Object.entries(read.rates)
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b))) : [];

  return (
    <section id="rates" className="token-rates" aria-labelledby="rates-title">
      <h3 id="rates-title">Token rates</h3>
      <p className="config-copy">
        Your fallback table, for models whose provider states no price. A model's own
        rates, set on its provider's page, come first.
      </p>
      <p className="config-copy">
        Grimoire records what each provider says a call cost. OpenRouter
        says; an OpenAI-compatible endpoint you host yourself says nothing
        at all, and those calls read as <em>not reported</em> everywhere costs
        are shown — which is honest, and no use for answering what a
        campaign has cost. Rates here fill that gap.
      </p>
      <p className="config-copy">
        What comes out of them is an <strong>estimate, and is labelled as
        one</strong>: a modelled figure is reported in its own column, is
        never added to what a provider actually charged, and is never
        charged against a campaign's budget. A model with no entry of its
        own falls back to a <code>provider/*</code> wildcard, then to the
        catch-all. Rates are dollars per 1,000 tokens; the per-million
        figure most price sheets quote is shown under each box.
      </p>
      <p className="config-copy">
        Leaving the two cache boxes empty is not the same as setting them
        to zero: cached tokens are part of the prompt the provider counted,
        so an empty box prices them at the input rate. Fill them in only
        for a provider that discounts them.
      </p>
      {mode === "edit" ? (
        <PricingEditor addModel={addModel}
                       onSaved={() => { setMode("view"); setAsked((n) => n + 1); onSaved(); }} />
      ) : read === null ? (
        <p className="field-hint">Reading rates…</p>
      ) : read.unreadable ? (
        <p className="field-hint error">
          Could not read the rate table. Nothing is shown and nothing can be
          saved from here — an empty form saved over rates that failed to load
          would delete them.
        </p>
      ) : (
        <>
          {rows.length === 0 ? (
            <p className="field-hint">No rates set.</p>
          ) : (
            <table className="rates-table">
              <thead><tr><th scope="col">Model</th><th scope="col">Input</th><th scope="col">Output</th></tr></thead>
              <tbody>
                {rows.map(([id, e]) => (
                  <tr key={id}>
                    <td>{id === "" ? "Every other model" : id}</td>
                    <td>{e.prompt_usd_per_1k === undefined ? "—" : perMillionRate(e.prompt_usd_per_1k)}</td>
                    <td>{e.completion_usd_per_1k === undefined ? "—" : perMillionRate(e.completion_usd_per_1k)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="form-actions">
            <button className="subtle" onClick={() => setMode("edit")}>Edit rates</button>
          </div>
        </>
      )}
    </section>
  );
}
