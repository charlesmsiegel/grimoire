import { useMemo, useState } from "react";

import { auxiliaryApi, type AuxiliaryResult } from "../../api/auxiliary";
import { CharacterSprite } from "../../components/CharacterSprite";
import { Markdown } from "../../components/Markdown";
import { campaignApi, type ApiAlternate, type ApiPost, type PCEntry } from "../../api/campaign";
import { AuxPanel } from "./Auxiliary/AuxPanel";
import { RetconLauncher } from "./RetconLauncher";
import type { SceneImage } from "./usePlayState";

interface Props {
  post: ApiPost;
  pcs: PCEntry[];
  images: SceneImage[];
  /** True when this post is the latest model-authored post in its scene.
   * Swipes (chevron prev/next, regenerate, pin) are only allowed there per the
   * swipes-alternates design; otherwise the buttons are disabled and a tooltip
   * directs the user to Retcon / Fork. */
  isLatestModelPost?: boolean;
  /** Campaign id; required for alternate mutations and sprite resolution. Omit
   * to render read-only without sprites. */
  campaignId?: string;
  /** Number of model-authored posts that follow this one in the current scene.
   * Threaded through to the retcon launcher so it can show the fork nudge
   * when ≥ 5 (per 2026-05-19-retcon-design). */
  subsequentModelPostCount?: number;
}

const AUTHOR_LABELS: Record<ApiPost["author_kind"], string> = {
  pc: "PC",
  narrator: "Narrator",
  npc: "NPC",
  system: "System",
};

function authorName(post: ApiPost, pcs: PCEntry[]): string {
  if (post.author_pc_ref) {
    const pc = pcs.find((p) => p.character_ref === post.author_pc_ref);
    return pc?.name ?? post.author_pc_ref;
  }
  if (post.author_npc_ref) return post.author_npc_ref;
  return AUTHOR_LABELS[post.author_kind];
}

function primaryCursor(alternates: ApiAlternate[], primaryId: string | null | undefined): number {
  if (!primaryId) return 0;
  const i = alternates.findIndex((a) => a.id === primaryId);
  return i < 0 ? 0 : i;
}

