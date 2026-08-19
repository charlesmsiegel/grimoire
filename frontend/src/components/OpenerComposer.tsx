import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type CharacterSummary } from "../api/client";

/** The "Generate an opener" block: stream a first post for an empty scene,
 *  then adopt it or keep it as a greeting. Split out of `CastPanel`. */
export function OpenerComposer({ cid, sid, ready, initialPrompt, characters, onSeeded, onError }: {
  cid: string;
  sid: string;
  /** An LLM connection is configured; without one there is nothing to call. */
  ready: boolean;
  initialPrompt?: string;
  /** Every character an opener could be saved against. Which one it IS saved
   *  against is this block's own state, not the panel's add-to-scene selection:
   *  those are unrelated choices, and sharing them meant staging a PC disabled
   *  saving outright, while restaging an actor silently re-targeted the save
   *  (#12). Greetings attach to characters only, so PCs are not offered. */
  characters: CharacterSummary[];
  /** NAVIGATES in the host (`selectScene(activeId)`), so it is only ever called
   *  for the scene this block is still showing. See `live`. */
  onSeeded: () => void;
  /** Raw, not stringified: the panel above renders it, and `kind` is what
   *  tells it the model could not be reached at all (#210). */
  onError: (err: unknown) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);
  const [charId, setCharId] = useState("");
  const [versionPick, setVersionPick] = useState("");

  const target = characters.find((c) => c.id === charId) ?? null;
  // The version to post: the explicit pick while the target still offers it,
  // its default otherwise — no pick yet, or the pick belongs to the character
  // picked before this one. That last case is not only cosmetic: this block
  // outlives a campaign switch, and `create_greeting` writes the version it is
  // given without checking, so a pick carried into a campaign whose copy of the
  // character lacks that version would bake a dangling ref into the greeting.
  const version = target?.versions.some((v) => v.id === versionPick)
    ? versionPick
    : target?.default_version ?? "";

  // seed from the chooser's premise; reset on scene switch so a prior
  // scene's premise never lingers in another scene's opener box
  useEffect(() => { setPrompt(initialPrompt ?? ""); }, [sid, initialPrompt]);

  // Which campaign/scene this block is showing NOW, readable from a callback
  // created under a previous one. See `generate`'s `finally`.
  //
  // A LAYOUT effect, not a passive one. Passive effects are scheduled in their
  // own task, so between committing scene B and running them there is a gap in
  // which microtasks run — and an opener from scene A settling in that gap
  // reads a ref that still says A, matches the props it closed over, and
  // navigates the reader back. Layout effects run synchronously inside the
  // commit, where no promise callback can interleave, so the ref is never
  // observable as stale.
  const live = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => { live.current = `${cid}/${sid}`; }, [cid, sid]);

  async function generate() {
    if (!prompt.trim() || busy) return;
    onError(null);
    setOpener("");
    setBusy(true);
    let acc = "";
    try {
      await api.opener(cid, sid, prompt, (e) => {
        if (e.delta) {
          acc += e.delta;
          setOpener(acc);
        } else if (e.error) {
          onError(e.error);
        }
      });
    } catch (err: unknown) {
      onError(err);
    } finally {
      setBusy(false);
      // The backend records an `opener` prompt snapshot for this attempt, and
      // nothing else here bumps the refresh — so without this the inspector's
      // Turn history keeps saying "No captured turns yet", and a rejected
      // preview leaves the row invisible indefinitely (#157).
      //
      // Guarded, because `onSeeded` NAVIGATES: the host's version is
      // `() => selectScene(activeId)`, closed over the id this render was given.
      // On the same scene that is a refresh and the preview above survives it —
      // but a reader who started an opener in scene A and moved to B would be
      // yanked back to A when the request finished, failure included. A ref,
      // because this callback closes over the props it was created with, so
      // comparing those to themselves would always agree.
      if (live.current === `${cid}/${sid}`) onSeeded();
    }
  }

  async function useOpener() {
    if (!opener.trim() || busy) return;
    onError(null);
    try {
      await api.firstPost(cid, sid, opener);
      setOpener("");
      onSeeded(); // the adopted opener now shows as the scene's first post
    } catch (err: unknown) {
      onError(err);
    }
  }

  async function saveAsGreeting() {
    if (!opener.trim() || !target) return;
    const name = window.prompt("Name this greeting?", "Opener")?.trim();
    if (!name) return;
    onError(null);
    try {
      // an opener saved as a greeting belongs to the campaign, not the world baseline
      await api.createGreeting({ kind: "campaign", id: cid }, {
        name, character: target.id, version, body: opener,
      });
      setOpener("");
    } catch (err: unknown) {
      // Clearing the preview on a failed save would throw away the only copy
      // of a generation that cost a call, so the text stays put.
      onError(err);
    }
  }

  return (
    <div>
      <div className="role">Generate an opener</div>
      {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
      <div className="picker">
        <input type="text" aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
               value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <button className="primary" onClick={generate} disabled={!ready || busy || !prompt.trim()}>
          {busy ? "…" : "Generate"}
        </button>
      </div>
      {opener && (
        <>
          <div className="opener-preview">{opener}</div>
          <div className="picker">
            <select aria-label="Greeting character" value={charId}
                    onChange={(e) => { setCharId(e.target.value); setVersionPick(""); }}>
              <option value="">— save as whose greeting? —</option>
              {characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            {/* A picked character always has versions to offer: the store skips
                version-less characters when it lists them, and reports a
                `default_version` drawn from the list it emits. */}
            {target && (
              <select aria-label="Greeting version" value={version}
                      onChange={(e) => setVersionPick(e.target.value)}>
                {target.versions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            )}
          </div>
          <div className="form-actions">
            <button className="primary" onClick={useOpener} disabled={busy}>Use</button>
            <button className="subtle" onClick={saveAsGreeting} disabled={!target}
                    title={target ? "" : "Pick a character to attach the saved greeting to"}>
              Save as greeting
            </button>
          </div>
        </>
      )}
    </div>
  );
}
