import type { Climate, ClimateEntry, ClimateSeason } from "../api/client";

/** The climate editor's pure half: the blank record, the display helpers, and
 *  every edit to the form as a function from one draft to the next. */

export const BLANK_SEASON: ClimateSeason = {
  name: "all year", from: 0, to: 0,
  temperature: [{ name: "mild", weight: 1 }],
  conditions: [{ name: "clear", weight: 1 }],
  wind: [{ name: "calm", weight: 1 }],
};
export const BLANK: Climate = { id: "", name: "", persistence: 0.5, seasons: [BLANK_SEASON] };

export const AXES = [
  { key: "temperature" as const, label: "Temperature" },
  { key: "conditions" as const, label: "Conditions" },
  { key: "wind" as const, label: "Wind" },
];
export type ClimateAxis = typeof AXES[number];
export type ClimateAxisKey = ClimateAxis["key"];

/** Percent of the year, for display. Seasons are stored as fractions so a
 *  preset drops into a calendar of any length; showing raw 0.92 helps nobody. */
export function pct(fraction: number) {
  return `${Math.round(fraction * 1000) / 10}%`;
}

/** An entry's share of its table, as a percentage.
 *
 *  Weights are relative, so `×1` and `×4` alone do not tell an author they
 *  configured 20% and 80% — the number they actually care about. Zero-weight
 *  rows are excluded from the total, matching how the draw skips them. */
export function share(entries: ClimateEntry[], entry: ClimateEntry) {
  const total = entries.reduce((sum, e) => sum + (e.weight > 0 ? e.weight : 0), 0);
  if (!total || entry.weight <= 0) return null;
  return `${Math.round((entry.weight / total) * 1000) / 10}%`;
}

/** The conditions a given temperature band can actually draw. */
function eligible(conditions: ClimateEntry[], temp: string) {
  return conditions.filter((c) => !c.requires_temp || c.requires_temp.includes(temp));
}

/** Each drawable temperature band with the shares its eligible conditions
 *  draw at, as one line: "cold — clear 50%, snow 50%; hot — clear 100%". */
export function perBandShares(s: ClimateSeason): string {
  return s.temperature.filter((tp) => tp.weight > 0).map((tp) => (
    `${tp.name} — ` + eligible(s.conditions, tp.name)
      .filter((c) => c.weight > 0)
      .map((c) => `${c.name} ${share(eligible(s.conditions, tp.name), c)}`)
      .join(", ")
  )).join("; ");
}

/** What deleting or reverting a climate changes, appended to the confirm
 *  question: the campaigns that default to it and the locations naming it. */
export function referrerNotice(
  refs: { campaigns?: { name: string }[]; locations?: { name: string }[] }, reverting: boolean,
): string {
  const campaigns = refs.campaigns ?? [];
  const locations = refs.locations ?? [];
  let message = "";
  if (campaigns.length) {
    message += `\n\nIt is the default climate for: ${campaigns.map((c) => c.name).join(", ")}.`
      + " Every location there that doesn't name its own climate"
      + (reverting ? " changes with it." : " falls back.");
  }
  if (locations.length) {
    message += `\n\n${locations.length} location(s) name it directly: `
      + `${locations.slice(0, 5).map((l) => l.name).join(", ")}`
      + `${locations.length > 5 ? ", …" : ""}.`;
  }
  if (!campaigns.length && !locations.length) message += " Nothing is using it.";
  return message;
}

export function patchSeason(form: Climate, n: number, patch: Partial<ClimateSeason>): Climate {
  return { ...form, seasons: form.seasons.map((s, i) => (i === n ? { ...s, ...patch } : s)) };
}

export function patchEntry(form: Climate, n: number, axis: ClimateAxisKey, e: number,
                           patch: Partial<ClimateEntry>): Climate {
  return patchSeason(form, n, {
    [axis]: form.seasons[n][axis].map((x, i) => {
      if (i !== e) return x;
      const next = { ...x, ...patch };
      // The validator rejects an empty requires_temp and wants the key
      // omitted for an unconstrained condition, so clearing the field has to
      // delete the property — otherwise Save fails and the constraint can
      // never be removed.
      if (Array.isArray(next.requires_temp) && next.requires_temp.length === 0) {
        delete next.requires_temp;
      }
      return next;
    }),
  });
}

/** Rename a temperature and rewrite every condition that requires it.
 *
 *  One state update, not two. Renaming and patching separately would each
 *  read the same captured `form`, so the second would overwrite the first
 *  and the rename would appear to do nothing.
 *
 *  Without the rewrite a plain rename leaves `requires_temp` pointing at a
 *  name that no longer exists, the next save is rejected as a dangling
 *  requirement, and the author has to hunt down each dependent condition. */
export function renameTemperature(form: Climate, n: number, e: number, from: string, to: string): Climate {
  return {
    ...form,
    seasons: form.seasons.map((s, i) => (i !== n ? s : {
      ...s,
      temperature: s.temperature.map((x, j) => (j === e ? { ...x, name: to } : x)),
      conditions: s.conditions.map((c) => (
        c.requires_temp?.includes(from)
          ? { ...c, requires_temp: c.requires_temp.map((r) => (r === from ? to : r)) }
          : c)),
    })),
  };
}

export function addEntry(form: Climate, n: number, axis: ClimateAxisKey): Climate {
  return patchSeason(form, n, { [axis]: [...form.seasons[n][axis], { name: "", weight: 1 }] });
}

export function removeEntry(form: Climate, n: number, axis: ClimateAxisKey, e: number): Climate {
  const season = form.seasons[n];
  if (axis !== "temperature") {
    return patchSeason(form, n, { [axis]: season[axis].filter((_, i) => i !== e) });
  }
  // Removing a temperature drops it from every condition that required it,
  // in the same update. Left behind, the reference is a dangling requirement
  // the backend rejects, so the remove button could not complete a normal
  // deletion without the author hunting down each dependent condition. A
  // condition left requiring nothing becomes unconstrained — the validator
  // wants the key absent rather than an empty array.
  const gone = season.temperature[e]?.name;
  return {
    ...form,
    seasons: form.seasons.map((s, i) => (i !== n ? s : {
      ...s,
      temperature: s.temperature.filter((_, j) => j !== e),
      conditions: s.conditions.map((c) => {
        if (!c.requires_temp?.includes(gone)) return c;
        const kept = c.requires_temp.filter((r) => r !== gone);
        const next = { ...c };
        if (kept.length) next.requires_temp = kept;
        else delete next.requires_temp;
        return next;
      }),
    })),
  };
}

export function addSeason(form: Climate): Climate {
  return { ...form, seasons: [...form.seasons, { ...BLANK_SEASON, name: "new season" }] };
}

export function removeSeason(form: Climate, n: number): Climate {
  return { ...form, seasons: form.seasons.filter((_, i) => i !== n) };
}
