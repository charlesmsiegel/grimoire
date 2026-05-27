import { useEffect, useRef } from "react";

import { Markdown } from "../../components/Markdown";
import type { ApiPost, ApiScene, PCEntry } from "../../api/campaign";
import { PostItem } from "./PostItem";
import type { PendingTurn, SceneImage } from "./usePlayState";

interface Props {
  posts: ApiPost[];
  pcs: PCEntry[];
  streaming: PendingTurn | null;
  images: Record<string, SceneImage>;
  campaignId?: string;
  scene?: ApiScene | null;
  hasMorePosts: boolean;
  onLoadMore: () => void;
  expressionsEnabledCharacters?: ReadonlySet<string>;
}

export function ScenePane({
  posts,
  pcs,
  streaming,
  images,
  campaignId,
  scene,
  hasMorePosts,
  onLoadMore,
  expressionsEnabledCharacters,
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
  }, [posts.length, streaming?.text.length]);

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
  return (
    <section className="scene-pane" aria-label="Scene posts" aria-live="polite">
      {hasMorePosts && <div ref={topSentinelRef} className="load-more-sentinel" />}
      {posts.length === 0 && !streaming && (
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
        />
      ))}
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
