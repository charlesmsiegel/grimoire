import { useEffect, useRef } from "react";

import { Markdown } from "../../components/Markdown";
import type { ApiPost, ApiScene, PCEntry } from "../../api/campaign";
import { PostItem } from "./PostItem";
import type { PendingTurn, SceneImage } from "./usePlayState";

interface Props {
  posts: ApiPost[];
  pcs: PCEntry[];
  streaming: PendingTurn | null;
  awaitingResponse: boolean;
  images: Record<string, SceneImage>;
  campaignId?: string;
  scene?: ApiScene | null;
  hasMorePosts: boolean;
  onLoadMore: () => void;
  expressionsEnabledCharacters?: ReadonlySet<string>;
  /** Clears the stuck streaming indicator when a per-post reroll fails; called
   *  with the rerolled post's turn_id so the parent can scope the clear. */
  onRerollFailed?: (turnId: string) => void;
  onPostDeleted?: (deletedIds: string[]) => void;
}

export function ScenePane({
  posts,
  pcs,
  streaming,
  awaitingResponse,
  images,
  campaignId,
  scene,
  hasMorePosts,
  onLoadMore,
  expressionsEnabledCharacters,
  onRerollFailed,
  onPostDeleted,
}: Props) {
  const topSentinelRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);
  // rAF-coalesce scrolls so dozens of streamed tokens in a single frame
  // turn into one scroll, not dozens of queued animations. Also use
  // ``auto`` (instant) for streaming deltas — smooth animations stacked
  // and never settled. New posts still get a smooth scroll.
  const lastPostCountRef = useRef(posts.length);
  useEffect(() => {
    const isNewPost = posts.length !== lastPostCountRef.current;
    lastPostCountRef.current = posts.length;
    const handle = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({
        behavior: isNewPost ? "smooth" : "auto",
        block: "end",
      });
    });
    return () => cancelAnimationFrame(handle);
  }, [posts.length, streaming?.text.length, awaitingResponse]);

  useEffect(() => {
    const sentinel = topSentinelRef.current;
    if (!sentinel || !hasMorePosts) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loadingMoreRef.current) {
          loadingMoreRef.current = true;
          onLoadMore();
          setTimeout(() => {
            loadingMoreRef.current = false;
          }, 300);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMorePosts, onLoadMore]);

  const byPost: Record<string, SceneImage[]> = {};
  const orphans: SceneImage[] = [];
  for (const img of Object.values(images)) {
    if (img.post_id) {
      const bucket = byPost[img.post_id] ?? (byPost[img.post_id] = []);
      bucket.push(img);
    } else {
      orphans.push(img);
    }
  }

  // The latest model-authored post in scene order; alternates can only be
  // mutated on this post per the swipes-alternates design.
  let latestModelPostId: string | null = null;
  for (const p of posts) {
    if (p.author_kind !== "pc" && !p.is_player) {
      latestModelPostId = p.id;
    }
  }

  // Attribute each turn's cost to the user post that triggered it: the first
  // model post after a user post carries the turn id whose cost we display.
  // This shows cost once even when one LLM call is split into several posts.
  const costTurnByPost: Record<string, string> = {};
  let lastUserPostId: string | null = null;
  for (const p of posts) {
    if (p.is_player) {
      lastUserPostId = p.id;
    } else if (lastUserPostId && !(lastUserPostId in costTurnByPost) && p.turn_id) {
      costTurnByPost[lastUserPostId] = p.turn_id;
    }
  }
  return (
    <section className="scene-pane" aria-label="Scene posts" aria-live="polite">
      {hasMorePosts && <div ref={topSentinelRef} className="load-more-sentinel" />}
      {posts.length === 0 && !streaming && !awaitingResponse && (
        <p className="scene-empty">No posts yet. Begin with a post below.</p>
      )}
      {posts.map((post) => (
        <PostItem
          key={post.id}
          post={post}
          pcs={pcs}
          images={byPost[post.id] ?? []}
          isLatestModelPost={post.id === latestModelPostId}
          campaignId={campaignId}
          presentCharacterRefs={scene?.present_character_refs ?? []}
          expressionsEnabledCharacters={expressionsEnabledCharacters}
          onRerollFailed={onRerollFailed}
          subsequentCount={
            scene ? Math.max(0, scene.post_count - post.order_in_scene - 1) : undefined
          }
          sceneClosed={scene?.closed ?? false}
          onDeleted={onPostDeleted}
          costTurnId={costTurnByPost[post.id]}
        />
      ))}
      {awaitingResponse && !streaming && (
        <article
          className="post post-streaming post-pending"
          aria-label="Narrator response, working"
          aria-busy="true"
        >
          <header className="post-header">
            <span className="post-author">Narrator</span>
            <span className="post-author-kind">
              <span className="pending-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              thinking…
            </span>
          </header>
        </article>
      )}
      {streaming && (
        <article className="post post-streaming" aria-label="Narrator response, streaming">
          <header className="post-header">
            <span className="post-author">Narrator</span>
            <span className="post-author-kind streaming-pulse">streaming…</span>
          </header>
          <Markdown className="post-body">{streaming.text || ""}</Markdown>
        </article>
      )}
      {orphans.length > 0 && (
        <ul className="scene-orphan-images" aria-label="Recently generated images">
          {orphans.map((img) => (
            <li key={img.id}>
              <img src={img.url} alt="" loading="lazy" />
            </li>
          ))}
        </ul>
      )}
      <div ref={bottomRef} aria-hidden />
    </section>
  );
}
