import { useEffect, useMemo, useRef, useState } from "react";

import { auxiliaryApi, type AuxiliaryResult } from "../../api/auxiliary";
import { CardIconBar, type CardIconAction } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { CharacterSprite } from "../../components/CharacterSprite";
import { Markdown } from "../../components/Markdown";
import { campaignApi, type ApiAlternate, type ApiPost, type PCEntry } from "../../api/campaign";
import { AuxPanel } from "./Auxiliary/AuxPanel";
import { observabilityApi, type TaskCostRow } from "../../api/observability";
import type { SceneImage } from "./usePlayState";

interface Props {
  post: ApiPost;
  pcs: PCEntry[];
  images: SceneImage[];
  isLatestModelPost?: boolean;
  campaignId?: string;
  presentCharacterRefs?: string[];
  expressionsEnabledCharacters?: ReadonlySet<string>;
  /** Called with this post's turn_id when a reroll request fails, so the parent
   *  can clear the streaming indicator for *this reroll's* stream (the WS
   *  alternate_added event only fires on success). */
  onRerollFailed?: (turnId: string) => void;
  /** Number of posts after this one in the scene (drives the confirm copy). */
  subsequentCount?: number;
  /** When true, the scene is closed — deletion is not offered. */
  sceneClosed?: boolean;
  /** Called after a successful delete with the ids the backend removed (so the
   *  caller can drop them from view and refresh the scene) and any warnings —
   *  e.g. derived state that could not be reverted — so the caller can surface
   *  that the delete was not fully clean. */
  onDeleted?: (deletedIds: string[], warnings: string[]) => void;
  /** Turn id whose cost should render on this (user) post. */
  costTurnId?: string;
}

const AUTHOR_LABELS: Record<ApiPost["author_kind"], string> = {
  pc: "PC",
  narrator: "Narrator",
  npc: "NPC",
  system: "System",
};

/** Turn a ref/slug like "worlds/sakura-high/characters/yui-natsume" into a
 *  display name ("Yui Natsume"). Used as a fallback when a post carries only a
 *  character ref (e.g. per-character split posts) and no resolved name. */
