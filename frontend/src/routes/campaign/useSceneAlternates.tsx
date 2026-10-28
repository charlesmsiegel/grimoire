import { useRef, useState } from "react";
import {
  api
} from "../../api/client";
import type { ScopedAlternates } from "./campaignShared";
import { NO_ALTERNATES } from "./campaignShared";
import type { SceneKey } from "./campaignTypes";

export function useSceneAlternates() {
  // Every variant of the generation reroll targets, refreshed by selectScene
  // (which every mutating path already funnels through). `active` is null when
  // the slot is empty — a reroll whose stream died — and picking a variant
  // then puts one back rather than swapping.
  const [alternates, setAlternates] = useState<ScopedAlternates>(NO_ALTERNATES);
  // The in-flight alternates fetch: its token identifies the latest request, and
  // its `sid` is re-keyed by a rename so a response still lands under the right
  // scene. Replaced wholesale per request, so identity comparison is the test.
  const altsReq = useRef({ token: 0, cid: "", sid: "" });

  // Only the LATEST request may write the alternate state — a stale success and
  // a stale rejection are both able to clobber the current scene's set, and the
  // rejection path is the one a scoped success cannot guard. Minting a fresh
  // token is also how a request is *retired*: a rename cannot change the scene
  // id already sent in an outstanding GET, so that GET has to stop being
  // authoritative rather than be re-labelled, or its eventual rejection clears
  // a set that is perfectly valid under the new id.
  function fetchAlternates(forCid: string, forSid: string, windowToken: number) {
    const req = {
      token: ++altsReq.current.token, cid: forCid, sid: forSid,
      // the transcript request this set describes: `selectScene`
      // mints it before calling here, so both land under the same one
      window: windowToken
    };
    altsReq.current = req;
    api.getAlternates(forCid, forSid)
      .then((a) => {
        if (altsReq.current === req) {
          setAlternates({ ...a, cid: req.cid, sid: req.sid, window: req.window });
        }
      })
      .catch(() => { if (altsReq.current === req) setAlternates(NO_ALTERNATES); });
  }
  const clearAlternates = () => setAlternates(NO_ALTERNATES);
  // Re-key what is on screen (the sidecar moved with the scene file, so the
  // set is still correct and the control must not blink) — but retire the
  // in-flight GET rather than re-label it: it still carries the *old* id, so
  // its rejection says nothing about the renamed scene, and honouring that
  // rejection would hide the controls until something re-selects the scene.

  function renameAlternates(scope: SceneKey, newSid: string, token: number) {
    setAlternates((a) => a.cid === scope.cid && a.sid === scope.sid ? { ...a, sid: newSid } : a);
    if (altsReq.current.cid === scope.cid && altsReq.current.sid === scope.sid) {
      fetchAlternates(scope.cid, newSid, token);
    }
  }
  return { clearAlternates, renameAlternates, fetchAlternates, alternates };
}
