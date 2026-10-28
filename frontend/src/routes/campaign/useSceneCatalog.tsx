import { useCallback, useRef, useState } from "react";
import type { SceneMeta } from "../../api/client";
import {
  api
} from "../../api/client";
import { useStableCallback } from "./campaignShared";
import type { SceneKey } from "./campaignTypes";

export function useSceneCatalog({ currentCampaign }: {
  currentCampaign: () => string;
}) {
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  // Which campaign `scenes` describes. The router reuses this component across
  // /campaigns/A → /campaigns/B, so between the switch and B's list landing
  // `scenes` still holds A's rows — and the resolver below would read B's sid
  // as missing from them and redirect the reader to one of A's scenes under
  // B's id. Every wholesale replacement goes through `installScenes` so the
  // two cannot drift; the one plain `setScenes` left is a rename re-keying the
  // list it already has, which cannot change whose list it is.
  const [sceneListCid, setSceneListCid] = useState<string | null>(null);

  // The campaign check lives HERE rather than at each call site, because every
  // caller reaches this after an await and the mistake is invisible from the
  // outside: `cid` is the render's, so a relist for the campaign the reader has
  // left installs its rows and labels them with the campaign that asked. If the
  // new campaign's own list already landed, `sceneListCid` then names a
  // campaign the resolver will never see again — and it returns early forever,
  // leaving the new campaign wearing the old one's rail with every rail click
  // and URL change dead until a reload (codex review).
  //
  // The mount read carried this guard alone and five mutation relists did not,
  // which is the shape of bug this file has now been bitten by repeatedly: a
  // rule spelled out at call sites is a rule the next call site forgets.
  function installScenes(cid: string, list: SceneMeta[]) {
    if (currentCampaign() !== cid) return;
    setScenes(list);
    setSceneListCid(cid);
  }

  // Every read of the scene list goes through here, and only the newest one
  // ISSUED may install — response order is not request order.
  //
  // Two mutations overlap easily: a rename's relist is still open when the
  // reader deletes another row, and nothing in the UI serializes them
  // (`renamesInFlight` blocks turns, not deletions). If the rename's older
  // answer lands last it puts the deleted row back. That used to be a stale
  // rail and nothing more; now the list decides which sid the URL may name, so
  // the restored row is a ghost the reader can click straight into a read that
  // 404s (codex review).
  //
  // Sequenced PER CAMPAIGN. A single global counter looked equivalent — the
  // reasoning being that a read for the campaign the reader left is always
  // older than the new campaign's own — and that reasoning is false: a
  // mutation in A can finish, and issue its relist, AFTER B's mount read was
  // already in flight. The global counter then hands A's later request the
  // newer number, B's answer is retired by it, and A's own answer is refused by
  // `installScenes` for being the wrong campaign. Nothing installs,
  // `sceneListCid` never becomes B, and the resolver is disabled until reload —
  // the exact stranding the guard exists to prevent, reintroduced by the fix
  // for it (codex review).
  //
  // Keyed by campaign, each campaign's reads order only among themselves, and
  // neither can retire the other's. One entry per campaign opened this session.
  const sceneListSeq = useRef(new Map<string, number>());
  // The newest read in flight per campaign, so a retired one can wait for it.
  const sceneListPending = useRef(new Map<string, Promise<void>>());

  // Resolves when a list at least as new as this call's has been INSTALLED —
  // not merely when this call's own response arrived.
  //
  // The difference is what a caller does next. `sceneCreated` and the first
  // send navigate to a row they have just created, and the resolver bounces a
  // sid the installed list does not have. The rail stays interactive while a
  // creation's relist is open, so a rename or delete can issue a newer read
  // that retires it — and if that newer read has not landed yet, the installed
  // list is still the one from before the scene existed. Returning here would
  // send the caller to navigate against it, and the brand-new id would be read
  // as stale and redirected away (codex review).
  //
  // Awaiting the newer read is not a self-deadlock: this branch is only
  // reached when a newer read exists, so the map holds that one, never this.
  const refreshScenes = useStableCallback((cid: string): Promise<void> => {
    const seq = (sceneListSeq.current.get(cid) ?? 0) + 1;
    sceneListSeq.current.set(cid, seq);
    // A later read FOR THIS CAMPAIGN was issued: it, not this one, is the
    // answer, so the caller gets its completion rather than a false one.
    const retired = () => sceneListSeq.current.get(cid) !== seq;
    const followNewer = () => sceneListPending.current.get(cid);
    const p = (async () => {
      let list: SceneMeta[];
      try {
        // `listScenes` never coalesces, which is what makes the sequence number
        // above honest: a read cannot be handed a promise older than the moment
        // it was issued, so "newest number" and "newest request" agree.
        list = await api.listScenes(cid);
      } catch (err) {
        // Retirement applies to a FAILED read exactly as it does to a
        // successful one, and covering only the success half left the mirror
        // image of the bug it fixed: a superseded read that rejects would
        // reject its caller, so `sceneCreated` never navigated to the scene it
        // had created even though the newer read installed a list containing
        // it — and, being called from a dropped event-handler promise, it did
        // so as an unhandled rejection (codex review).
        //
        // A retired read's failure describes a request nobody is waiting on.
        // Only the campaign's newest read may speak for the list, in either
        // direction.
        if (retired()) return void await followNewer();
        throw err;
      }
      if (retired()) return void await followNewer();
      installScenes(cid, list);
    })();
    sceneListPending.current.set(cid, p);
    return p;
  });
  const listSequence = useCallback((cid: string) => sceneListSeq.current.get(cid), []);
  // The rail's own metadata, not just the things pointing at it. `pcless` and
  // the title are read off the row whose id matches `activeId`, so adopting
  // the id without re-keying the row loses both — an offscreen scene silently
  // offering the PC composer for sends the backend still handles as director
  // notes. A relist would fix it, and a relist that FAILS is exactly the
  // state this has to survive.
  //
  // Campaign-scoped, like the adoption above, and for a sharper reason than
  // tidiness: the rail is what the resolver reads to decide whether the sid
  // in the URL still exists. Re-keying a row in the campaign the reader moved
  // to — B's `s1` matches `oldId` whenever both campaigns number from 001 —
  // makes B's own URL look stale, and the resolver dutifully moves the reader
  // off it. Scoping only the navigation above left this door open, which is
  // how the test for that fix still failed.
  function repointRow(scope: SceneKey, newId: string) {
    if (currentCampaign() !== scope.cid) return;
    setScenes((list) => list.map((s) => s.id === scope.sid ? { ...s, id: newId } : s));
  }
  return { refreshScenes, listSequence, sceneListCid, scenes, repointRow };
}
