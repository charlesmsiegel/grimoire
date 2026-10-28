import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type RegexBundle, type SceneRewrite } from "../../api/client";
import { errorText } from "../../api/errors";
import { LOCKED_WHILE_GENERATING } from "../sceneLock";
import type { InspectorInputs, RewrittenPost } from "./types";
/** What the reload after a stale restore says, so the player knows why the
 *  post is not what they asked for. */
const STALE_RESTORE =
  "This post has changed since it was rewritten, so nothing was restored. The scene was reloaded.";

/** Rule id -> name, over every rule a campaign runs (its own and every level it
 *  inherits). A rule since deleted is not in it, and is shown by its id. */
function ruleNames(bundle: RegexBundle | undefined): Record<string, string> {
  const names: Record<string, string> = {};
  for (const e of bundle?.inherited ?? []) names[e.rule.id] = e.rule.name;
  for (const r of bundle?.layer.rules ?? []) names[r.id] = r.name;
  return names;
}

/** One rewritten post, opened: what was written, what is stored now, the rules
 *  that changed it, and Restore original.
 *
 *  The record is read on opening rather than with the scene, because only a
 *  restore needs the original and a scene read would carry every one of them.
 *  It is looked up by the key the scene read named (`rewrite_key`), which is
 *  the server's to work out -- a later part of a response has a key of its
 *  own -- and, from a read that names none, by `response_id`, else `post_id`,
 *  the first that has one. Restore writes that
 *  original back through the ordinary message edit with `restore`, which the
 *  server refuses (409 `rewrite_stale`) unless the post still says what the
 *  rewrite stored -- so a view that has fallen behind cannot overwrite newer
 *  text, and that refusal reloads the scene instead. */
function RewriteDetail({ cid, sid, post, locked, onRestored, onStale }: {
  cid: string; sid: string; post: RewrittenPost; locked: boolean;
  onRestored: () => void; onStale: () => void;
}) {
  const [read, setRead] =
    useState<{ rec: SceneRewrite | null; names: Record<string, string> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read again only when the post itself moves. Every transcript read hands
  // the inspector new message objects, and re-reading on identity would blank
  // the open record to "Loading…" on every turn.
  const { response_id: responseId, post_id: postId, rewrite_key: rewriteKey, content } = post.message;
  useEffect(() => {
    let live = true;
    setRead(null); setError(null);
    // The names are a nicety: a failed regex read shows ids, never no record.
    const names = Promise.resolve().then(() => api.getRegex({ kind: "campaign", cid }))
      .then(ruleNames).catch(() => ({}));
    Promise.all([api.getSceneRewrites(cid, sid), names]).then(([records, n]) => {
      if (!live) return;
      const key = rewriteKey ? rewriteKey : [responseId, postId].find((k) => k && records[k]);
      setRead({ rec: key ? records[key] ?? null : null, names: n });
    }).catch((err) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [cid, sid, responseId, postId, rewriteKey, content]);

  async function restore() {
    if (!read?.rec || busy || locked) return;
    setBusy(true); setError(null);
    try {
      await api.editMessage(cid, sid, post.index, read.rec.original, { restore: true });
      onRestored();
    } catch (err) {
      const kind = typeof err === "object" && err !== null
        ? (err as { kind?: unknown }).kind : undefined;
      if (kind === "rewrite_stale") onStale();
      else setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="side-section-body" aria-label={`Rewrite of post ${post.index + 1}`}>
      {error && <div className="banner" role="alert">{error}</div>}
      {!read && !error && <div className="field-hint">Loading…</div>}
      {read && !read.rec && (
        <div className="field-hint">
          This post no longer has a recorded original, so there is nothing to restore.
        </div>
      )}
      {read?.rec && (
        <>
          <div className="field-hint">Written</div>
          <pre className="regex-code">{read.rec.original}</pre>
          <div className="field-hint">Stored now</div>
          <pre className="regex-code">{content}</pre>
          <div className="field-hint">Changed by</div>
          <div>
            {/* A record the server could not vouch for may carry anything here;
                a sync conflict is not a reason to lose the inspector. */}
            {(Array.isArray(read.rec.rules) ? read.rec.rules : [])
              .filter((id): id is string => typeof id === "string").map((id) => (
              <span key={id} className="chip on">{read.names[id] || id}</span>
            ))}
          </div>
          <div className="form-actions">
            <button className="primary" onClick={() => void restore()} disabled={busy || locked}
                    title={locked ? LOCKED_WHILE_GENERATING : undefined}>
              Restore original
            </button>
          </div>
        </>
      )}
    </section>
  );

}

export function useRewriteHistory({ cid, sid, onTranscriptEdited, onSceneChanged }:
  InspectorInputs<"cid" | "sid" | "onTranscriptEdited" | "onSceneChanged">) {
  // The rewritten post opened under Turn history, and what a stale restore had
  // to say. Both stamped with their scene, like `turns`, because the inspector
  // outlives a switch; the note is held here rather than in the opened post,
  // since the reload it reports usually takes that post off the list.
  const [openRewrite, setOpenRewrite] =
    useState<{ cid: string; sid: string; index: number } | null>(null);
  const [rewriteNote, setRewriteNote] = useState<{ cid: string; sid: string } | null>(null);
  const owner = useRef(`${cid}/${sid}`);
  useLayoutEffect(() => {
    owner.current = `${cid}/${sid}`;
    return () => { owner.current = ""; };
  }, [cid, sid]);
  function selectRewrite(index: number, open: boolean) {
    setRewriteNote(null);
    setOpenRewrite(open ? null : { cid, sid, index });
  }
  function restored() {
    if (owner.current !== `${cid}/${sid}`) return;
    setOpenRewrite(null);
    (onTranscriptEdited ?? onSceneChanged)();
  }
  function stale() {
    if (owner.current !== `${cid}/${sid}`) return;
    setOpenRewrite(null);
    setRewriteNote({ cid, sid });
    onSceneChanged();
  }
  return { openRewrite, rewriteNote, selectRewrite, restored, stale };
}

export function RewriteHistory({ cid, sid, rewritten, sceneLocked, history }:
  InspectorInputs<"cid" | "sid" | "rewritten" | "sceneLocked"> & {
    history: ReturnType<typeof useRewriteHistory> }) {
  const { openRewrite, rewriteNote, selectRewrite, restored, stale } = history;
  return <>
        {/* Posts a `rewrite_stored` rule changed as they landed. Listed by
            post rather than beside a captured turn: a capture records the
            prompt that was sent, not which post its reply became. */}
        {rewriteNote?.cid === cid && rewriteNote.sid === sid && (
          <div className="banner" role="status">{STALE_RESTORE}</div>
        )}
        {(rewritten ?? []).map((p) => {
          const open = openRewrite?.cid === cid && openRewrite.sid === sid
            && openRewrite.index === p.index;
          return (
            <div key={`rewrite-${p.index}`}>
              <button className={"inspector-row" + (open ? " on" : "")} aria-expanded={open}
                      onClick={() => {
                        selectRewrite(p.index, open);
                      }}>
                <span className="inspector-name">
                  {(p.message.speaker || (p.message.role === "user" ? "You" : "Reply"))
                   + ` · post ${p.index + 1}`}
                </span>
                <span className="chip on">Rewritten</span>
              </button>
              {open && (
                <RewriteDetail cid={cid} sid={sid} post={p} locked={!!sceneLocked}
                  onRestored={restored} onStale={stale} />
              )}
            </div>
          );
        })}
  </>;
}
