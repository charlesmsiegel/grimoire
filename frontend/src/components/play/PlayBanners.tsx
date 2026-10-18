import { Link } from "react-router-dom";
import type { CampaignBudget } from "../../api/client";
import { errorText, isSetupFailure, setupFix } from "../../api/errors";
import { money } from "../cost";
import { ErrorNote } from "../ErrorNote";

/** A failure the play view is reporting.
 *
 *  `retryable` says whether the banner's Retry -- which GENERATES -- is the
 *  recovery: right for a turn that failed, wrong for anything else (retrying a
 *  failed alternate swap by generating appends a consecutive reply, which
 *  moves the slot and hides the very set the user was trying to cycle). A
 *  refusal of the model settings is never retryable: sending it again meets
 *  the same refusal, so `fail` clears the flag for one (`isSetupFailure`).
 *
 *  `from` names the operation that raised it. The banner is global but the
 *  reviews are not: an absorb review survives a scene switch, so a chat error
 *  raised in scene B can be on screen while scene A's review is still open.
 *  Without the tag, A's retry clearing "the previous attempt's error" would
 *  take B's unrelated failure -- and its generate-a-reply Retry -- with it.
 *
 *  `err` is the rejection itself, kept alongside its text so the banner can
 *  tell a model it could not reach from any other refusal (#210) -- the
 *  `network` kind lives on the rejection and nowhere in the sentence. Only
 *  `fail` has one to keep: half a dozen other raisers compose their own
 *  sentence out of nothing that failed, and set `text` alone. */
export type PlayError = { text: string; retryable: boolean; from?: string; err?: unknown };

const SETUP_LINK: Record<"/providers" | "/models", string> = {
  "/providers": "Set it on the Providers page →",
  "/models": "Choose another on the Models page →",
};

/** The page's banners, above the scene and above a review alike: whether a
 *  model can answer here, the campaign's budget, and the last failure.
 *
 *  They are the page's, not the scene's: `error` is deliberately global and
 *  tagged with `from` because a review outlives a scene switch, and a scoped
 *  retry that fails inside the review reports through it — so hiding it
 *  during a review would swallow the failure of the one button the review
 *  offers. The budget (#153) is the page's for a plainer reason: it is the
 *  campaign's, and a review spends nothing of its own.
 *
 *  One banner per fact. The readiness banner carries the campaign's own
 *  reason it cannot generate, and a failed turn whose failure IS that reason
 *  -- a missing key, a model that cannot do the job -- is not said again
 *  under it: it used to be, as a second banner with a Retry that sent the
 *  same request into the same refusal. */
export function PlayBanners({
  ready, problem, readyId, budget, budgetSeen, onBudgetSeen,
  error, onRetry, retryDisabled, onDismissError,
}: {
  /** Whether this campaign's scene turns have a model that can answer. */
  ready: boolean;
  /** The server's sentence for why not, when it gave one. */
  problem: string | null;
  /** The readiness banner's id, for whatever it explains (Send). */
  readyId: string;
  budget: CampaignBudget | null;
  /** The budget level the reader has already dismissed. */
  budgetSeen: string | null;
  onBudgetSeen: (level: string) => void;
  error: PlayError | null;
  onRetry: () => void;
  retryDisabled: boolean;
  onDismissError: () => void;
}) {
  const fix = error ? setupFix(error.err) : null;
  // A review can use a different route and fail while scene turns are already
  // unavailable. Only the scene route's own identical refusal repeats this fact.
  const restatesReady = !ready && error?.from === "scene" && isSetupFailure(error.err)
    && problem !== null && errorText(error.err ?? error.text) === problem;
  return (
    <>
      {!ready && (
        <div className="banner" id={readyId}>
          No model is ready to generate{problem ? ` — ${problem}` : ""}. Fix it on
          the <Link to="/models">Models page</Link> or
          the <Link to="/providers">Providers page</Link>.
        </div>
      )}
      {/* Dismissible because it describes a decision the reader may well
          have made on purpose, where the readiness banner describes something
          broken. Dismissing is per level, so spending past the cap says so
          again after the 80% warning was waved off. Below the connection
          notice and above the error, which is the order they matter in:
          nothing can be spent without a connection, and a turn that just
          failed outranks a running total. */}
      {budget && (budget.level === "warn" || budget.level === "over")
        && budgetSeen !== budget.level && (
        <div className="banner error-banner" role="alert">
          <span>
            {budget.level === "over" ? "Over budget: " : "Approaching budget: "}
            {money(budget.spent_usd ?? 0)} of {money(budget.limit_usd)} spent{" "}
            {budget.period === "total" ? "in total" : "this month"}
            {/* The figure is a floor whenever this is non-zero, and a banner
                that hid that would be understating the very number it exists
                to raise. */}
            {(budget.unpriced_calls ?? 0) > 0
              && ` — ${budget.unpriced_calls} unpriced `
                 + `${budget.unpriced_calls === 1 ? "call" : "calls"} not counted`}
            {". "}
            {/* Where to act on it. The control is in the inspector's Cost
                section, which is collapsed by default and so is exactly what a
                reader given this banner cannot find — a warning nobody can act
                on is the shape this whole view avoids. */}
            <span className="ctx-meta">Change it in the inspector’s Cost section.</span>
          </span>
          <button className="retry" onClick={() => onBudgetSeen(budget.level)}>Dismiss</button>
        </div>
      )}
      {/* Dismissible too: a failure that has been read is not a state to be
          stuck looking at until the next turn happens to clear it. */}
      {error && !restatesReady && (
        <div className="banner error-banner">
          {/* `?? text` for the raisers that composed a sentence rather than
              catching something: `ErrorNote` renders a plain string
              unchanged, so both kinds of banner go through one path. */}
          <span>
            <ErrorNote err={error.err ?? error.text} />
            {fix && <>{" "}<Link to={fix}>{SETUP_LINK[fix]}</Link></>}
          </span>
          <span className="banner-actions">
            {error.retryable && (
              <button className="retry" onClick={onRetry} disabled={retryDisabled}>Retry</button>
            )}
            <button className="dismiss" onClick={onDismissError}>Dismiss</button>
          </span>
        </div>
      )}
    </>
  );
}
