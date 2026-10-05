import type { QuickReply } from "../api/types";
import { quickReplyAvailability, type QuickReplyContext } from "./quickReplies";

/** The composer's row of quick replies, in effective order. Nothing at all when
 *  no reply is offered. No hotkeys, deliberately: a bare key that posts or
 *  spends a generation would break the registry's rule, and each button is one
 *  tap with its whole text in its title. */
export function QuickReplyStrip({ replies, ctx, onRun }: {
  replies: QuickReply[];
  ctx: QuickReplyContext;
  onRun: (r: QuickReply) => void;
}) {
  const offered = replies
    .map((r) => ({ r, a: quickReplyAvailability(r, ctx) }))
    .filter(({ a }) => a.shown);
  if (offered.length === 0) return null;
  return (
    <div className="quick-replies" role="toolbar" aria-label="Quick replies">
      {offered.map(({ r, a }) => (
        <button key={r.id} type="button" className="quick-reply" title={a.title}
                disabled={a.disabled} onClick={() => onRun(r)}>
          {r.label}
        </button>
      ))}
    </div>
  );
}
