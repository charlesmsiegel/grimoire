import { Link } from 'react-router-dom';
import type { useVNReply } from './useVNReply';

export function VNReply({ reply, playHref, canReply }: { reply: ReturnType<typeof useVNReply>; playHref: string; canReply: boolean }) {
  if (!canReply && !reply.locked && !reply.error && !reply.proposal) return null;
  return <form className="vn-reply" aria-label="Reply" onSubmit={event => { event.preventDefault(); void reply.send(); }}>
    {canReply && <><label htmlFor="vn-reply-text">Your reply</label>
    <div className="vn-reply-entry">
      <textarea id="vn-reply-text" rows={2} value={reply.text} disabled={reply.locked || reply.proposal}
        placeholder="What do you say or do?" onChange={event => reply.setText(event.target.value)}
        onKeyDown={event => {
          if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey
            || event.nativeEvent.isComposing || event.keyCode === 229) return;
          event.preventDefault();
          if (!event.repeat) void reply.send();
        }} />
      <button type="submit" disabled={reply.locked || reply.proposal || !reply.text.trim()}>Send reply</button>
    </div></>}
    {reply.locked && reply.canStop && <button type="button" disabled={reply.phase === 'stopping'} onClick={() => void reply.stop()}>Stop reply</button>}
    {reply.locked && <p role="status">{reply.phase === 'blocked' ? 'Reply status is unknown. Check before sending again.'
      : reply.phase === 'checking' ? 'Checking scene…' : reply.phase === 'stopping' ? 'Stopping reply…' : 'Waiting for the reply…'}</p>}
    {reply.error && <p role="alert">{reply.error}</p>}
    {reply.proposal && <p>A roll needs your decision. <Link to={playHref}>Resolve roll in play</Link></p>}
    {(reply.phase === 'blocked' || reply.proposal) && <button type="button" disabled={reply.locked && reply.phase !== 'blocked'} onClick={() => void reply.check()}>Check reply</button>}
  </form>;
}
