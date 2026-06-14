import { useEffect, useMemo, useRef } from "react";

import type { ApiPost } from "../../api/campaign";

interface Options {
  posts: ApiPost[];
  /** The active scene's id, or null/undefined while none is loaded. */
  sceneId: string | null | undefined;
  /** A turn is streaming or awaiting its first token. */
  turnActive: boolean;
  hasMorePosts: boolean;
  onLoadMore: () => void;
}

interface ScrollAnchor {
  /** Attach to the scrolling scene section. */
  paneRef: React.RefObject<HTMLElement>;
  /** Attach to the load-more sentinel at the top of the post list. */
  topSentinelRef: React.RefObject<HTMLDivElement>;
  /** Attach to the empty element at the bottom of the post list. */
  bottomRef: React.RefObject<HTMLDivElement>;
}

/**
 * Side-effect-only hook owning the scene pane's scroll behavior. It keeps the
 * most recent user post anchored to the top of the window across a turn and
 * preserves scroll position when older posts page in. The caller renders the
 * JSX and attaches the returned refs to the section, top sentinel, and bottom
 * marker; this hook holds all the imperative scroll logic.
 */
export function useScrollAnchor({
  posts,
  sceneId,
  turnActive,
  hasMorePosts,
  onLoadMore,
}: Options): ScrollAnchor {
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

  // Keep the most recent user post at the top of the main window across a turn,
  // so the response is read top-to-bottom with no back-scrolling. We anchor on
  // two edges:
  //   • submit — the user-post count *increases* (never a bare id change, so
  //     regenerate / refetch / pagination / deletes don't scroll, and a missed
  //     seed fails safe);
  //   • completion — the turn goes from streaming/awaiting back to idle. This
  //     re-asserts the anchor after the completion refetch settles, correcting
  //     any scroll reset it caused.
  // On scene load we instead jump to the most recent post. ``scrollIntoView``
  // aligns the post within whichever ancestor scrolls (the inner pane and the
  // outer main column are both scroll containers); re-anchoring when already at
  // the top is a no-op, so it doesn't fight a steady view.
  const initializedSceneRef = useRef<string | null | undefined>(undefined);
  const prevUserPostCountRef = useRef<number | null>(null);
  const prevTurnActiveRef = useRef(false);

  useEffect(() => {
    const wasActive = prevTurnActiveRef.current;
    prevTurnActiveRef.current = turnActive;

    // Scroll the latest user post to the top of the main window.
    const anchorTop = (id: string) => {
      const handle = requestAnimationFrame(() => {
        paneRef.current
          ?.querySelector<HTMLElement>(`[data-post-id="${CSS.escape(id)}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      return () => cancelAnimationFrame(handle);
    };

    // First populated render for this scene: show the latest post and record
    // the user-post count so existing posts aren't treated as newly submitted.
    if (sceneId !== initializedSceneRef.current) {
      if (postCount === 0) return;
      initializedSceneRef.current = sceneId;
      prevUserPostCountRef.current = userPostCount;
      const handle = requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
      });
      return () => cancelAnimationFrame(handle);
    }

    const grew =
      prevUserPostCountRef.current !== null && userPostCount > prevUserPostCountRef.current;
    prevUserPostCountRef.current = userPostCount;
    const completed = wasActive && !turnActive;

    if ((grew || completed) && latestUserPostId) {
      return anchorTop(latestUserPostId);
    }
  }, [sceneId, postCount, userPostCount, latestUserPostId, turnActive]);

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

  return { paneRef, topSentinelRef, bottomRef };
}
