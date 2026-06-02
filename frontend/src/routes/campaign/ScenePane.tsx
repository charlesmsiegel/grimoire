import { useEffect, useMemo, useRef } from "react";

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
  onPostDeleted?: (deletedIds: string[], warnings: string[]) => void;
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
  const paneRef = useRef<HTMLElement>(null);
  const topSentinelRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);

  // The most recent player-authored post — the one a new turn responds to —
  // and how many player posts exist (a new turn increments this).
  const latestUserPostId = useMemo(() => {
    let id: string | null = null;
    for (const p of posts) {
      if (p.is_player) id = p.id;
    }
    return id;
  }, [posts]);
  const userPostCount = useMemo(
    () => posts.reduce((n, p) => (p.is_player ? n + 1 : n), 0),
    [posts],
  );
  const postCount = posts.length;
  const streamingActive = !!streaming;

  // When a new turn begins, scroll the just-submitted user post to the top of
  // the main window so the response streams in below it (read top-to-bottom,
  // no back-scrolling). On scene load we instead jump to the most recent post.
  //
  // The trigger is a genuine *increase* in the user-post count — never a bare
  // id change — so regenerate, refetch, pagination and deletes don't scroll,
  // and a missed seed fails safe (no scroll) rather than yanking to an existing
  // post. ``scrollIntoView`` aligns the post within whichever ancestor scrolls
  // (the inner pane and the outer main column are both scroll containers).
  const initializedSceneRef = useRef<string | null | undefined>(undefined);
  const prevUserPostCountRef = useRef<number | null>(null);

  // Scroll diagnostics: log whatever scrolls the pane or main column to the
  // top (with a stack trace), plus each scroll-effect run below. Kept in to
  // diagnose intermittent "jump to top" reports; cheap and console-only.
  useEffect(() => {
    const pane = paneRef.current;
    const main = pane?.closest<HTMLElement>(".app-main") ?? null;
    const onScroll = (label: string) => (e: Event) => {
      const top = (e.target as HTMLElement).scrollTop;
      if (top <= 4) {
        console.log(`[scroll] ${label} scrolled to top`, top, new Error().stack);
      }
    };
    const ps = onScroll("pane");
    const ms = onScroll("app-main");
    pane?.addEventListener("scroll", ps);
    main?.addEventListener("scroll", ms);
    return () => {
      pane?.removeEventListener("scroll", ps);
      main?.removeEventListener("scroll", ms);
    };
  }, []);

  useEffect(() => {
    const sceneId = scene?.id ?? null;
    const main = paneRef.current?.closest<HTMLElement>(".app-main") ?? null;
    console.log("[scroll] effect", {
      sceneId,
      initialized: initializedSceneRef.current,
      postCount,
      userPostCount,
      prevUserCount: prevUserPostCountRef.current,
      latestUserPostId,
      awaitingResponse,
      streamingActive,
      paneScrollTop: paneRef.current?.scrollTop,
      mainScrollTop: main?.scrollTop,
    });

    // First populated render for this scene: show the latest post and record
    // the user-post count so existing posts aren't treated as newly submitted.
    if (sceneId !== initializedSceneRef.current) {
      if (postCount === 0) return;
      initializedSceneRef.current = sceneId;
      prevUserPostCountRef.current = userPostCount;
      console.log("[scroll] INIT -> bottom");
      const handle = requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
      });
      return () => cancelAnimationFrame(handle);
    }

    const grew =
      prevUserPostCountRef.current !== null && userPostCount > prevUserPostCountRef.current;
    prevUserPostCountRef.current = userPostCount;

    if (grew && latestUserPostId) {
      const targetId = latestUserPostId;
      console.log("[scroll] ANCHOR (count grew) ->", targetId);
      const handle = requestAnimationFrame(() => {
        paneRef.current
          ?.querySelector<HTMLElement>(`[data-post-id="${CSS.escape(targetId)}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      return () => cancelAnimationFrame(handle);
    }
  }, [scene?.id, postCount, userPostCount, latestUserPostId, awaitingResponse, streamingActive]);

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
    <section ref={paneRef} className="scene-pane" aria-label="Scene posts" aria-live="polite">
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
            // order_in_scene is 1-based, so posts after this one is
            // post_count - order_in_scene (no extra -1).
            scene ? Math.max(0, scene.post_count - post.order_in_scene) : undefined
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
