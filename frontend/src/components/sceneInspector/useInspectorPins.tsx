import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, type CharacterSummary, type PCSummary, type PinRule } from "../../api/client";
import { errorText } from "../../api/errors";
import type { InspectorInputs } from "./types";

/** What a pin or exclude can name (#129) — the world-info kinds plus the two
 *  actor kinds, mirroring `store/pins.py`'s KINDS. The order is the picker's. */
export const PIN_KINDS = ["lore", "locations", "items", "groups", "creatures",
                   "characters", "pcs"] as const;
export type PinKind = typeof PIN_KINDS[number];

export type RuleDraft = { kind: PinKind; target: string; mode: "pin" | "exclude";
  scope: "scene" | "campaign"; ttl: string };
export function useInspectorPins({ cid, sid, refreshKey, onSceneChanged, onError }:
  InspectorInputs<"cid" | "sid" | "refreshKey" | "onSceneChanged"> & {
    onError: (error: string | null) => void }) {
  const key = `${cid}/${sid}`;
  const owner = useRef(key);
  const read = useRef(0);
  useLayoutEffect(() => {
    owner.current = key;
    return () => { owner.current = ""; };
  }, [key]);
  // --- pins & excludes (#129) ---
  // Held with the campaign AND scene it was read for, like `brief` and `turns`
  // above and for a sharper version of their reason: this component survives
  // both switches, and these rules do not merely LABEL the panel, they decide
  // what the row toggles do. A bare array left the previous scene's rules
  // driving the new scene's buttons -- a toggle reading "pinned" from a scene
  // the reader is no longer in, whose click then asks the server to lift a rule
  // that was never there. Comparing during render makes that impossible rather
  // than short.
  const [pinState, setPinState] =
    useState<{ cid: string; sid: string; rows: PinRule[] } | null>(null);
  const pins = pinState && pinState.cid === cid && pinState.sid === sid ? pinState.rows : [];

  const reloadPins = useCallback(async () => {
    if (owner.current !== `${cid}/${sid}`) return;
    const token = ++read.current;
    try {
      const result = await api.getPins(cid, sid);
      if (owner.current === `${cid}/${sid}` && token === read.current) setPinState({ cid, sid, rows: result.pins });
    } catch {
      if (owner.current === `${cid}/${sid}` && token === read.current) setPinState({ cid, sid, rows: [] });
    }
  }, [cid, sid]);
  useEffect(() => {
    void reloadPins();
    return () => { read.current += 1; };
  }, [refreshKey, reloadPins]);
  /** Toggle one scene-scoped rule for `ref`.
   *
   *  Toggling rather than always setting is what makes one button per mode
   *  enough: clicking Pin on something already pinned is the reader undoing it,
   *  and clicking Exclude on something pinned REPLACES the rule (the store keys
   *  one rule per scope and target), which is the same thing they would mean by
   *  it. TTL is deliberately absent here — the quick toggle is the standing
   *  version, and a countdown is set from the section below, where the number
   *  is visible. */
  async function toggleRule(ref: string, mode: "pin" | "exclude") {
    onError(null);
    const held = pins.find((p) => p.ref === ref && p.scope === "scene" && p.mode === mode);
    try {
      if (held) await api.removePin(cid, ref, "scene", sid);
      else await api.setPin(cid, { ref, mode, scope: "scene", sid });
      if (owner.current !== key) return;
      await reloadPins();
      if (owner.current !== key) return;
      onSceneChanged();   // the prompt changed, so the context panel has moved
    } catch (err: unknown) {
      if (owner.current === key) onError(errorText(err));
    }
  }
  async function dropRule(p: PinRule) {
    onError(null);
    try {
      await api.removePin(cid, p.ref, p.scope, p.sid);
      if (owner.current !== key) return;
      await reloadPins();
      if (owner.current !== key) return;
      onSceneChanged();
    } catch (err: unknown) {
      if (owner.current === key) onError(errorText(err));
    }
  }

  /** Whether a rule of this mode is in force for `ref`, at either scope — the
   *  toggle reports what the prompt is actually doing, not just what this scene
   *  asked for. */
  function ruleOn(ref: string, mode: "pin" | "exclude"): boolean {
    return pins.some((p) => p.ref === ref && p.mode === mode);
  }

  /** A campaign-wide rule naming `ref`, which is what makes the row toggles
   *  read-only for it.
   *
   *  Without this the button showed pressed (the rule IS in force) while the
   *  click below only ever touched scene scope: pressing it filed a redundant
   *  scene rule and changed nothing visible, and pressing it again removed that
   *  rule and still changed nothing. A standing campaign rule is lifted where it
   *  was set — the list below, which shows it with its own ✕ — and overridden
   *  for one scene from the picker there. */
  function standingRule(ref: string): PinRule | undefined {
    return pins.find((p) => p.ref === ref && p.scope === "campaign");
  }

  async function addRule(draft: RuleDraft): Promise<boolean> {
    onError(null);
    const ttl = draft.scope === "scene" ? Math.max(parseInt(draft.ttl, 10) || 0, 0) : 0;
    try {
      await api.setPin(cid, { ref: `${draft.kind}:${draft.target}`, mode: draft.mode,
        scope: draft.scope, sid, ttl_posts: ttl });
      if (owner.current !== key) return false;
      await reloadPins();
      if (owner.current !== key) return false;
      onSceneChanged();
      return true;
    } catch (err: unknown) {
      if (owner.current === key) onError(errorText(err));
      return false;
    }
  }
  return { pins, ruleOn, standingRule, toggleRule, addRule, dropRule };
}
export type InspectorPins = ReturnType<typeof useInspectorPins>;

export function usePinChoices(cid: string, pinKind: PinKind | "", chars: CharacterSummary[], pcs: PCSummary[]) {
  // Lazily, one kind at a time: the picker offers seven kinds and a reader opens
  // it rarely, so loading all seven with the panel would be seven requests per
  // campaign for a section that is usually empty and shut.
  // Keyed by campaign as well as kind, for the same reason: the cache outlives
  // a campaign switch, so a bare `{lore: [...]}` offered one campaign's lore in
  // another campaign's picker -- and pinning from it would file a rule naming
  // an entry this campaign has never had.
  const [pinOptions, setPinOptions] =
    useState<Record<string, { id: string; name: string }[]>>({});

  // The picker's options for the kind currently selected, fetched once per kind
  // per campaign. Actors come from lists the panel already holds.
  const pinOptionKey = `${cid}/${pinKind}`;
  useEffect(() => {
    if (!pinKind || pinKind === "characters" || pinKind === "pcs" || pinOptions[pinOptionKey]) return;
    let live = true;
    api.listEntities({ kind: "campaign", id: cid }, pinKind)
      .then((rows) => {
        if (live) setPinOptions((prev) => ({ ...prev,
          [pinOptionKey]: rows.map((r) => ({ id: r.id, name: r.name })) }));
      })
      .catch(() => { if (live) setPinOptions((prev) => ({ ...prev, [pinOptionKey]: [] })); });
    return () => { live = false; };
  }, [cid, pinKind, pinOptionKey, pinOptions]);

  const pinChoices = useMemo(() => {
    const rows: { id: string; name: string }[] =
      pinKind === "characters" ? chars
      : pinKind === "pcs" ? pcs
      : pinKind ? pinOptions[pinOptionKey] ?? []
      : [];
    return rows.map((o) => <option key={o.id} value={o.id}>{o.name}</option>);
  }, [pinKind, chars, pcs, pinOptions, pinOptionKey]);
  return pinChoices;
}
