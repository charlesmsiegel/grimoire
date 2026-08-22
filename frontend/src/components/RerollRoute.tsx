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
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [endpointModels, setEndpointModels] = useState<Model[]>([]);

  useEffect(() => {
    let live = true;
    api.listConnections()
      .then((list) => { if (live) setConnections(list); })
      .catch(() => { if (live) setConnections([]); });
    api.getConfig()
      .then((c) => { if (live) setActive(c.active_connection); })
      .catch(() => { if (live) setActive(null); });
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
        <option value="" title={active ? `Default: ${active.name}` : undefined}>
          Default
        </option>
        {/* The active connection is not offered again below: "Default" already
            IS it, and for a call that runs immediately there is no difference
            between naming it and letting it be resolved. Two rows reading
            "OpenRouter" would only ask the reader to tell apart a distinction
            that does not exist. */}
        {connections.filter((c) => c.id !== active?.id).map((c) => (
          <option key={c.id} value={c.id}>{c.name}</option>
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
        onChange={(model) => onChange({ ...value, model })}
        models={models}
        error={kind === "openrouter" && orError}
      />
    </span>
  );
}
