import { useRef, useState } from "react";
import type { ProposalRecord } from "../../api/client";
import {
  api
} from "../../api/client";
import { liveProposal } from "./campaignShared";

export function useProposalState() {
  // Orders writes to the chip, because the proposal is read from two places
  // that can answer out of order. `selectScene` fires a read and does not await
  // it; the post-cancel `settleProposal` fires a later one deliberately. Making
  // the fresh read bypass the shared promise (#95) was not enough on its own —
  // review caught that it still left the older read free to resolve afterwards
  // and put its pre-flush `null` back over the record that had just been
  // settled, with the poll already finished. So: every write bumps this, and a
  // read may only apply while its own bump is still the newest.
  const proposalSeqRef = useRef(0);
  // a pending/resolved roll-proposal record surfaced by the model mid-stream
  // or rehydrated on scene select; RollProposal only renders pending/resolved.
  const [proposal, setProposal] = useState<ProposalRecord | null>(null);

  // A write the caller knows the answer to — a live SSE proposal, or a clear
  // the player's own action implies. Retires every read still in flight, since
  // all of them were issued against a world older than this.
  function setProposalNow(record: ProposalRecord | null) {
    proposalSeqRef.current += 1;
    setProposal(record);
  }

  // A write from a read: `claim` is what `claimProposalRead` handed out when the
  // request went out, and it applies only if nothing has been written or read
  // since. Last *issued* wins rather than last resolved, which is the whole
  // point — the slow answer is by definition the one asked earliest.
  function claimProposalRead(): number {
    proposalSeqRef.current += 1;
    return proposalSeqRef.current;
  }

  function applyProposalRead(claim: number, record: ProposalRecord | null) {
    if (claim === proposalSeqRef.current) setProposal(liveProposal(record));
  }
  function refreshProposal(cid: string, id: string) {
    const claim = claimProposalRead();
    api.getRollProposal(cid, id).then((r) => applyProposalRead(claim, r.record))
      .catch(() => applyProposalRead(claim, null));
  }
  return { refreshProposal, setProposalNow, claimProposalRead, applyProposalRead, proposal };
}
