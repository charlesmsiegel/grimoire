import { useLayoutEffect, useRef, useState } from "react";
import type { CharacterSummary, PCSummary } from "../../api/client";
import { SideSection, type SectionControls } from "./SideSection";
import { PIN_KINDS, usePinChoices, type InspectorPins, type PinKind } from "./useInspectorPins";

export function PinsSection({ cid, sid, rules, chars, pcs, collapsed, toggleSection }:
  SectionControls & { cid: string; sid: string; rules: InspectorPins; chars: CharacterSummary[]; pcs: PCSummary[] }) {
  const { pins, addRule, dropRule } = rules;
  // Starts unset, and the options for a kind are fetched only once one is
  // chosen: the section is open by default and most readers never touch it, so
  // a kind selected up front would mean a list request per campaign for a
  // picker nobody opened.
  const [pinKind, setPinKind] = useState<PinKind | "">("");
  const [pinTarget, setPinTarget] = useState("");
  const [pinMode, setPinMode] = useState<"pin" | "exclude">("pin");
  const [pinTtl, setPinTtl] = useState("");
  const [pinScope, setPinScope] = useState<"scene" | "campaign">("scene");
  // Targets are campaign choices, TTL/scope are scene writes. Reset on their
  // owner change only; hiding the body or rereading the scene keeps the draft.
  const [draftOwner, setDraftOwner] = useState({ cid, sid });
  if (draftOwner.cid !== cid || draftOwner.sid !== sid) {
    setDraftOwner({ cid, sid });
    setPinTtl(""); setPinScope("scene");
    if (draftOwner.cid !== cid) { setPinKind(""); setPinTarget(""); setPinMode("pin"); }
  }
  const owner = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { owner.current = `${cid}/${sid}`; }, [cid, sid]);
  const pinChoices = usePinChoices(cid, pinKind, chars, pcs);
  async function saveRule() {
    if (!pinKind || !pinTarget) return;
    const key = `${cid}/${sid}`;
    if (await addRule({ kind: pinKind, target: pinTarget, mode: pinMode, scope: pinScope, ttl: pinTtl })) {
      if (owner.current === key) { setPinTarget(""); setPinTtl(""); }
    }
  }
  return <>
      {/* Pins & excludes (#129). Directly under the cast, because the row
          toggles above file their rules into this list and a reader needs to
          see where they went — and above Context, which is where the effect
          shows up. */}
      <SideSection id="pins" title="Pins & excludes" collapsed={!!collapsed.pins}
                   onToggle={toggleSection}
                   extra={pins.length > 0 ? <span className="chip on">{pins.length}</span> : null}>
        {pins.length === 0 && (
          <div className="field-hint">
            Nothing pinned. A pin keeps something in the prompt even when the
            budget is tight; an exclude keeps it out.
          </div>
        )}
        {pins.map((p) => (
          <div className="inspector-row-item" key={`${p.scope}/${p.sid}/${p.ref}`}>
            <div className="pin-row">
              <span className={"chip on pin-mode " + p.mode}>
                {p.mode === "pin" ? "pinned" : "excluded"}
              </span>
              <span className="inspector-name">{p.name}</span>
              <span className="field-hint pin-meta">
                {p.kind}
                {p.scope === "campaign" ? " · campaign" : ""}
                {p.remaining !== null ? ` · ${p.remaining} post${p.remaining === 1 ? "" : "s"} left` : ""}
                {p.missing ? " · deleted" : ""}
              </span>
            </div>
            <button className="inspector-row-remove" aria-label={`Lift the rule on ${p.name}`}
                    onClick={() => { void dropRule(p); }}>✕</button>
          </div>
        ))}
        <div className="picker">
          <select aria-label="What to pin or exclude" value={pinKind}
                  onChange={(e) => { setPinKind(e.target.value as PinKind); setPinTarget(""); }}>
            <option value="">— kind —</option>
            {PIN_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select aria-label="Record to pin or exclude" value={pinTarget}
                  onChange={(e) => setPinTarget(e.target.value)}>
            <option value="">— pick —</option>
            {pinChoices}
          </select>
          <select aria-label="Pin or exclude" value={pinMode}
                  onChange={(e) => setPinMode(e.target.value as "pin" | "exclude")}>
            <option value="pin">pin</option>
            <option value="exclude">exclude</option>
          </select>
          <select aria-label="Rule scope" value={pinScope}
                  onChange={(e) => setPinScope(e.target.value as "scene" | "campaign")}>
            <option value="scene">this scene</option>
            <option value="campaign">whole campaign</option>
          </select>
          {/* Only a scene rule can carry one: a TTL counts posts, and a
              campaign-wide rule has no scene to count them in (store/pins.py). */}
          {pinScope === "scene" && (
            <input aria-label="Posts to keep the rule for" type="number" min="0"
                   placeholder="posts (blank = keep)" value={pinTtl}
                   onChange={(e) => setPinTtl(e.target.value)} />
          )}
          {/* "+ Add rule", not "+ Add": the cast picker two sections up has an
              Add of its own, and one rail with two identically-named buttons is
              ambiguous to a screen reader before it is ambiguous to a test. */}
          <button className="primary" onClick={() => { void saveRule(); }}
                  disabled={!pinTarget}>+ Add rule</button>
        </div>
      </SideSection>

  </>;
}
