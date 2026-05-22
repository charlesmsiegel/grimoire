/**
 * Per-campaign routing model + helpers shared by `CampaignSettings`.
 *
 * Kept in its own module so non-component exports don't break React's
 * Fast Refresh contract on the settings file.
 */

export interface RoutingValue {
  llm: Record<string, string>;
  embedding: Record<string, string>;
  imagegen: Record<string, string>;
}

/** Strip incomplete `"provider."` entries (a provider picked, no model
 * yet) before PUTting routing to the server. The trailing-dot encoding
 * is fine for local dropdown state but the backend's
 * ``Route.parse("provider.")`` raises ``ValueError`` → 422. Treat such
 * entries as absent so partial selections don't crash the auto-save.
 */
export function cleanRoutes(value: RoutingValue): RoutingValue {
  const strip = (block: Record<string, string>): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [task, raw] of Object.entries(block)) {
      if (raw && !raw.endsWith(".")) out[task] = raw;
    }
    return out;
  };
  return {
    llm: strip(value.llm ?? {}),
    embedding: strip(value.embedding ?? {}),
    imagegen: strip(value.imagegen ?? {}),
  };
}
