import { useEffect, useState } from "react";

import { api } from "../api/client";
import { getModels, type Model } from "../api/models";
import type { ActiveConnection, LLMConnection, LLMConnectionKind } from "../api/types";
import ModelCombobox from "../routes/ModelCombobox";
import { CLAUDE_MODEL_OPTIONS } from "./ConnectionForm";

/** One reroll's route override (#77): which connection to send it to, and
 *  which model to drive that connection at. Both empty is the standing
 *  configuration, which is what every reroll did before this existed. */
export type RerollRoute = { connection_id: string; model: string };

export const NO_REROLL_ROUTE: RerollRoute = { connection_id: "", model: "" };

/** Pick the connection and model ONE reroll runs on.
 *
 *  Two controls rather than one, because neither expresses the other's case: a
 *  model id cannot reach a different provider (the credentials and base URL
 *  that makes possible live on a connection), and a connection cannot say "the
 *  same provider, its bigger model". See `RegenerateBody`, which is shaped the
 *  same way for the same reason.
 *
 *  Both fetches are the component's own rather than the play view's, and they
 *  run when the popover opens rather than when the campaign does: this mounts
 *  only while the reader is choosing, and the alternative is a model catalog
 *  downloaded on every campaign open for a control most turns never touch.
 *  `getModels` is memoized per page load, so opening the popover twice costs
 *  one download.
 */
