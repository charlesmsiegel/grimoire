import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type CastDetail, type CastSource } from "../api/client";
import { useHotkeys } from "../shortcuts/useHotkeys";

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

  // The drawer covers the transcript, so while it is up it owns the keyboard:
  // Escape closes it, and the scene's own bindings are inert underneath. Until
  // #193 this was the one overlay in the app with no key handling at all — the
  // close button was the only way out of it, which the chooser beside it had
  // never asked of anyone.
  useHotkeys(
    [{ keys: "escape", label: "Close this record", group: "THIS PANEL",
       whileTyping: true, run: onClose }],
    { modal: true },
  );

  useEffect(() => {
    setAvatar(null);
    // Cleared alongside the avatar, not left standing: the drawer is reused
    // for the next target, and a stale badge would sit under a location's
    // title — or under the next actor's, claiming her predecessor's provenance.
    setSource(null);
    // And the same hazard from the other end: two clicks in a row leave two
    // reads in flight, and the slower one lands last. Title and body have
    // always raced here, and a badge is the field where losing that race stops
    // being a smudge and becomes a lie — "Library" over a character whose card
    // this campaign has rewritten. The whole reply is dropped rather than the
    // one field, since a title from one actor and a badge from another is not
    // an improvement.
    let live = true;
    if (target.type === "actor") {
      api.getCastDetail(cid, sid, target.kind, target.id).then((d: CastDetail) => {
        if (!live) return;
        setTitle(d.name);
        setBody(d.body);
        setSource(d.source);
        // Either actor kind: `d.kind` is the asset base, so a PC's portrait
        // resolves here exactly as a character's does (#219).
        setAvatar(api.actorImageUrl({ kind: "campaign", id: cid }, d.kind, d.id, d.version, "avatar"));
      });
    } else {
      api.readEntity({ kind: "campaign", id: cid }, "locations", target.id).then((e) => {
        if (!live) return;
        setTitle(e.meta.name);
        setBody(e.body);
      });
    }
    return () => { live = false; };
  }, [cid, sid, target]);

  // Annotated rather than inferred: `SOURCE` is keyed by `CastSource` so that
  // adding a fourth badge is a compile error here, but the value arrives over
  // HTTP and is not checked by anything — the miss below is real at runtime
  // even though the index signature says it cannot be.
  const badge: { label: string; hint: string } | undefined = source ? SOURCE[source] : undefined;
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
