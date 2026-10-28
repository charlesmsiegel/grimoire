import { useEffect, useState } from "react";
import type { CampaignBudget, SceneMeta, SceneUsage, UsagePostBucket } from "../../api/client";
import {
  api
} from "../../api/client";
import {
  bucketPrice,
  headlineIsEstimate,
  UNPRICED
} from "../../components/cost";
import { useStableCallback } from "./campaignShared";

export function useSceneAccounting({ cid, transcriptIsActive, activeId, sceneListCid, scenes, ctxKey, loaded }: {
  cid: string;
  transcriptIsActive: boolean;
  activeId: string | null;
  sceneListCid: string | null;
  scenes: SceneMeta[];
  ctxKey: number;
  loaded: { cid: string; sid: string; token: number; } | null;
}) {
  // The campaign's budget, and the level the reader has already been told about
  // (#153). Held as a level rather than a boolean so dismissing the 80% warning
  // does not also swallow the one that says the budget is gone: crossing into
  // `over` is a different fact, and it gets said again.
  //
  // Held with the campaign it was read for, because it is read later than it
  // used to be (see the effect that reads it): between a campaign switch and
  // the new campaign's first transcript, the previous campaign's answer is
  // still the one on hand, and it must not be shown -- or handed to the Cost
  // section -- as this campaign's.
  const [budgetRead, setBudgetRead] =
    useState<{ cid: string; data: CampaignBudget } | null>(null);
  const budget = budgetRead && budgetRead.cid === cid ? budgetRead.data : null;
  const [budgetSeen, setBudgetSeen] = useState<string | null>(null);

  // Where this campaign stands against its budget (#153). Re-read on `ctxKey`,
  // which bumps once a turn -- the moment the number can have moved -- rather
  // than on a timer, so a campaign with no budget costs one request per scene
  // and the server does not even scan the ledger for it (`level: "off"`).
  //
  // Not read until the scene being opened has landed, though. Opening a scene
  // bumps `ctxKey` when its transcript arrives, so a read taken on the way in
  // -- the campaign changing, or this view mounting -- was answered again a
  // few hundred milliseconds later with nothing spent in between, and with a
  // budget set each of those is a scan of the ledger's period. `budgetFor` is
  // the campaign once its transcript is the one on screen, or once its scene
  // list has come back empty and there is no transcript to wait for; a
  // campaign switch or a first open therefore reads once, on the landing.
  //
  // Every rejection is swallowed to a cleared banner, the rule this whole view
  // follows: a budget lookup that failed is not a reason to put an error over a
  // turn that landed, and the Cost section in the inspector is where a reader
  // who actually wants the figure goes.
  const budgetFor = transcriptIsActive
    || (!activeId && sceneListCid === cid && scenes.length === 0) ? cid : null;
  useEffect(() => {
    if (!budgetFor) return;   // keep the last answer; it is scoped by `cid` anyway
    let live = true;
    api.getCampaignBudget(budgetFor)
      .then((b) => { if (live) setBudgetRead({ cid: budgetFor, data: b }); })
      .catch(() => { if (live) setBudgetRead(null); });
    return () => { live = false; };
  }, [budgetFor, ctxKey]);

  // What each player post in this scene has cost, keyed by transcript index
  // (#153), and the scene's whole usage read, which the inspector's Cost
  // section renders rather than asking for it a second time. Read on the same
  // `ctxKey` beat as the budget above.
  //
  // Held WITH the scene it was read for, and that is load-bearing rather than
  // tidy. This view deliberately keeps scene A's messages on screen until B's
  // transcript read lands, and the usage read is a separate request that could
  // land FIRST — so a plain index-keyed map put B's costs beside A's posts, and
  // left them there for good if B's transcript read then failed. Matching the
  // costs to `loaded` (the transcript actually rendered) rather than to `cid`/
  // `activeId` (the one requested) is what closes that window.
  //
  // And read FOR `loaded`, not for `activeId`, for the budget's reason: keyed
  // on the scene being requested it fired once on the way in and again when
  // the transcript's landing bumped `ctxKey`, and the first answer could only
  // ever be discarded by the gate below. A ledger scan is the slowest read on
  // this screen, so the one that cannot be shown is not asked for.
  //
  // Swallowed to "no chips" on failure, the rule this view follows everywhere:
  // an accounting read that failed is not a reason to put an error over a
  // transcript. The inspector's Cost section is where a reader who wants the
  // figure and an explanation goes.
  const [postCosts, setPostCosts] =
    useState<{
      cid: string; sid: string; byPost: Record<number, UsagePostBucket>;
      usage: SceneUsage
    } | null>(null);
  const usageCid = loaded?.cid ?? null;
  const usageSid = loaded?.sid ?? null;
  useEffect(() => {
    // Not cleared when nothing is loaded, nor on the way to a refetch.
    // `sceneCosts` below already refuses to render a read whose cid/sid is not
    // the transcript on screen, so clearing bought nothing -- and it made the
    // chips blink out and back on every `ctxKey` change, which is a real gap
    // under load: the suite's own `findByText` for a cost could open and close
    // inside it, and this test reds CI intermittently (#351's shape).
    if (!usageCid || !usageSid) return;
    let live = true;
    api.getSceneUsage(usageCid, usageSid)
      .then((u) => {
        if (!live) return;
        // The scene's own totals ride the read the chips already pay for.
        // They are summed over every ledger row in the scene's lifetime, so
        // they stay correct where the per-post list is capped or the scan is
        // clamped -- which is why the bar says the total and the gutter says
        // the breakdown, rather than the bar adding the gutter up.
        setPostCosts({
          cid: usageCid, sid: usageSid, usage: u,
          byPost: Object.fromEntries(u.by_post.map((b) => [b.post, b]))
        });
      })
      .catch(() => { if (live) setPostCosts(null); });
    return () => { live = false; };
  }, [usageCid, usageSid, ctxKey]);

  /** The chips, but only where they describe the transcript on screen. */
  const sceneCosts =
    postCosts && loaded && postCosts.cid === loaded.cid && postCosts.sid === loaded.sid
      ? postCosts : null;
  const postChips = sceneCosts?.byPost ?? null;
  /** What this scene has cost, as the scene head prints it.
   *
   *  `bucketPrice` rather than a bare figure: a scene whose calls nobody
   *  priced reads "not reported" instead of `$0.00`, and one the ledger has no
   *  rows for at all prints nothing -- "never generated against" is not "cost
   *  nothing". The design puts spend and modelled side by side here, and they
   *  stay side by side: `Footnotes` in the inspector is where the three are
   *  explained, and this is the one line of it the bar has room for. */
  const sceneSpend = sceneCosts && sceneCosts.usage.totals.calls > 0
    // `calls > 0` is the "never generated against" test, and it has to be made
    // here: `bucketPrice` answers `$0.00` for an all-zero bucket, which is
    // correct for a bucket of calls that genuinely cost nothing and wrong for
    // a scene that has no calls in it at all. The two are the same numbers and
    // different claims, and only the caller knows which one it is holding.
    ? bucketPrice(sceneCosts.usage.totals)
    : null;
  /** Whether that figure is spend -- `cost_usd`, what a provider charged. An
   *  estimate (`≈`) is real usage and not money anybody paid, so it is never
   *  labelled SPEND; neither is "not reported". */
  const sceneSpendIsSpend = sceneSpend !== null && sceneSpend !== UNPRICED
    && !!sceneCosts && !headlineIsEstimate(sceneCosts.usage.totals);

  // A dismissal belongs to the campaign it was made in, to the level it was
  // made at, and to the budget it was made against. Without the campaign, "I
  // have seen it" carried onto a different campaign's budget; without the limit
  // and period, raising a cap that had been waved off at 80% and then filling
  // it again said nothing, because the level had not changed since the
  // dismissal even though the number it describes had.
  useEffect(() => { setBudgetSeen(null); },
    [cid, budget?.limit_usd, budget?.period]);
  const onBudgetSaved = useStableCallback(
    (forCid: string, b: CampaignBudget) => setBudgetRead({ cid: forCid, data: b }));
  return { postChips, sceneSpend, budget, budgetSeen, setBudgetSeen, sceneCosts, onBudgetSaved, sceneSpendIsSpend };
}
