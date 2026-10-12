import type { DescriptionRecovery, DescriptionState } from "../../api/types";
const caption = (text: string, described: boolean) => text || (described ? "No description (reviewed)" : "Not described");
export function DescriptionAlternatives({ conflicts }: { conflicts: DescriptionState["conflicts"] }) {
  const alternatives = Array.from(new Map(conflicts.map((row) => [JSON.stringify([row.from, row.text]), row])).entries());
  return <>{alternatives.map(([key, row]) => <p key={key}>From {row.from}: {row.text}</p>)}</>;
}
export function DescriptionConflictPanel({ snapshot, draft, onChoose, onCancel, busy }: {
  snapshot: Extract<DescriptionRecovery, { available: true }>; draft: string;
  onChoose: (choice: "shared" | "draft") => void; onCancel: () => void; busy: boolean;
}) {
  const shared = snapshot.write_target_description.scope === "shared";
  return <div className="art-description-conflict" role="alert">
    <p>The description changed. Review it before saving.</p>
    <dl><dt>Current effective description</dt><dd>{caption(snapshot.effective_description.text, snapshot.effective_description.described)}</dd>
      <dt>{shared ? "Shared description" : "Description for this placement"}</dt><dd>{caption(snapshot.write_target_description.text, snapshot.write_target_description.described)}</dd>
      <dt>Your draft</dt><dd>{draft || "No description"}</dd></dl>
    <DescriptionAlternatives conflicts={snapshot.write_target_description.conflicts} />
    <div className="form-actions">
      <button disabled={busy} onClick={() => onChoose("shared")}>{shared ? "Use shared value" : "Use this placement's value"}</button>
      <button disabled={busy} onClick={() => onChoose("draft")}>Keep my draft</button>
      <button disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </div>;
}
