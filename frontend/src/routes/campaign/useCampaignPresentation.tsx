import type { Dispatch, SetStateAction } from "react";
import { useEffect, useId, useRef, useState } from "react";
import {
  api
} from "../../api/client";
import { errorText } from "../../api/errors";
import { onConfigChanged } from "../../appEvents";
import type { PlayError } from "../../components/play/PlayBanners";
import { usePublishSceneModel } from "../../components/ShellStatus";
import { useStableCallback } from "./campaignShared";

export function useCampaignPresentation({ cid, ready, setError, currentCampaign, refreshScenes, listSequence }: {
  cid: string;
  ready: boolean;
  setError: Dispatch<SetStateAction<PlayError | null>>;
  currentCampaign: () => string;
  refreshScenes: (cid: string) => Promise<void>;
  listSequence: (cid: string) => number | undefined;
}) {
  // The model THIS campaign's scene turns run on, which is not necessarily the
  // library's: a campaign can override Primary, or point its scene route
  // elsewhere. Read here because the header has a pathname and no cid, and
  // answered by the resolver -- the campaign inference view's `scene` route,
  // whose `resolves` is what `require_inference` would serve a turn and whose
  // `problem` is the seam's own refusal of it, so the dot cannot say "ready"
  // about a send the server would refuse. A failed read publishes nothing and
  // the header keeps the library's answer (`GET /config`).
  const [sceneModel, setSceneModel] = useState<string | null>(null);
  const [sceneReady, setSceneReady] = useState<boolean | null>(null);
  const [sceneProblem, setSceneProblem] = useState<string | null>(null);
  const [modelsRev, setModelsRev] = useState(0);
  const [sceneReadFor, setSceneReadFor] = useState<{ cid: string; revision: number } | null>(null);
  // Whether a turn here has a model to answer it: the campaign's own scene
  // route after this campaign/config read has answered. An explicit failed
  // read retains the library fallback, but a pending read cannot grant it.
  // The library's alone was wrong both ways -- a campaign whose own Primary
  // works read "no model is ready", and one whose override has no key read
  // ready until a send came back refused.
  const canGenerate = sceneReadFor?.cid === cid && sceneReadFor.revision === modelsRev
    && (sceneReady ?? ready);
  const [name, setName] = useState("");
  const [worldName, setWorldName] = useState("");
  const [colorQuotes, setColorQuotes] = useState(false);
  const [labels, setLabels] = useState({ user: "You", assistant: "Grimoire" });
  // Whether this campaign resolves to a mechanics pack. `null` while unknown:
  // the dice button renders on `true` only, so a control never appears and then
  // vanishes a moment later once the read lands.
  const [moduleBound, setModuleBound] = useState<boolean | null>(null);
  // Read on every `cid` change and again whenever MechanicsConfig saves, so
  // binding or clearing a pack updates the input bar without a reload.
  //
  // TWO guards, because they stop different things (Codex review):
  //
  // - `moduleReq` drops a read that STARTED earlier and landed later, exactly
  //   as `lockReq`/`anchorReq` do.
  // - `liveCid` drops a read that started later but belongs to a campaign the
  //   reader has left. A token cannot catch that one: MechanicsConfig's save
  //   holds the `onChanged` it was handed, so a PUT issued in campaign A and
  //   settling after a move to B calls A's callback, which would start a
  //   *newer* read of A and commit A's answer over B's.
  //
  // `reset` separates the two callers. A campaign change genuinely knows
  // nothing yet, so it blanks to `null`; a refresh in place keeps the value it
  // has, or the dice button would blink out and back on every save -- and the
  // popover-closing effect below would read that blank as "unbound".
  const liveCid = useRef(cid);
  liveCid.current = cid;
  const moduleReq = useRef(0);
  const readModuleBound = useStableCallback((reset = false) => {
    const forCid = liveCid.current;
    const req = ++moduleReq.current;
    if (reset) setModuleBound(null);
    const settle = (v: boolean) => {
      if (moduleReq.current === req && liveCid.current === forCid) setModuleBound(v);
    };
    api.getCampaignModule(forCid)
      .then((m) => settle(m.resolved !== null))
      // A failed READ is not evidence of anything. On a campaign change there
      // is nothing better to fall back on, so it errs toward "no dice" -- the
      // same side it errs on while the read is out. On a refresh in place there
      // IS something better: the answer already on screen. Keeping it stops a
      // transient failure from retracting the dice button and discarding a
      // half-typed roll with it (Codex review round 2).
      .catch(() => { if (reset) settle(false); });
  });

  useEffect(() => {
    // Scene ids repeat between campaigns. A previous campaign's list failure
    // must go even when selecting this campaign keeps the same scene id (and
    // therefore does not enter selectScene's scene-switch reset). Clear before
    // issuing this campaign's reads, so their own failures remain visible.
    setError(null);
    let live = true;
    setName("");
    setWorldName("");
    api.getCampaign(cid).then((c) => {
      if (!live || currentCampaign() !== cid) return;
      setName(c.meta.name);
      setWorldName(c.meta.world_name ?? ""); // embedded: no second fetch
    }).catch((err: unknown) => {
      if (!live || currentCampaign() !== cid) return;
      setError({ text: `The campaign could not be read: ${errorText(err)}`, retryable: false });
    });
    // Which scene to open is NOT decided here: the URL says, and the resolver
    // below is the one place that reads it -- once this list has installed. A
    // read that fails installs nothing, so dropping its rejection left the page
    // with no scene, no rail and no word of why.
    const mountScenes = refreshScenes(cid);
    const mountSequence = listSequence(cid);
    mountScenes.catch((err: unknown) => {
      // A retired mount read follows a newer mutation's refresh. That caller
      // owns its refusal: only it knows whether the scene was already created.
      if (currentCampaign() === cid && listSequence(cid) === mountSequence) {
        setError({ text: `The scene list could not be read: ${errorText(err)}`, retryable: false });
      }
    });
    api.getConfig().then((c) => {
      setColorQuotes(c.quote_color === "on");
    }).catch(() => { });   // unread, quotes render uncoloured: a style, not the transcript
    readModuleBound(true);   // new campaign: nothing known about it yet
    return () => { live = false; };
  }, [cid, currentCampaign, refreshScenes, listSequence, readModuleBound, setError]);

  // Any model-settings write announces itself (the Inspector's Models section,
  // /models, /providers), and each can move what the scene route resolves to.
  useEffect(() => onConfigChanged(() => setModelsRev((n) => n + 1)), []);
  // Cleared on a campaign switch only: the previous campaign's model must not
  // sit in the header for as long as the read takes. A re-read of THIS one
  // keeps what it shows until the answer lands, rather than dropping to the
  // library's model for a moment on every config change (a theme included).
  useEffect(() => {
    setSceneModel(null);
    setSceneReady(null);
    setSceneProblem(null);
    setLabels({ user: "You", assistant: "Grimoire" });
  }, [cid]);
  // #73: display labels follow this campaign's effective settings. Config is
  // only a fallback after a failed campaign read, so a late global read can
  // never overwrite a successful campaign answer. The effect's lifetime also
  // fences responses from the previous campaign or an older config event.
  useEffect(() => {
    let live = true;
    api.getCampaignSettings(cid).then((view) => {
      if (live) setLabels({
        user: view.effective.user_label || "You",
        assistant: view.effective.assistant_label || "Grimoire"
      });
    }).catch(() => {
      if (!live) return;
      api.getConfig().then((config) => {
        if (live) setLabels({
          user: config.user_label || "You",
          assistant: config.assistant_label || "Grimoire"
        });
      }).catch(() => { });
    });
    return () => { live = false; };
  }, [cid, modelsRev]);
  useEffect(() => {
    let live = true;
    api.getCampaignInference(cid).then((view) => {
      if (!live) return;
      // No scene route in the answer is no answer: the library's, as on a
      // failed read, rather than the last one this campaign gave.
      const scene = view.routes.find((r) => r.key === "scene");
      setSceneModel(scene?.resolves?.model || null);
      setSceneReady(scene ? scene.problem === null : null);
      setSceneProblem(scene?.problem ?? null);
      setSceneReadFor({ cid, revision: modelsRev });
    }).catch(() => {
      if (live) {
        setSceneModel(null); setSceneReady(null); setSceneProblem(null);
        setSceneReadFor({ cid, revision: modelsRev });
      }
    });
    return () => { live = false; };
  }, [cid, modelsRev]);
  usePublishSceneModel(sceneModel, sceneReady);
  // The banner that says why not, which is what explains a disabled Send.
  const notReadyId = useId();
  return { name, canGenerate, moduleBound, labels, worldName, sceneProblem, notReadyId, readModuleBound, colorQuotes };
}
