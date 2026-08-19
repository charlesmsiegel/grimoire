// The scene, beside the review that judges it. Read-only and deliberately
// plain -- speaker then text -- rather than rendered through the transcript's
// own machinery, which carries edit, reroll and alternate controls that have
// no business in a review.
import type { Message } from "../../api/client";

export default function ReviewTranscript({ messages, speakerOf, isCited }: {
  messages: Message[];
  speakerOf: (m: Message) => string;
  /** The quote the reviewer asked to find, if it is in this post. */
  isCited: (text: string) => boolean;
}) {
  return (
    <aside className="review-transcript" aria-label="The scene, for checking">
      <div className="section-label">The scene, for checking</div>
      {messages.map((m, i) => (
        <div className={"review-post" + (isCited(m.content) ? " cited" : "")} key={i}>
          <div className="review-post-speaker">{speakerOf(m)}</div>
          <div className="review-post-body">{m.content}</div>
        </div>
      ))}
      {messages.length === 0 && (
        <p className="column-empty">This scene has no transcript to check against.</p>
      )}
    </aside>
  );
}
