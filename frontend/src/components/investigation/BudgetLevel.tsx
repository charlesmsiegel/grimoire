/** Where the campaign stands against its budget, shown where an investigation
 *  is started (roadmap 12 §7.1). An investigation over budget is never
 *  refused -- campaign budgets only warn, and measure a different column than
 *  a run's own spend ceiling -- so this line is how the reader starting one
 *  decides with the number in front of them.
 *
 *  Read once per campaign. A read that fails says so rather than going
 *  quiet, since its absence would read as "no budget". */
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { CampaignBudget } from "../../api/types";
import { money } from "../cost";

export function budgetSentence(b: CampaignBudget): string {
  if (b.level === "off") return "This campaign has no budget set.";
  const when = b.period === "total" ? "in total" : "this month";
  let text = `Campaign budget: ${money(b.spent_usd ?? 0)} of ${money(b.limit_usd)} spent ${when}`;
  // The figure is a floor whenever calls went unpriced; never pass it off as whole.
  const unpriced = b.unpriced_calls ?? 0;
  if (unpriced > 0) text += `, not counting ${unpriced} unpriced ${unpriced === 1 ? "call" : "calls"}`;
  text += ".";
  if (b.level === "over") text += " Over budget: an investigation still runs, and adds to it.";
  else if (b.level === "warn") text += " Approaching the budget.";
  return text;
}

export function BudgetLevel({ cid }: { cid: string }) {
  const [read, setRead] = useState<{ cid: string; data: CampaignBudget | "failed" } | null>(null);
  useEffect(() => {
    let live = true;
    api.getCampaignBudget(cid)
      .then((data) => { if (live) setRead({ cid, data }); })
      .catch(() => { if (live) setRead({ cid, data: "failed" }); });
    return () => { live = false; };
  }, [cid]);
  const here = read?.cid === cid ? read.data : null;
  if (here === null) return null;
  const level = here === "failed" ? "unknown" : here.level;
  return (
    <p className={"field-hint" + (level === "over" || level === "warn" ? " error" : "")}
       data-budget-level={level}>
      {here === "failed" ? "The campaign's budget could not be read." : budgetSentence(here)}
    </p>
  );
}