export function PostItem({
  post,
  pcs,
  images,
  isLatestModelPost = false,
  campaignId,
  subsequentModelPostCount = 0,
}: Props) {
  const name = authorName(post, pcs);
  const alternates = useMemo(() => post.alternates ?? [], [post.alternates]);
  const initialCursor = useMemo(
    () => primaryCursor(alternates, post.primary_alternate_id),
    [alternates, post.primary_alternate_id],
  );
  const [cursor, setCursor] = useState(initialCursor);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retconOpen, setRetconOpen] = useState(false);
  const [rewriteInstr, setRewriteInstr] = useState<string | null>(null);
  const [lastRewriteInstr, setLastRewriteInstr] = useState<string>("");
  const [auxResult, setAuxResult] = useState<AuxiliaryResult | null>(null);
  const [auxBusy, setAuxBusy] = useState(false);
  const [auxError, setAuxError] = useState<string | null>(null);

  const showStrip = alternates.length > 1;
  const current = alternates[cursor];
  const canMutate = isLatestModelPost && !!campaignId;
  // Retcon is available on any model post (NOT gated to latest like swipes are).
  const canRetcon = !!campaignId && post.author_kind !== "pc" && !post.is_player;
  const canRewrite = canRetcon;

  async function runRewrite(instruction: string) {
    if (!campaignId || !instruction.trim()) return;
    setAuxBusy(true);
    setAuxError(null);
    setLastRewriteInstr(instruction);
    try {
      const result = await auxiliaryApi.rewritePost(campaignId, post.id, instruction);
      setAuxResult(result);
      setRewriteInstr(null);
    } catch (e) {
      setAuxError(e instanceof Error ? e.message : String(e));
    } finally {
      setAuxBusy(false);
    }
  }
  const speakerRef = post.author_pc_ref ?? post.author_npc_ref ?? null;
  const showSprite = !!campaignId && !!speakerRef && post.author_kind !== "system";

  async function call(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  async function gotoIndex(next: number) {
    if (!canMutate || next === cursor || next < 0 || next >= alternates.length) return;
    const target = alternates[next];
    if (!target) return;
    await call(async () => {
      await campaignApi.switchPrimaryAlternate(campaignId!, post.scene_id, post.id, target.id);
      setCursor(next);
    });
  }

  async function togglePin() {
    if (!canMutate || !current) return;
    const target = current;
    await call(() =>
      campaignApi.pinAlternate(campaignId!, post.scene_id, post.id, target.id, !target.pinned),
    );
  }

  async function regenerate() {
    if (!canMutate) return;
    await call(() => campaignApi.regeneratePost(campaignId!, post.scene_id, post.id));
  }

  return (
    <article className={`post post-${post.author_kind}`} aria-label={`Post by ${name}`}>
      <header className="post-header">
        {showSprite && speakerRef && campaignId && (
          <CharacterSprite
            campaignId={campaignId}
            characterId={speakerRef}
            characterName={name}
            asOfTurn={post.turn_id}
            size="sm"
          />
        )}
        <span className="post-author">{name}</span>
        <span className="post-author-kind">{AUTHOR_LABELS[post.author_kind]}</span>
        <time className="post-time" dateTime={post.created_at}>
          {new Date(post.created_at).toLocaleTimeString()}
        </time>
      </header>
      <Markdown className="post-body">{post.body}</Markdown>
      {images.length > 0 && (
        <ul className="post-images" aria-label="Generated images">
          {images.map((img) => (
            <li key={img.id}>
              <img src={img.url} alt="" loading="lazy" />
            </li>
          ))}
        </ul>
      )}
      {(canRetcon || canRewrite) && (
        <div className="post-actions">
          {canRetcon && (
            <button
              type="button"
              className="post-retcon"
              aria-label="Retcon this post"
              onClick={() => setRetconOpen(true)}
            >
              Retcon...
            </button>
          )}
          {canRewrite && (
            <button
              type="button"
              className="post-rewrite"
              aria-label="Rewrite this post"
              onClick={() => setRewriteInstr("")}
              disabled={auxBusy}
            >
              Rewrite...
            </button>
          )}
        </div>
      )}
      {rewriteInstr !== null && campaignId && (
        <form
          className="post-rewrite-form"
          onSubmit={(e) => {
            e.preventDefault();
            void runRewrite(rewriteInstr);
          }}
        >
          <input
            type="text"
            value={rewriteInstr}
            onChange={(e) => setRewriteInstr(e.target.value)}
            placeholder="How should this be rewritten?"
            aria-label="Rewrite instruction"
            autoFocus
          />
          <button type="submit" disabled={auxBusy || !rewriteInstr.trim()}>
            {auxBusy ? "Drafting…" : "Draft"}
          </button>
          <button type="button" onClick={() => setRewriteInstr(null)} disabled={auxBusy}>
            Cancel
          </button>
        </form>
      )}
      {auxError && (
        <p className="post-aux-error" role="alert">
          {auxError}
        </p>
      )}
      {auxResult && campaignId && (
        <AuxPanel
          campaignId={campaignId}
          result={auxResult}
          onAccepted={() => setAuxResult(null)}
          onDiscarded={() => setAuxResult(null)}
          onTryAgain={() => {
            setAuxResult(null);
            if (lastRewriteInstr) void runRewrite(lastRewriteInstr);
          }}
        />
      )}
      {retconOpen && campaignId && (
        <RetconLauncher
          campaignId={campaignId}
          postId={post.id}
          turnId={post.turn_id}
          originalText={post.body}
          subsequentModelPostCount={subsequentModelPostCount}
          onClose={() => setRetconOpen(false)}
        />
      )}
      {showStrip && (
        <div className="chevron-strip" role="group" aria-label="Alternates">
          <button
            type="button"
            className="chevron-prev"
            aria-label="Previous alternate"
            disabled={!canMutate || cursor === 0 || busy}
            onClick={() => gotoIndex(cursor - 1)}
          >
            ◀
          </button>
          <span className="chevron-count" aria-live="polite">
            {cursor + 1} of {alternates.length}
          </span>
          <button
            type="button"
            className="chevron-next"
            aria-label="Next alternate"
            disabled={!canMutate || cursor === alternates.length - 1 || busy}
            onClick={() => gotoIndex(cursor + 1)}
          >
            ▶
          </button>
          <button
            type="button"
            className="chevron-pin"
            aria-label={current?.pinned ? "Unpin alternate" : "Pin alternate"}
            aria-pressed={current?.pinned ? true : false}
            disabled={!canMutate || busy}
            onClick={togglePin}
          >
            {current?.pinned ? "📌" : "📍"}
          </button>
          <button
            type="button"
            className="chevron-regenerate"
            aria-label="Regenerate post"
            disabled={!canMutate || busy}
            onClick={regenerate}
          >
            🔄
          </button>
          {!isLatestModelPost && (
            <span className="chevron-hint" role="note">
              Switching alternates is only available on the latest post. Use Retcon to revise
              earlier posts or Fork for a new timeline.
            </span>
          )}
          {error && (
            <span className="chevron-error" role="alert">
              {error}
            </span>
          )}
        </div>
      )}
    </article>
  );
}
