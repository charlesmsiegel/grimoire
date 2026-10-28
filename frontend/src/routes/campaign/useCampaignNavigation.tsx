import { useEffect } from "react";
import type { NavigateFunction } from "react-router-dom";
import {
  type SceneMeta
} from "../../api/client";
import type { SceneKey } from "./campaignTypes";
import { sceneUrl } from "./campaignShared";

export function useCampaignNavigation({ selected, cid, readScene, navigate, sceneListCid, sid, scenes, clearScene, activeId }: {
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  cid: string;
  readScene: (scope: SceneKey) => void;
  navigate: NavigateFunction;
  sceneListCid: string | null;
  sid: string | undefined;
  scenes: SceneMeta[];
  clearScene: () => void;
  activeId: string | null;
}) {

  // A scene the reader chose: a real history entry, so Back returns to the one
  // they were reading. Everything else moves the URL with `replace`.

  // THE RESOLVER (#87). The URL names the scene; the rail's list says whether
  // it still exists; `selected` says what is already loaded. Reconciling
  // those three is this effect's whole job, and it is the ONLY place that
  // decides what to read or where to send the reader instead — so a rail
  // click, Back/Forward, a deep link, a delete and a rename all converge here
  // rather than each re-deciding for itself.
  //
  // Two rules make it terminate and keep it honest:
  //
  //   - It reads `selected`, not `activeId`. The paths that adopt an id
  //     WITHOUT navigating — a rename mints a new id for the scene already on
  //     screen, and the first send into an empty campaign creates one — set
  //     the ref and point the URL at it in the same batch. This then sees them
  //     agreeing and does nothing, which is what preserves the rename's
  //     `renamed` refresh (turn state kept) over a switch (turn state cleared).
  //   - Every redirect it issues is a `replace`. A scene the reader never
  //     chose — a fallback from a dead id, the scene a delete left behind — is
  //     not a place they went, and Back must not return them to a URL that
  //     resolves to nothing.
  //
  // `activeId` is in the deps because a divergence is exactly what has to be
  // repaired: a background refresh (runStream's finally, the flush poll) calls
  // `selectScene` with the scene the TURN belongs to, which is not always the
  // scene the reader is now looking at. The URL is authoritative, so the fix
  // is to read the URL's scene back — not to leave the two disagreeing.
  useEffect(() => {
    if (sceneListCid !== cid) return;      // the list is still the last campaign's
    if (sid && scenes.some((s) => s.id === sid)) {
      const loadedHere = sid === (selected()?.sid ?? null) && (selected()?.cid ?? null) === cid;
      if (!loadedHere) readScene({ cid, sid });
      return;
    }
    // No scene in the URL, or one that names nothing: scene ids are filenames
    // and a rename, a first date stamp or a repad all mint a new one, so a
    // bookmarked id goes stale on its own. Never fetch it — a dead id is a 404
    // and an error banner where a fallback belongs.
    if (scenes.length) {
      // Landing on /scenes with no scene named is a request for "the scenes of
      // this campaign", so the newest is a reasonable answer. Landing on a sid
      // that no longer resolves is not -- ids are filenames and a rename mints
      // a new one -- and the same fallback covers both.
      void navigate(sceneUrl(cid, scenes[0].id), { replace: true });
    } else if (sid) {
      // A named scene that resolves to nothing, in a campaign with none left:
      // back to `/scenes`, which is the empty play view rather than a 404.
      void navigate(`/campaigns/${encodeURIComponent(cid)}/scenes`, { replace: true });
      if ((selected()?.sid ?? null)) clearScene();
    } else {
      // An empty campaign sits here with its composer. Sending into it creates
      // the first scene -- so bouncing to the hub would delete that path, and
      // the hub's own "Start a scene" link lands right back here.
      if ((selected()?.sid ?? null)) clearScene();
    }
  }, [cid, sid, scenes, sceneListCid, activeId, selected, readScene, clearScene, navigate]);

}
