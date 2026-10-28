import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { THUMB } from "../../api/thumbs";
import { Portrait } from "../Portrait";
import type { DrawerTarget } from "../RecordDrawer";
import { SuggestedCast } from "../SuggestedCast";
import { SideSection, type SectionControls } from "./SideSection";
import type { InspectorCast } from "./useInspectorCast";
import type { InspectorPins } from "./useInspectorPins";

export function CastSection({ cid, sid, pcless, castView, pinActions, setDrawer, collapsed, toggleSection }:
  SectionControls & { cid: string; sid: string; pcless?: boolean | undefined; castView: InspectorCast;
    pinActions: Pick<InspectorPins, "ruleOn" | "standingRule" | "toggleRule">;
    setDrawer: (target: DrawerTarget) => void }) {
  const { cast, chars, pcs, roster, names, nameOf, addMember, removeCastMember, suggestedCastAdded } = castView;
  const { ruleOn, standingRule, toggleRule } = pinActions;
  const [addKind, setAddKind] = useState<"characters" | "pcs">("characters");
  const [addActorId, setAddActorId] = useState("");
  const [addRole, setAddRole] = useState<"player" | "npc">("npc");

  // The picker belongs to the campaign, and remains mounted when its body is
  // collapsed. Refreshes retain typing; a campaign switch retires the choice.
  const [draftCampaign, setDraftCampaign] = useState(cid);
  if (draftCampaign !== cid) {
    setDraftCampaign(cid); setAddKind("characters"); setAddActorId(""); setAddRole("npc");
  }
  const owner = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { owner.current = `${cid}/${sid}`; }, [cid, sid]);
  // The pickers' options, built once per change to what they list rather than
  // on every render. A campaign's cast and location lists are the longest
  // things in this panel, and rebuilding an `<option>` per record on each of
  // its re-renders was most of what an open inspector cost.
  const addOptions = useMemo(() => {
    const seated = new Set(cast.filter((a) => a.kind === addKind).map((a) => a.id));
    return (addKind === "characters" ? chars : pcs)
      .filter((o) => !seated.has(o.id))
      .map((o) => <option key={o.id} value={o.id}>{o.name}</option>);
  }, [addKind, chars, pcs, cast]);

  async function addCastMember() {
    const key = `${cid}/${sid}`;
    if (await addMember(addKind, addActorId, addRole)) {
      if (owner.current === key) setAddActorId("");
    }
  }
  return <>
      <SideSection id="cast" title="Active characters" collapsed={!!collapsed.cast} onToggle={toggleSection}>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => {
          // The roster carries the locked version for either actor kind, and
          // so does the image route, so a PC gets a portrait here too (#219).
          const locked = roster.find((r) => r.kind === a.kind && r.id === a.id);
          const ver = locked?.version;
          const pc = a.role === "player";
          const ref = `${a.kind}:${a.id}`;
          return (
            <div className="inspector-row-item" key={`${a.kind}/${a.id}`}>
              <button className={"inspector-row" + (pc ? " pc" : "")}
                      onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
                <Portrait src={ver ? api.actorImageUrl({ kind: "campaign", id: cid },
                                                            a.kind, a.id, ver, "avatar",
                                                            { w: THUMB.face, v: locked?.avatar_v })
                                 : null}
                          name={nameOf(a)} />
                <span className="inspector-name">{nameOf(a)}</span>
                <span className="role-chip">{pc ? "player" : "npc"}</span>
              </button>
              {/* Two toggles rather than one control with a mode, because they
                  are opposite requests about the same person and both have to
                  be reachable in one click from the row they are about (#129).
                  `aria-pressed` carries the state: the glyphs alone would leave
                  a screen reader with two identically-named buttons. */}
              <button className={"inspector-row-pin" + (ruleOn(ref, "pin") ? " on" : "")}
                      aria-pressed={ruleOn(ref, "pin")} disabled={!!standingRule(ref)}
                      aria-label={`Pin ${nameOf(a)} in the prompt`}
                      title={standingRule(ref) ? "Set for the whole campaign — change it below"
                                               : "Keep in the prompt under budget pressure"}
                      onClick={() => { void toggleRule(ref, "pin"); }}>📌</button>
              <button className={"inspector-row-pin" + (ruleOn(ref, "exclude") ? " on" : "")}
                      aria-pressed={ruleOn(ref, "exclude")} disabled={!!standingRule(ref)}
                      aria-label={`Exclude ${nameOf(a)} from the prompt`}
                      title={standingRule(ref) ? "Set for the whole campaign — change it below"
                                               : "Keep out of the prompt"}
                      onClick={() => { void toggleRule(ref, "exclude"); }}>🚫</button>
              <button className="inspector-row-remove" aria-label={`Remove ${nameOf(a)} from scene`}
                      onClick={() => { void removeCastMember(a); }}>✕</button>
            </div>
          );
        })}
        <div className="picker">
          {!pcless && (
            <select aria-label="Cast kind to add" value={addKind}
                    onChange={(e) => { setAddKind(e.target.value as "characters" | "pcs"); setAddActorId(""); }}>
              <option value="characters">Character</option>
              <option value="pcs">PC</option>
            </select>
          )}
          <select aria-label="Character or PC to add" value={addActorId}
                  onChange={(e) => setAddActorId(e.target.value)}>
            <option value="">— pick —</option>
            {addOptions}
          </select>
          {addKind === "characters" && !pcless && (
            <select aria-label="Role for new cast member" value={addRole}
                    onChange={(e) => setAddRole(e.target.value as "player" | "npc")}>
              <option value="npc">npc</option>
              <option value="player">player</option>
            </select>
          )}
          <button className="primary" onClick={() => { void addCastMember(); }} disabled={!addActorId}>+ Add</button>
        </div>
        {/* "Who should appear" (#96), under the picker it shortcuts. Takes the
            cast, NOT `refreshKey`: the scan reads these characters' cards and
            never the transcript, so keying it on the turn counter would buy a
            request per turn to be told the same thing. */}
        <SuggestedCast cid={cid} sid={sid} cast={cast}
                       nameOf={(id) => names[`characters/${id}`]
                                       ?? chars.find((c) => c.id === id)?.name ?? id}
                       onCast={suggestedCastAdded} />
      </SideSection>

  </>;
}
