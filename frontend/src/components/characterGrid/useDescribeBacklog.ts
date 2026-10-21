import { useCallback, useEffect, useState } from "react";
import { api, type EntityScope, type UndescribedImage } from "../../api/client";
import { useResetOnKey, type VisitFence } from "./useCharacterRoster";

/** The scope's backlog of images nobody has described: its count, for the
 *  toolbar's button, and the queue itself once the reader opens it. */
export function useDescribeBacklog(
  scope: EntityScope, { visit, liveVisit }: VisitFence, setError: (err: unknown) => void,
) {
  /** How long the describe backlog is, from its `?count=1` form; `null` until
   *  that lands, and after it fails -- either way no button. */
  const [undescribedCount, setUndescribedCount] = useState<number | null>(null);
  /** The backlog itself, fetched only when the reader opens it: non-null is
   *  "the queue is open". */
  const [describeQueue, setDescribeQueue] = useState<UndescribedImage[] | null>(null);
  const [describeLoading, setDescribeLoading] = useState(false);

  useResetOnKey(`${scope.kind}:${scope.id}`, () => {
    setUndescribedCount(null);
    setDescribeQueue(null);
  });

  const recountUndescribed = useCallback(() => {
    const from = scope;
    const at = visit;
    const current = () => liveVisit.current === at;
    Promise.resolve()
      .then(() => api.countUndescribedImages(from))
      .then((n) => { if (current()) setUndescribedCount(n); })
      .catch(() => { if (current()) setUndescribedCount(null); });
  }, [scope, visit, liveVisit]);

  useEffect(() => { recountUndescribed(); }, [recountUndescribed]);

  /** Open the describe queue: the backlog is fetched now, when somebody means
   *  to work through it, and not on every visit to label a button. The count
   *  is corrected from the list while it is at hand. */
  function openDescribe() {
    const from = scope;
    const at = visit;
    const current = () => liveVisit.current === at;
    setDescribeLoading(true);
    api.listUndescribedImages(from)
      .then((q) => {
        if (!current()) return;
        setDescribeQueue(q);
        setUndescribedCount(q.length);
      })
      .catch((err: unknown) => { if (current()) setError(err); })
      .finally(() => { if (current()) setDescribeLoading(false); });
  }

  /** Close the queue, and recount what it left behind. */
  function closeDescribe() {
    setDescribeQueue(null);
    recountUndescribed();
  }

  return {
    undescribedCount, describeQueue, describeLoading,
    openDescribe, closeDescribe, recountUndescribed,
  };
}
