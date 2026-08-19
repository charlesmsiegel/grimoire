// The column while a review is open. It belongs to the review, not to the
// scene: what you are navigating is eighteen proposals, and the cast grid
// behind them is answering a question nobody is asking yet.
import { ColumnSection } from "../PageShell";
import type { SceneReview } from "./useSceneReview";

export default function ReviewColumn({ review }: { review: SceneReview }) {
  const {
    editRows, approvedCount, rejectedCount, undecidedCount, uncitedRows, lowRows, groupCounts,
    reviewSection: openSection, setReviewSection,
  } = review;
  return (
    <>
      <div className="column-section">
        <div className="eyebrow" style={{ padding: "0 16px" }}>Proposed</div>
        <h3 className="review-count">
          {editRows.length} {editRows.length === 1 ? "edit" : "edits"}
        </h3>
        <div className="review-tally">
          {approvedCount} approved · {rejectedCount} rejected · {undecidedCount} left
        </div>
        {/* Approved and rejected are both *judged*; the bar fills with the work
            done rather than with the work approved, or rejecting everything
            would read as making no progress. */}
        <div className="review-bar" role="img"
             aria-label={`${approvedCount + rejectedCount} of ${editRows.length} judged`}>
          <span className="review-bar-approved"
                style={{ width: `${(approvedCount / Math.max(1, editRows.length)) * 100}%` }} />
          <span className="review-bar-rejected"
                style={{ width: `${(rejectedCount / Math.max(1, editRows.length)) * 100}%` }} />
        </div>
      </div>

      {/* The two drawers that hold the rows which did NOT arrive pre-approved.
          They cut across the stores deliberately: "what must I answer before I
          can save" is a different question from "what is this absorb claiming
          about her state", and it is the one with a deadline. */}
      <ColumnSection label="Needs you">
        <button className={"column-row alert" + (openSection === "uncited" ? " active" : "")}
                onClick={() => setReviewSection("uncited")}>
          <span className="column-row-label">Uncited</span>
          <span className="column-row-count">{uncitedRows.length}</span>
        </button>
        {lowRows.length > 0 && (
          <button className={"column-row alert" + (openSection === "low" ? " active" : "")}
                  onClick={() => setReviewSection("low")}>
            <span className="column-row-label">Low confidence</span>
            <span className="column-row-count">{lowRows.length}</span>
          </button>
        )}
      </ColumnSection>

      <ColumnSection label="By store">
        {groupCounts.map((g) => (
          <button key={g.key}
                  className={"column-row" + (openSection === g.key ? " active" : "")}
                  onClick={() => setReviewSection(g.key)}>
            <span className="column-row-label">{g.label}</span>
            <span className="column-row-count">{g.n}</span>
          </button>
        ))}
      </ColumnSection>
    </>
  );
}
