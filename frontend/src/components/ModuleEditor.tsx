import type { ModuleDetail } from "../api/client";

// Placeholder shell (Task 13). Task 14 replaces this with the real
// section bodies (Manifest/Groups/Sheet types/Checks/Rules/Content/Layout/Theme)
// plus the dry-run save harness.
const SECTIONS = ["Manifest", "Groups", "Sheet types", "Checks", "Rules", "Content", "Layout", "Theme"];

export default function ModuleEditor({ onDone }: { detail: ModuleDetail; onDone: () => void }) {
  return (
    <div className="module-editor">
      <nav className="section-nav">
        {SECTIONS.map((s) => (
          <span key={s} className="chip">{s}</span>
        ))}
      </nav>
      <div className="form-actions">
        <button className="primary" onClick={onDone}>Done</button>
      </div>
    </div>
  );
}