function prettifyRef(ref: string): string {
  const slug = ref.split("/").pop() ?? ref;
  return slug.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function authorName(post: ApiPost, pcs: PCEntry[]): string {
  if (post.author_kind === "system" && post.is_player) return "Direction";
  if (post.author_pc_ref) {
    const pc = pcs.find((p) => p.character_ref === post.author_pc_ref);
    return pc?.name ?? prettifyRef(post.author_pc_ref);
  }
  if (post.author_npc_ref) return prettifyRef(post.author_npc_ref);
  return AUTHOR_LABELS[post.author_kind];
}

function primaryCursor(alternates: ApiAlternate[], primaryId: string | null | undefined): number {
  if (!primaryId) return 0;
  const i = alternates.findIndex((a) => a.id === primaryId);
  return i < 0 ? 0 : i;
}

function fmtUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function PostItem({
  post,
  pcs,
  images,
  isLatestModelPost = false,
  campaignId,
  presentCharacterRefs = [],
  expressionsEnabledCharacters,
  onRerollFailed,
  subsequentCount,
  sceneClosed = false,
  onDeleted,
  costTurnId,
}: Props) {
  const name = authorName(post, pcs);
  const isDirection = post.author_kind === "system" && post.is_player;
  const alternates = useMemo(() => post.alternates ?? [], [post.alternates]);
  const initialCursor = useMemo(
    () => primaryCursor(alternates, post.primary_alternate_id),
    [alternates, post.primary_alternate_id],
  );
  const [cursor, setCursor] = useState(initialCursor);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [guidedHint, setGuidedHint] = useState<string | null>(null);

  const [auxResult, setAuxResult] = useState<AuxiliaryResult | null>(null);
  const [auxBusy, setAuxBusy] = useState(false);
  const [auxError, setAuxError] = useState<string | null>(null);
  type AuxForm =
    | { kind: "translate"; targetLanguage: string }
    | { kind: "continue"; characterRef: string };
  const [auxForm, setAuxForm] = useState<AuxForm | null>(null);
  const [lastAuxAction, setLastAuxAction] = useState<(() => Promise<AuxiliaryResult>) | null>(null);

  const [editDraft, setEditDraft] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [bodyOverride, setBodyOverride] = useState<string | null>(null);

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const canDelete = !!campaignId && !sceneClosed;

  const isModelPost = post.author_kind !== "pc" && !post.is_player;
  const authorRef = post.author_npc_ref ?? post.author_pc_ref ?? null;
  const continueCandidates = useMemo(() => {
    if (authorRef) return [authorRef];
    return presentCharacterRefs.length > 0 ? [...presentCharacterRefs] : [];
  }, [authorRef, presentCharacterRefs]);

  const showStrip = alternates.length > 1;
  const current = alternates[cursor];
  const canMutate = isLatestModelPost && !!campaignId;
  const canEdit = !!campaignId;
  const canRegenerate = canMutate;
  const canContinue = !!campaignId && isModelPost && continueCandidates.length > 0;
  const displayBody = bodyOverride ?? post.body;

  async function saveEdit() {
    if (!campaignId || editDraft === null) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const updated = await campaignApi.editPostBody(campaignId, post.scene_id, post.id, editDraft);
      setBodyOverride(updated.body);
      setEditDraft(null);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }

  async function doDelete() {
    if (!campaignId) return;
    setDeleteBusy(true);
    setError(null);
    try {
      const result = await campaignApi.deletePost(campaignId, post.scene_id, post.id);
      onDeleted?.(result.deleted_post_ids, result.warnings);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleteBusy(false);
    }
  }

  async function runAux(action: () => Promise<AuxiliaryResult>) {
    if (!campaignId) return;
    setAuxBusy(true);
    setAuxError(null);
    try {
      const result = await action();
      setAuxResult(result);
      setAuxForm(null);
    } catch (e) {
      setAuxError(e instanceof Error ? e.message : String(e));
    } finally {
      setAuxBusy(false);
    }
  }

  async function runContinue(characterRef: string) {
    if (!campaignId || !characterRef) return;
    const action = () => auxiliaryApi.continueAs(campaignId, characterRef, post.id);
    setLastAuxAction(() => action);
    await runAux(action);
  }

  async function runTranslate(targetLanguage: string) {
    if (!campaignId || !targetLanguage.trim()) return;
    const lang = targetLanguage.trim();
    const action = () => auxiliaryApi.translate(campaignId, post.body, lang);
    setLastAuxAction(() => action);
    await runAux(action);
  }

  const speakerRef = post.author_pc_ref ?? post.author_npc_ref ?? null;
  const speakerAssetId = speakerRef ? (speakerRef.split("/").pop() ?? speakerRef) : null;
  const speakerExpressionsEnabled =
    !!speakerAssetId && (expressionsEnabledCharacters?.has(speakerAssetId) ?? false);
  const showSprite =
    !!campaignId && !!speakerAssetId && post.author_kind !== "system" && speakerExpressionsEnabled;

  async function call(action: () => Promise<unknown>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
      return false;
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

  async function regenerate(steeringHint?: string): Promise<boolean> {
    if (!canMutate) return false;
    const opts = steeringHint?.trim() ? { steering_hint: steeringHint.trim() } : undefined;
    const ok = await call(() =>
      campaignApi.regeneratePost(campaignId!, post.scene_id, post.id, opts),
    );
    // On success the WS alternate_added event clears the streaming indicator;
    // on failure no such event arrives, so clear it here or the UI stays stuck
    // showing "streaming". A reroll streams under this post's turn_id, so pass
    // it up to scope the clear to this reroll and not some other live turn.
    if (!ok) onRerollFailed?.(post.turn_id);
    return ok;
  }

  return (
    <article
      data-post-id={post.id}
      className={`post ${isDirection ? "post-direction" : `post-${post.author_kind}`}`}
      aria-label={isDirection ? "Direction" : `Post by ${name}`}
    >
      <header className="post-header">
        {showSprite && speakerAssetId && campaignId && (
          <CharacterSprite
            campaignId={campaignId}
            characterId={speakerAssetId}
            characterName={name}
            asOfTurn={post.turn_id}
            size="sm"
            expressionsEnabled
          />
        )}
        <span className="post-author">{name}</span>
        <span className="post-author-kind">{AUTHOR_LABELS[post.author_kind]}</span>
        {costTurnId && <CostLabel turnId={costTurnId} />}
        <time className="post-time" dateTime={post.created_at}>
          {new Date(post.created_at).toLocaleTimeString()}
        </time>
      </header>
      {editDraft === null ? (
        isDirection && !displayBody.trim() ? (
          <p className="post-body post-body-continue">(Continue)</p>
        ) : (
          <Markdown className="post-body">{displayBody}</Markdown>
        )
      ) : (
        <form
          className="post-edit-form"
          onSubmit={(e) => {
            e.preventDefault();
            void saveEdit();
          }}
        >
          <textarea
            className="post-edit-textarea"
            value={editDraft}
            onChange={(e) => setEditDraft(e.target.value)}
            rows={Math.max(8, Math.min(40, editDraft.split("\n").length + 2))}
            aria-label="Edit post markdown"
            autoFocus
          />
          <div className="post-edit-actions">
            <button type="submit" disabled={editBusy}>
              {editBusy ? "Saving..." : "Save"}
            </button>
            <button
              type="button"
              disabled={editBusy}
              onClick={() => {
                setEditDraft(null);
                setEditError(null);
              }}
            >
              Cancel
            </button>
          </div>
          {editError && (
            <p className="post-edit-error" role="alert">
              {editError}
            </p>
          )}
        </form>
      )}
      {images.length > 0 && (
        <ul className="post-images" aria-label="Generated images">
          {images.map((img) => (
            <li key={img.id}>
              <img src={img.url} alt={img.prompt || "Generated scene image"} loading="lazy" />
            </li>
          ))}
        </ul>
      )}
      {campaignId && editDraft === null && (
        <CardIconBar
          actions={[
            ...(canEdit
              ? [
                  {
                    key: "edit",
                    icon: "✎",
                    label: "Edit post",
                    onClick: () => setEditDraft(displayBody),
                  } satisfies CardIconAction,
                ]
              : []),
            ...(canRegenerate
              ? [
                  {
                    key: "regenerate",
                    icon: "🔄",
                    label: "Regenerate post",
                    disabled: busy,
                    onClick: () => void regenerate(),
                  } satisfies CardIconAction,
                  {
                    key: "guided-regenerate",
                    icon: "🎯",
                    label: "Guided regenerate",
                    disabled: busy,
                    onClick: () => setGuidedHint(""),
                  } satisfies CardIconAction,
                ]
              : []),
            ...(canContinue
              ? [
                  {
                    key: "continue",
                    icon: "➤",
                    label: "Continue",
                    disabled: auxBusy,
                    onClick: () => {
                      if (continueCandidates.length === 1) {
                        void runContinue(continueCandidates[0]!);
                      } else {
                        setAuxForm({ kind: "continue", characterRef: continueCandidates[0]! });
                      }
                    },
                  } satisfies CardIconAction,
                ]
              : []),
            {
              key: "translate",
              icon: "🌐",
              label: "Translate this post",
              disabled: auxBusy,
              onClick: () => setAuxForm({ kind: "translate", targetLanguage: "" }),
            } satisfies CardIconAction,
            ...(canDelete
              ? [
                  deleteAction({
                    onClick: () => setConfirmingDelete(true),
                    label: "Delete post",
                    busy: deleteBusy,
                  }),
                ]
              : []),
          ]}
        />
      )}
      {confirmingDelete && campaignId && (
        <div className="post-delete-confirm" role="alertdialog" aria-label="Confirm delete">
          <p className="post-delete-warning">
            Delete this post
            {subsequentCount && subsequentCount > 0
              ? ` and the ${subsequentCount} following ${
                  subsequentCount === 1 ? "post" : "posts"
                } in this scene`
              : ""}
            ? Facts and changes derived from {subsequentCount ? "them" : "it"} will be reverted.
            This cannot be undone.
          </p>
          <div className="post-delete-actions">
            {/* eslint-disable-next-line local/no-bespoke-delete -- confirm-dialog action, not a card control */}
            <button
              type="button"
              className="post-delete-confirm-btn"
              aria-label="Confirm delete"
              disabled={deleteBusy}
              onClick={() => void doDelete()}
            >
              {deleteBusy ? "Deleting..." : "Delete"}
            </button>
            <button
              type="button"
              aria-label="Cancel delete"
              disabled={deleteBusy}
              onClick={() => setConfirmingDelete(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {guidedHint !== null && campaignId && (
        <form
          className="post-guided-form"
          onSubmit={(e) => {
            e.preventDefault();
            const hint = guidedHint;
            void regenerate(hint).then((ok) => {
              if (ok) setGuidedHint(null);
            });
          }}
        >
          <input
            type="text"
            value={guidedHint}
            onChange={(e) => setGuidedHint(e.target.value)}
            placeholder="What should the response contain?"
            aria-label="Guided regenerate hint"
            autoFocus
          />
          <button type="submit" disabled={busy || !guidedHint.trim()}>
            {busy ? "Regenerating..." : "Regenerate"}
          </button>
          <button type="button" onClick={() => setGuidedHint(null)} disabled={busy}>
            Cancel
          </button>
        </form>
      )}
      {auxForm?.kind === "continue" && campaignId && (
        <form
          className="post-continue-form"
          onSubmit={(e) => {
            e.preventDefault();
            void runContinue(auxForm.characterRef);
          }}
        >
          <select
            value={auxForm.characterRef}
            onChange={(e) => setAuxForm({ ...auxForm, characterRef: e.target.value })}
            aria-label="Character to continue as"
          >
            {continueCandidates.map((ref) => (
              <option key={ref} value={ref}>
                {pcs.find((p) => p.character_ref === ref)?.name ?? ref}
              </option>
            ))}
          </select>
          <button type="submit" disabled={auxBusy}>
            {auxBusy ? "Continuing..." : "Continue"}
          </button>
          <button type="button" onClick={() => setAuxForm(null)} disabled={auxBusy}>
            Cancel
          </button>
        </form>
      )}
      {auxForm?.kind === "translate" && campaignId && (
        <form
          className="post-translate-form"
          onSubmit={(e) => {
            e.preventDefault();
            void runTranslate(auxForm.targetLanguage);
          }}
        >
          <input
            type="text"
            value={auxForm.targetLanguage}
            onChange={(e) => setAuxForm({ ...auxForm, targetLanguage: e.target.value })}
            placeholder="Target language (e.g. French)"
            aria-label="Target language"
            list={`translate-langs-${post.id}`}
            autoFocus
          />
          <datalist id={`translate-langs-${post.id}`}>
            <option value="French" />
            <option value="Spanish" />
            <option value="German" />
            <option value="Japanese" />
            <option value="Latin" />
            <option value="Plain English" />
          </datalist>
          <button type="submit" disabled={auxBusy || !auxForm.targetLanguage.trim()}>
            {auxBusy ? "Translating..." : "Translate"}
          </button>
          <button type="button" onClick={() => setAuxForm(null)} disabled={auxBusy}>
            Cancel
          </button>
        </form>
      )}
      {auxError && (
        <p className="post-aux-error" role="alert">
          {auxError}
        </p>
      )}
      {error && (
        <p className="post-error" role="alert">
          {error}
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
            if (lastAuxAction) {
              void runAux(lastAuxAction);
            }
          }}
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
          {!isLatestModelPost && (
            <span className="chevron-hint" role="note">
              Switching alternates is only available on the latest post.
            </span>
          )}
        </div>
      )}
    </article>
  );
}

function CostLabel({ turnId }: { turnId: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [total, setTotal] = useState<string | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          observer.disconnect();
          observabilityApi
            .turnCosts(turnId)
            .then((rows: TaskCostRow[]) => {
              if (cancelled) return;
              const sum = rows.reduce((acc, r) => acc + r.total_usd, 0);
              if (sum > 0) setTotal(fmtUsd(sum));
            })
            .catch(() => {});
        }
      },
      { threshold: 0 },
    );
    observer.observe(el);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [turnId]);

  if (!total) {
    return <span ref={ref} className="post-cost" />;
  }
  return (
    <span ref={ref} className="post-cost" aria-label="Turn cost">
      {total}
    </span>
  );
}