export default function RerollRoutePicker({
  value, onChange,
}: {
  value: RerollRoute;
  onChange: (route: RerollRoute) => void;
}) {
  // Which connection is active is READ HERE, on mount, rather than handed down
  // from the play view. Codex review caught the prop going stale: the view held
  // it from a `[cid]`-keyed effect, so another tab repointing the active
  // connection left the header (which `App` refreshes on navigation) and this
  // picker disagreeing — Default named the old connection's model, the old one
  // was filtered out of the list, and the real active one was offered as a
  // redundant row. This mounts only when the popover opens, which is exactly
  // when the answer is needed, so there is no window in which it can be wrong.
  const [active, setActive] = useState<ActiveConnection | null>(null);
  // Whether the read above has SETTLED, which `active === null` cannot say on
  // its own: it is both "not asked yet" and "asked, nothing is active". The
  // model box below is refused until this is true, so a model typed before the
  // answer arrives cannot be stored with no route attached (Codex review). A
  // read that FAILS still settles — we then genuinely do not know the active
  // connection, and disabling the control forever over that would be worse
  // than letting an unpinned model through.
  const [activeSettled, setActiveSettled] = useState(false);
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [endpointModels, setEndpointModels] = useState<Model[]>([]);

  useEffect(() => {
    let live = true;
    api.listConnections()
      .then((list) => { if (live) setConnections(list); })
      .catch(() => { if (live) setConnections([]); });
    // `{ fresh: true }`, which Codex caught the first version of this missing:
    // `getConfig` answers from a module cache "keyed to nothing but this tab's
    // own writes", so relocating the read into this component fixed nothing at
    // all for the cross-tab repoint it was moved here to handle. The popover
    // opens rarely and this is the one moment the answer has to be current.
    api.getConfig({ fresh: true })
      .then((c) => { if (live) { setActive(c.active_connection); setActiveSettled(true); } })
      .catch(() => { if (live) { setActive(null); setActiveSettled(true); } });
    return () => { live = false; };
  }, []);

  // Which connection this reroll would actually reach: the one named, or the
  // active one when nothing is. `null` until the list lands, and also for a
  // choice the list does not contain — a connection deleted in another tab
  // since the popover opened. Nothing is silently reinterpreted as the
  // default: the state still names what was chosen, the model box offers
  // nothing rather than the wrong provider's catalog, and sending the reroll
  // gets the server's 400.
  const chosen =
    connections.find((c) => c.id === (value.connection_id || active?.id)) ?? null;
  const kind: LLMConnectionKind | null = chosen?.kind ?? null;
  const chosenId = chosen?.id ?? "";

  useEffect(() => {
    // Only for the kind whose models come from a catalog. An Ollama reroll must
    // not pull down OpenRouter's, which is the largest single download the app
    // makes.
    if (kind !== "openrouter") return;
    let live = true;
    getModels()
      .then((m) => { if (live) { setOrModels(m); setOrError(false); } })
      .catch(() => { if (live) { setOrModels([]); setOrError(true); } });
    return () => { live = false; };
  }, [kind]);

  useEffect(() => {
    // A custom endpoint's list is whatever its last refresh cached, per
    // connection — so this re-reads when the choice changes, and clears first
    // so a slow read cannot leave the previous endpoint's models on screen
    // under the new one's name.
    setEndpointModels([]);
    if (kind !== "openai_compatible" || !chosenId) return;
    let live = true;
    api.readConnection(chosenId)
      .then((d) => { if (live) setEndpointModels(d.models); })
      .catch(() => { if (live) setEndpointModels([]); });
    return () => { live = false; };
  }, [kind, chosenId]);

  const models =
    kind === "openrouter" ? orModels
    : kind === "claude" ? CLAUDE_MODEL_OPTIONS
    : kind === "openai_compatible" ? endpointModels
    : [];

  return (
    <span className="reroll-route">
      <select
        aria-label="Reroll connection"
        value={value.connection_id}
        onChange={(e) => {
          // The model is cleared with the connection, never carried across:
          // an OpenRouter id means nothing to a local endpoint, and a picker
          // that kept it would send a reroll to a model the chosen provider
          // has never heard of.
          onChange({ connection_id: e.target.value, model: "" });
        }}
      >
        {/* Just "Default", with the connection's name on hover. The name does
            not fit a control this size — a real one ran as "Default — Oper" —
            and it is the one thing here that is already said twice over: the
            status bar names the active connection, and the model box beside
            this shows the model leaving it alone would run. */}
        <option value=""
                title={active
                  ? `Whichever connection is active when the reroll is sent — ${active.name} now`
                  : undefined}>
          Default
        </option>
        {/* The active connection IS offered again below, which an earlier
            round removed as a duplicate and Codex caught as the removal of the
            only way to pin a provider. The distinction is real: "Default" is
            whichever connection is active when the reroll is sent, and the
            named row is that connection specifically. The titles say so, since
            the difference only shows itself when the two diverge. */}
        {connections.map((c) => (
          <option key={c.id} value={c.id}
                  title={c.id === active?.id
                    ? `${c.name} — pinned, even if the active connection changes`
                    : c.name}>
            {c.name}
          </option>
        ))}
      </select>
      <ModelCombobox
        ariaLabel="Reroll model"
        // What leaving it blank means, spelled out rather than implied: the
        // model the chosen route will actually run. Both sources now report
        // that directly — `/config`'s `active_connection.model` always did,
        // and `/llm-connections` gained `effective_model` for this — so the
        // rule that a `claude` connection with no model still runs one lives
        // in `llm.effective_model` and nowhere else.
        placeholder={(value.connection_id ? chosen?.effective_model
                                          : active?.model) || "model"}
        value={value.model}
        // Choosing a model under "Default" PINS the connection the box is
        // describing. Codex caught the gap: the placeholder and the catalog
        // both come from whichever connection is active right now, so a route
        // left dynamic would apply a model chosen against A to whatever B
        // happens to be active by the time Reroll is clicked.
        //
        // The pin STAYS once set, including when the model is cleared again:
        // an implicit pin and an explicit one are the same value and nothing
        // here distinguishes them, and the `<select>` shows the connection by
        // name from that moment on — so the route on screen is the route that
        // will run, and Default is one click away for a reader who wants it
        // dynamic again.
        onChange={(model) => onChange({
          connection_id: value.connection_id || (model ? active?.id ?? "" : ""),
          model,
        })}
        models={models}
        error={kind === "openrouter" && orError}
        // Only while BOTH are unknown. An explicitly named connection is its
        // own pin and needs nothing from the config read; it is the Default
        // row, whose meaning is "whichever is active", that has nothing to
        // attribute a typed model to until the answer lands.
        disabled={!value.connection_id && !activeSettled}
      />
    </span>
  );
}
