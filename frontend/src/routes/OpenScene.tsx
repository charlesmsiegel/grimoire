/** Where a completion-notification tap lands.
 *
 *  The Android shell knows the campaign and the scene's stable IDENTITY, not
 *  its id -- an id moves when a scene is renamed, and a notification can sit
 *  unread for a long time, so a link built from one would open a route that
 *  404s or, worse, a scene that has since taken that id. This resolves the
 *  identity and replaces itself with the real route.
 *
 *  Falls back to the campaign rather than an error page: the reply is on disk
 *  either way, and a player who tapped a notification wants to be somewhere
 *  useful, not to be told their scene could not be found.
 */
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { encodeSegment } from "../urlSegment";

export default function OpenScene() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const cid = params.get("campaign") ?? "";
  const identity = params.get("identity") ?? "";

  useEffect(() => {
    let alive = true;
    if (!cid) { navigate("/", { replace: true }); return; }
    void api.sceneByIdentity(cid, identity)
      .then((r) => {
        if (alive) {
          navigate(`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(r.id)}`,
                   { replace: true });
        }
      })
      .catch(() => {
        if (alive) navigate(`/campaigns/${encodeSegment(cid)}`, { replace: true });
      });
    return () => { alive = false; };
  }, [cid, identity, navigate]);

  return null;
}
