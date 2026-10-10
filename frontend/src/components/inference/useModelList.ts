import { useCallback, useEffect, useState } from "react";
import {
  api, type CapabilityModel, type CapabilityNeed, type CapabilityValue,
  type ModelCapabilities, type TestableCapability,
} from "../../api/client";

/** Where one model lands for a set of needs. */
export type Placement =
  | { group: "fits" | "unverified"; row: CapabilityModel }
  | { group: "hidden"; id: string; reason: string };

/** Several needs' answers as one list (spec 6.3: "the capabilities API takes
 *  one need per call, so a route needing two combines two calls").
 *
 *  A model **fits** when every need fits, is **hidden** when any need hides it
 *  (with that need's reason), and is otherwise **unverified** -- carrying the
 *  reason from a need that left it unverified, which is the one worth reading.
 *  A provider that rules a need out for every model answers with one `reason`
 *  and no rows, and that reason is the answer for the whole list. */
export function combine(answers: ModelCapabilities[]): {
  reason: string | null; placements: Map<string, Placement>;
} {
  const reason = answers.find((a) => a.reason)?.reason ?? null;
  const placements = new Map<string, Placement>();
  if (reason !== null) return { reason, placements };
  const ids = new Set<string>();
  for (const a of answers) {
    for (const r of [...a.groups.fits, ...a.groups.unverified]) ids.add(r.id);
    for (const h of a.hidden) ids.add(h.id);
  }
  for (const id of [...ids].sort()) {
    const hidden = answers.map((a) => a.hidden.find((h) => h.id === id)).find(Boolean);
    if (hidden) {
      placements.set(id, { group: "hidden", id, reason: hidden.reason });
      continue;
    }
    const fits = answers.map((a) => a.groups.fits.find((r) => r.id === id));
    const unverified = answers.map((a) => a.groups.unverified.find((r) => r.id === id))
      .find(Boolean);
    const everyFits = fits.every(Boolean);
    const row = everyFits ? fits[0] : unverified ?? fits.find(Boolean);
    if (row) placements.set(id, { group: everyFits ? "fits" : "unverified", row });
  }
  return { reason, placements };
}

/** Whether `cap` is a `no` that is knowledge rather than a guess: the
 *  resolver's own rule (`resolve._known_no`), under which the name rule's
 *  `no` is a guess that hides a model but never decides how it is served. */
function knownNo(cap: CapabilityValue | undefined): boolean {
  return cap?.value === "no" && cap.source !== "name";
}

/** The probes a Test… sends for `needs` on `row`: each need it does not
 *  already answer yes to. A need names its own probe, except `decide`: a
 *  decision is answered by structured generation wherever a model generates
 *  (spec 7.4), so it tests `generate`. It tests `decide_native` as well only
 *  for a row the resolver would serve natively -- KNOWN unable to generate
 *  and not known unable to decide natively (`knownNo`, so a name-rule guess
 *  counts for neither): a model that generates stays structured whatever
 *  that probe finds, so the probe would spend and change nothing. The server
 *  refuses anything it has no probe for, with a reason the dialog shows. */
export function probesFor(needs: CapabilityNeed[], row: CapabilityModel | null): TestableCapability[] {
  const all = [...new Set(needs.map((n) => (n === "decide" ? "generate" : n)))] as
    TestableCapability[];
  if (needs.includes("decide") && knownNo(row?.capabilities.generate)
      && !knownNo(row?.capabilities.decide_native)) {
    all.push("decide_native");
  }
  const open = all.filter((c) => row?.capabilities[c]?.value !== "yes");
  return open.length ? open : all;
}

/** Ask each need of one provider, optionally about one model. */
function ask(provider: string, needs: CapabilityNeed[], model?: string) {
  return Promise.all(needs.map((need) => (model === undefined
    ? api.readConnectionCapabilities(provider, need)
    : api.readConnectionCapabilities(provider, need, model))));
}

export type Listed = ReturnType<typeof combine>;

/** One provider's models for a set of needs, and the chosen model's own
 *  verdict when no row lists it (spec 6.3) -- the logic every model control
 *  shares, so the picker and the Models page's select cannot disagree.
 *
 *  An answer is used only while the provider and needs it was asked for are
 *  still the ones shown: a slow list for a provider the reader has left
 *  never fills the next one's. `reask(true)` clears the list first (the
 *  picker's radios are redrawn from nothing, a stale failure with them);
 *  `reask(false)` keeps it on
 *  screen until the new one lands, so a control with focus is not unmounted. */
export function useModelList(provider: string, needs: CapabilityNeed[], model: string) {
  const [listed, setListed] = useState<Listed | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [verdict, setVerdict] = useState<Placement | null>(null);
  const [asked, setAsked] = useState(0);
  // Held as a key, so a caller passing a fresh array each render asks once.
  const needKey = needs.join(",");

  useEffect(() => { setListed(null); setFailed(null); }, [provider, needKey]);

  useEffect(() => {
    if (!provider || !needKey) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[])
      .then((answers) => { if (current) { setListed(combine(answers)); setFailed(null); } })
      .catch((err: unknown) => { if (current) setFailed(err); });
    return () => { current = false; };
  }, [provider, needKey, asked]);

  const known = listed?.placements.get(model);
  const needsVerdict = !!listed && !!model && listed.reason === null && !known;
  useEffect(() => {
    setVerdict(null);
    if (!needsVerdict) return;
    let current = true;
    ask(provider, needKey.split(",") as CapabilityNeed[], model)
      .then((answers) => {
        if (!current) return;
        const { reason, placements } = combine(answers);
        setVerdict(placements.get(model)
          ?? { group: "hidden", id: model, reason: reason ?? "Nothing is known of this id." });
      })
      .catch(() => { if (current) setVerdict(null); });
    return () => { current = false; };
  }, [needsVerdict, provider, needKey, model, asked]);

  const reask = useCallback((clear: boolean) => {
    if (clear) { setListed(null); setFailed(null); }
    setAsked((n) => n + 1);
  }, []);

  return { listed, failed, known, needsVerdict, verdict, reask };
}
