import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CastDetail } from "../api/client";

export type DrawerTarget =
  | { type: "actor"; kind: "characters" | "pcs"; id: string }
  | { type: "location"; id: string };

export function RecordDrawer({ cid, sid, target, onClose }:
  { cid: string; sid: string; target: DrawerTarget; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [avatar, setAvatar] = useState<string | null>(null);

  useEffect(() => {
    setAvatar(null);
    if (target.type === "actor") {
      api.getCastDetail(cid, sid, target.kind, target.id).then((d: CastDetail) => {
        setTitle(d.name);
        setBody(d.body);
        // Either actor kind: `d.kind` is the asset base, so a PC's portrait
        // resolves here exactly as a character's does (#219).
        setAvatar(api.actorImageUrl({ kind: "campaign", id: cid }, d.kind, d.id, d.version, "avatar"));
      });
    } else {
      api.readEntity({ kind: "campaign", id: cid }, "locations", target.id).then((e) => {
        setTitle(e.meta.name);
        setBody(e.body);
      });
    }
  }, [cid, sid, target]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        <h3>{title}</h3>
        {avatar && (
          <img className="drawer-avatar" alt={`${title} avatar`} src={avatar}
               onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
        )}
        <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown></div>
      </aside>
    </div>
  );
}
