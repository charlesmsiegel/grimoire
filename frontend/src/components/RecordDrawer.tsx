import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CastDetail, type CastSource } from "../api/client";

export type DrawerTarget =
  | { type: "actor"; kind: "characters" | "pcs"; id: string }
  | { type: "location"; id: string };

/** The three provenance badges (#99), and what each one is claiming. The
 *  wording is the reason a reader would care: "library" is the promise that
 *  nothing here has been touched, "override" withdraws it, and "emergent"
 *  says there is no library record to compare against in the first place.
 *
 *  Keyed rather than switched so an unrecognized value — a store written by a
 *  newer build, read by an older one — renders no chip at all rather than an
 *  empty box with a border round it. */
const SOURCE: Record<CastSource, { label: string; hint: string }> = {
  library: { label: "Library", hint: "From the world library, unedited in this campaign." },
  override: { label: "Override", hint: "The library's record, with this campaign's edits on top." },
  emergent: { label: "Emergent", hint: "This campaign's own — no library record behind it." },
};

export function RecordDrawer({ cid, sid, target, onClose }:
  { cid: string; sid: string; target: DrawerTarget; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [avatar, setAvatar] = useState<string | null>(null);
  const [source, setSource] = useState<CastSource | null>(null);

  useEffect(() => {
    setAvatar(null);
    // Cleared alongside the avatar, not left standing: the drawer is reused
    // for the next target, and a stale badge would sit under a location's
    // title — or under the next actor's, claiming her predecessor's provenance.
    setSource(null);
    if (target.type === "actor") {
      api.getCastDetail(cid, sid, target.kind, target.id).then((d: CastDetail) => {
        setTitle(d.name);
        setBody(d.body);
        setSource(d.source);
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

  const badge = source ? SOURCE[source] : undefined;
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        <h3>{title}</h3>
        {badge && (
          <span className={`role-chip cast-source ${source}`} title={badge.hint}>{badge.label}</span>
        )}
        {avatar && (
          <img className="drawer-avatar" alt={`${title} avatar`} src={avatar}
               onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
        )}
        <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown></div>
      </aside>
    </div>
  );
}
