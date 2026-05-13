import { useEffect, useRef } from "react";

import { Markdown } from "../../components/Markdown";
import type { ApiPost, PCEntry } from "../../api/campaign";
import { PostItem } from "./PostItem";
import type { PendingTurn, SceneImage } from "./usePlayState";

interface Props {
  posts: ApiPost[];
  pcs: PCEntry[];
  streaming: PendingTurn | null;
  images: Record<string, SceneImage>;
}

export function ScenePane({ posts, pcs, streaming, images }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [posts.length, streaming?.text.length]);

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

  return (
    <section className="scene-pane" aria-label="Scene posts" aria-live="polite">
      {posts.length === 0 && !streaming && (
        <p className="scene-empty">No posts yet. Begin with a post below.</p>
      )}
      {posts.map((post) => (
        <PostItem key={post.id} post={post} pcs={pcs} images={byPost[post.id] ?? []} />
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
