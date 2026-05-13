import { Markdown } from "../../components/Markdown";
import type { ApiPost, PCEntry } from "../../api/campaign";
import type { SceneImage } from "./usePlayState";

interface Props {
  post: ApiPost;
  pcs: PCEntry[];
  images: SceneImage[];
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

export function PostItem({ post, pcs, images }: Props) {
  const name = authorName(post, pcs);
  return (
    <article className={`post post-${post.author_kind}`} aria-label={`Post by ${name}`}>
      <header className="post-header">
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
    </article>
  );
}
