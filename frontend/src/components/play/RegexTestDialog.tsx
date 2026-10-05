import { useEffect, useState } from "react";
import { api, type Message } from "../../api/client";
import { useHotkeys } from "../../shortcuts/useHotkeys";
import { RegexTestPane } from "../RegexTestPane";

/** What the pane is opened on: one post of the scene on screen. */
export type RegexTestTarget = { message: Message; depth: number };

/** The rules test pane over one post (spec 6.2), at the campaign's level: the
 *  rules a campaign runs are every level's, so that is where a post's own
 *  processing can be traced.
 *
 *  Filled from the STORED text, never `shown`: the pane runs the rules, so it
 *  has to start where they start. `depth` is the post's distance from the end
 *  of the transcript, and the connection is the one that produced it -- the
 *  pane offers every connection beside it, and one since deleted is still
 *  offered under its id rather than silently replaced by "None". */
export function RegexTestDialog({ cid, target, onClose }: {
  cid: string; target: RegexTestTarget; onClose: () => void;
}) {
  const [connections, setConnections] = useState<{ id: string; name: string }[]>([]);
  const produced = target.message.connection ?? "";

  useEffect(() => {
    let live = true;
    // Inside a promise so a read that throws synchronously degrades the same
    // way a rejected one does: to the pane's "None" and the producer's id.
    Promise.resolve().then(() => api.listConnections())
      .then((rows) => { if (live) setConnections((rows ?? []).map((c) => ({ id: c.id, name: c.name }))); })
      .catch(() => { if (live) setConnections([]); });
    return () => { live = false; };
  }, []);

  useHotkeys([{ keys: "escape", whileTyping: true, run: onClose }], { modal: true });

  const offered = produced && !connections.some((c) => c.id === produced)
    ? [...connections, { id: produced, name: produced }] : connections;

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-modal="true"
         aria-label="Test rules on this post">
      <div className="tagline-modal import-dialog">
        <h3>Test rules on this post</h3>
        <RegexTestPane scope={{ kind: "campaign", cid }} connections={offered}
          initial={{ text: target.message.content,
                     role: target.message.role === "user" ? "user" : "model",
                     depth: target.depth,
                     ...(produced ? { connection: produced } : {}) }} />
        <div className="form-actions">
          <button className="subtle" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
