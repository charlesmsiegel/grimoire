import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type ResponseBundle,
  type ResponseOverride
} from "../../api/client";
import type { RunRegistry } from "../../runs/RunRegistryProvider";
import { useStableCallback } from "./campaignShared";
import type { SceneKey } from "./campaignTypes";

export function useComposerDraft({ activePcless, activeId, cid, selected, registry }: {
  registry: RunRegistry;
  activePcless: boolean;
  activeId: string | null;
  cid: string;
  selected: () => SceneKey | null;
}) {

  const [input, setInput] = useState("");
  // Response-length chip beside Send: the scene's own preset (its saved
  // setting, from the loaded scene's frontmatter) versus a one-shot pending
  // override the player just picked for the next reply only. `pendingResponse`
  // is never persisted — it rides the next chat/retry/regenerate call and is
  // cleared once a reply actually lands, but survives a failed stream so
  // retry/reroll still honour it (see runStream/send/retry/reroll below).
  // The server's resolved bundle for this scene — the ONLY source of truth for
  // what the next reply is actually budgeted at. The cascade (turn → scene →
  // campaign → global → built-in default) is deliberately not re-implemented
  // here; a campaign-scope preset is invisible to the scene's own frontmatter.
  const [sceneResponse, setSceneResponse] = useState<ResponseBundle | null>(null);
  const [pendingResponse, setPendingResponse] = useState<ResponseOverride | null>(null);
  // What the last quick reply has to say -- a declined fold, a failed roll --
  // scoped to the scene it was tapped in.
  const [quickNotice, setQuickNotice] =
    useState<{ cid: string; sid: string; text: string } | null>(null);

  function chooseResponseOverride(key: "response_continuation_words" | "response_continuation_paragraphs", value: string) {
    if (value && !/^[1-9]\d*$/.test(value)) return;
    setPendingResponse((current) => {
      const next = { ...current };
      if (value) next[key] = value;
      else delete next[key];
      return Object.keys(next).length ? next : null;
    });
  }
  function clearResponseOverride() {
    setPendingResponse(null);
  }

  // #83: the composer's two modes. Speak writes a player post; Direct writes a
  // director note — sent to steer one generation, never stored. Both have
  // always existed; only Direct had no way to say so, which left it inferred
  // (from an offscreen scene, or from an empty box) and unlabelled.
  //
  // Sticky across a scene switch, ON PURPOSE. The composer is one shared box
  // whose text deliberately survives the move, and a mode that reset under
  // surviving text would post as the player's own words something written as
  // direction — the exact confusion the control exists to end.
  const [directMode, setDirectMode] = useState(false);
  // An offscreen scene has no other kind of turn, so it forces the mode rather
  // than merely defaulting it: the toggle is shown locked, not hidden, because
  // "there is no PC here to speak as" is the fact worth teaching.
  const directing = activePcless || directMode;
  // ...and the force is STAMPED, not merely applied for as long as the reader
  // stays. `activePcless` is the scene's property; `directMode` is the
  // composer's, and the composer is the thing the words are in. Without this,
  // a note typed in an offscreen scene and carried into a normal one by the
  // shared box arrived staged as a player post — the mode having quietly
  // reverted while the text it labelled did not, which is the sticky rule
  // above broken in the one case that writes it into the transcript.
  // ...and only while there are WORDS for it to belong to (Codex review). The
  // stamp exists because the composer's text survives a scene switch and its
  // mode has to survive with it — so with an empty box it has nothing to
  // protect, and stamping anyway left an ordinary scene silently in Direct
  // after a visit to an offscreen one, spending the next line typed as a note
  // instead of posting it. Keyed on `input` rather than on leaving, because
  // leaving is when `activePcless` goes FALSE and this would no longer run.
  useEffect(() => {
    if (activePcless && input.trim()) setDirectMode(true);
  }, [activePcless, input]);

  // Prompts recovered from a turn that stored nothing, held under the scene
  // they were written for until that scene is on screen.
  //
  // The composer is one shared box that survives a scene switch, so writing a
  // recovered prompt into it straight away puts scene A's words in front of a
  // player looking at scene B — and the next Send posts them to B. Review
  // caught the same shape once before, in the reroll Retry that followed the
  // player to another scene; this is the other half of it, and the one that
  // moves text rather than an action.
  //
  // Parked rather than dropped, because dropping is the whole thing this
  // machinery exists to prevent: the prompt is nowhere else. A map, not one
  // slot, so a second scene failing does not evict the first — both are the
  // player's and neither can be retyped from anywhere.
  // Codex review (round 2, P2): the KIND travels with the words, not just the
  // words. The mode buttons stay live while a turn runs — the composer is
  // deliberately editable — so a player can be in Speak by the time a Direct
  // turn fails, and text handed back into a Speak composer is posted as
  // dialogue by the next Send. That is the "never posted" contract broken by
  // the recovery meant to protect it.
  // Keyed by CAMPAIGN AND SCENE (Codex review, round 4). Scene ids are
  // campaign-local — `s1` names a different scene in every campaign — and this
  // component is reused across campaign routes, so a scene-only key both
  // collides between campaigns and hands A's words to B's composer. The
  // durable registry has always been campaign-keyed; this half had not caught
  // up, and since #83 getting it wrong also flips the wrong composer's mode.
  const parkedPrompts = useRef<Map<string, { text: string; director: boolean }>>(new Map());
  const parkKey = (c: string, sid: string) => `${c}\u0000${sid}`;
  // Mirrors of the composer's two live facts, for the same reason
  // `selected` exists: `recoverPrompt` runs from a callback that outlives
  // the render it started in, and a captured `input`/`directing` would answer
  // for the moment the turn began rather than the moment it failed.
  const inputRef = useRef("");
  const directingRef = useRef(false);
  inputRef.current = input;
  directingRef.current = directing;
  // Something recovered for THIS scene that the box is not free to take yet.
  // Read off the ref during render rather than mirrored into state: every
  // trigger that can change the answer — parking, switching scene, typing,
  // flipping the mode — is already a re-render.
  const heldForScene = activeId
    ? parkedPrompts.current.get(parkKey(cid, activeId)) : undefined;
  // EITHER kind, not just the note (Codex review). The hold rule is symmetric —
  // a rolled-back post waits behind a note being drafted exactly as a note
  // waits behind dialogue — so a notice that only fired for notes left the
  // player's own words waiting in a place nothing on screen named, which is
  // the whole failure the notice exists to prevent.
  const heldKind: "note" | "post" | null =
    heldForScene && !!input.trim() && heldForScene.director !== directing
      ? (heldForScene.director ? "note" : "post")
      : null;
  const [parkedTick, setParkedTick] = useState(0);

  // Appending, not replacing: the composer stays editable while a turn runs,
  // so the player may already be typing the next thing. Failed prompt first,
  // since it was typed first, joined visibly so it reads as two things to edit
  // rather than one. Shared by both delivery paths so they cannot drift.
  const giveBackPrompt = useCallback((text: string) => {
    setInput((cur) => (cur.trim() ? `${text}\n\n${cur}` : text));
  }, []);

  // Where a recovered prompt goes: the composer if the player is still on the
  // scene it belongs to, the parking map otherwise. `selected`, not
  // `activeId` — this runs from a callback that outlives the render it started
  // in, and a captured `activeId` would be the scene the turn began on, which
  // is exactly the stale answer this exists to avoid.
  const recoverPrompt = useCallback((pcid: string, sid: string, text: string,
    director = false) => {
    // Merged only into a draft of the SAME kind (Codex review, round 3).
    // `giveBackPrompt` prepends, and one composer carries one mode — so
    // folding a recovered note into dialogue typed since would give the
    // combined text a single mode that is wrong for half of it: as Direct the
    // dialogue is spent as an ephemeral note and never posted, as Speak the
    // note is posted as dialogue. Neither is acceptable, and discarding either
    // text is worse than both. So the odd one out waits: it stays parked until
    // the box is free, and `heldNote` says so on screen meanwhile.
    if ((selected()?.cid ?? null) === pcid && (selected()?.sid ?? null) === sid
      && (!inputRef.current.trim() || director === directingRef.current)) {
      giveBackPrompt(text);
      // BOTH directions (Codex review). One-sided, this restored dialogue into
      // a composer left in Direct, and the next send spent it as an ephemeral
      // note instead of posting it — the round-two bug with its mirror still
      // in place. The recovered kind decides the mode, whichever kind it is.
      setDirectMode(director);
      return;
    }
    parkedPrompts.current.set(parkKey(pcid, sid), { text, director });
    setParkedTick((n) => n + 1);
  }, [giveBackPrompt, selected]);
  // Words the registry is still holding for a resolved-but-undurable send, put
  // back where the player can see them. This is what makes the durable copy
  // worth having: after a reload or an Android renderer restart, the component
  // state that `recoverPrompt` wrote into is gone, and this is the pass that
  // reads them out of storage again.
  //
  // Once per (cid, sid): `giveBackPrompt` APPENDS, so running twice for one
  // scene would hand the player their words back doubled.
  const reparked = useRef(new Set<string>());

  /** A quick reply's insert: the canned text into the composer, to edit and
   *  send. An empty box takes it and the reply's turn kind; a draft of the same
   *  kind gets it appended after a blank line. A draft of the OTHER kind keeps
   *  the box -- one composer carries one mode -- so the text waits exactly as a
   *  recovered prompt does, behind the existing held-draft notice, and comes
   *  back when the box is cleared. If something recovered is already waiting
   *  for this scene, the insert is refused instead: parking over it would
   *  drop words the player wrote. */
  function insertQuick(text: string, director: boolean) {
    if (!activeId) return;
    if (!input.trim()) {
      setInput(text);
      setDirectMode(director);
      return;
    }
    if (director === directing) {
      setInput((cur) => `${cur.trimEnd()}\n\n${text}`);
      return;
    }
    const key = parkKey(cid, activeId);
    if (parkedPrompts.current.has(key)) {
      setQuickNotice({
        cid, sid: activeId,
        text: `${director ? "note" : "post"} not inserted · clear the box first`
      });
      return;
    }
    parkedPrompts.current.set(key, { text, director });
    setParkedTick((n) => n + 1);
  }
  function refreshPreset(cid: string, id: string) {
    // Re-read on every selectScene, refresh included: the inspector's picker
    // calls onSceneChanged after a save, so this is what keeps the chip from
    // showing a preset the scene no longer has.
    Promise.resolve(api.getSceneResponse?.(cid, id))
      .then((r) => setSceneResponse(r ?? null))
      .catch(() => setSceneResponse(null));
  }
  const restoreParked = useStableCallback(() => {
    if (!activeId) return;
    const held = parkedPrompts.current.get(parkKey(cid, activeId));
    if (held === undefined) return;
    // Same rule as `recoverPrompt`: a draft of the other kind still owns the
    // box, so the held text waits rather than merging into it. `input` is a
    // dependency for exactly this — clearing or sending the draft is what
    // releases it, and that is the moment it comes back.
    if (input.trim() && held.director !== directing) return;
    parkedPrompts.current.delete(parkKey(cid, activeId));
    giveBackPrompt(held.text);
    // Restored HERE rather than when the note was parked: the player may have
    // been writing something of their own in another scene in the meantime,
    // and flipping the shared composer's mode under that draft would mis-stage
    // it. The mode arrives with the words it belongs to.
    setDirectMode(held.director);   // both directions, as above
  });
  const restoreDurableOnce = useStableCallback(() => {
    if (!activeId) return;
    const once = `${cid}\u0000${activeId}`;
    if (reparked.current.has(once)) return;
    const held = registry.parked(cid, activeId);
    if (held === undefined) return;
    reparked.current.add(once);
    recoverPrompt(cid, activeId, held);
  });
  function repointPark(cid: string, oldId: string, newId: string) {
    const parked = parkedPrompts.current.get(parkKey(cid, oldId));
    if (parked !== undefined) {
      parkedPrompts.current.delete(parkKey(cid, oldId));
      parkedPrompts.current.set(parkKey(cid, newId), parked);
      setParkedTick((n) => n + 1);
    }
  }
  const consumeText = () => { const text = input.trim(); setInput(""); return text; };
  return {
    refreshPreset, restoreParked, restoreDurableOnce, repointPark, consumeText, input, directing,
    setDirectMode, parkedTick, recoverPrompt, setPendingResponse, setInput, pendingResponse, setQuickNotice,
    insertQuick, heldKind, sceneResponse, chooseResponseOverride, clearResponseOverride, quickNotice,
  };
}
