import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type CharacterSummary } from "../api/client";
import { errMsg } from "./errMsg";

/** The "Generate an opener" block: stream a first post for an empty scene,
 *  then adopt it or keep it as a greeting. Split out of `CastPanel`. */
export function OpenerComposer({ cid, sid, ready, initialPrompt, character, onSeeded, onError }: {
  cid: string;
  sid: string;
  /** An LLM connection is configured; without one there is nothing to call. */
  ready: boolean;
  initialPrompt?: string;
  /** The character the panel's picker has selected, if any — the only one an
   *  opener can be saved against as a greeting. */
  character: CharacterSummary | null;
  /** NAVIGATES in the host (`selectScene(activeId)`), so it is only ever called
   *  for the scene this block is still showing. See `live`. */
  onSeeded: () => void;
  onError: (msg: string | null) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);

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
          onError(e.error.detail);
        }
      });
    } catch (err: any) {
      onError(errMsg(err));
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
    } catch (err: any) {
      onError(errMsg(err));
    }
  }

  async function saveAsGreeting() {
    if (!opener.trim() || !character) return;
    const name = window.prompt("Name this greeting?", "Opener")?.trim();
    if (!name) return;
    // an opener saved as a greeting belongs to the campaign, not the world baseline
    await api.createGreeting({ kind: "campaign", id: cid }, {
      name, character: character.id, version: character.default_version, body: opener,
    });
    setOpener("");
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
          <div className="form-actions">
            <button className="primary" onClick={useOpener} disabled={busy}>Use</button>
            <button className="subtle" onClick={saveAsGreeting} disabled={!character}
                    title={character ? "" : "Pick a character above to attach the saved greeting"}>
              Save as greeting
            </button>
          </div>
        </>
      )}
    </div>
  );
}
