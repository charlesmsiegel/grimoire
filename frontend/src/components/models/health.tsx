import { useEffect, useState } from "react";
import { api, type ProviderHealth } from "../../api/client";
import { onConfigChanged } from "../../appEvents";

/** Each provider's health, by id, from `GET /api/llm-connections` -- the one
 *  place it is served (the app's health registry; spec 1). Read again on any
 *  model-settings change. A failed read is an empty map: a provider with no
 *  health shows no dot, never a guessed one. */
export function useProviderHealth(): ReadonlyMap<string, ProviderHealth> {
  const [map, setMap] = useState<ReadonlyMap<string, ProviderHealth>>(() => new Map());
  const [asked, setAsked] = useState(0);
  useEffect(() => onConfigChanged(() => setAsked((n) => n + 1)), []);
  useEffect(() => {
    let live = true;
    api.listConnections()
      .then((list) => { if (live) setMap(new Map(list.map((c) => [c.id, c.health]))); })
      .catch(() => { if (live) setMap(new Map()); });
    return () => { live = false; };
  }, [asked]);
  return map;
}

/** A health state in a word, as an `<option>` can carry it (no colour there). */
export function healthWord(h: ProviderHealth | undefined): string {
  if (!h) return "";
  if (h.state === "ok") return "working";
  if (h.state === "error") return "failing";
  return "not checked";
}

/** The dot, and its word for a reader who cannot see colour. Nothing for a
 *  provider whose health is not known at all. */
export function HealthDot({ health }: { health: ProviderHealth | undefined }) {
  if (!health) return null;
  const cls = health.state === "ok" ? "ok" : health.state === "error" ? "bad" : "off";
  return (
    <>
      <span className={"conn-dot " + cls} aria-hidden> ●</span>
      <span className="sr-only"> {healthWord(health)}</span>
    </>
  );
}
