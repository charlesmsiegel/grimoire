import type { ApiScene, OpenCommitment, PCEntry } from "../../api/campaign";
import { SourceBadge } from "./SourceBadge";

interface QuickActions {
  onRegenerate: () => void;
  onUndo: () => void;
  onEndScene: () => void;
  onSkipTime: () => void;
  onManualFact: () => void;
  busy: boolean;
}

interface Props {
  scene: ApiScene | null;
  pcs: PCEntry[];
  commitments: OpenCommitment[];
  actions: QuickActions;
}

export function SidePanel({ scene, pcs, commitments, actions }: Props) {
  const present = scene?.present_character_refs ?? [];
  const threads = scene?.threads_introduced ?? [];

  return (
    <aside className="side-panel" aria-label="Scene side panel">
      <section className="side-section">
        <h3>Present cast</h3>
        {present.length === 0 ? (
          <p className="side-empty">No cast tracked yet.</p>
        ) : (
          <ul className="side-list">
            {present.map((ref) => {
              const pc = pcs.find((p) => p.character_ref === ref);
              return (
                <li key={ref}>
                  {pc ? <strong>{pc.name}</strong> : <span>{ref}</span>}
                  <SourceBadge source={pc ? "library" : "library"} />
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="side-section">
        <h3>Active threads</h3>
        {threads.length === 0 ? (
          <p className="side-empty">No open threads.</p>
        ) : (
          <ul className="side-list">
            {threads.map((t, idx) => (
              <li key={`${idx}-${t.text}`}>{t.text}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="side-section">
        <h3>Open commitments</h3>
        {commitments.length === 0 ? (
          <p className="side-empty">No open commitments.</p>
        ) : (
          <ul className="side-list">
            {commitments.slice(0, 5).map((c) => (
              <li key={c.id}>{c.text}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="side-section">
        <h3>Capabilities</h3>
        <p className="side-empty">
          Per-PC capabilities surface here once a mechanics module is active. (Task 34 wires the
          full sheet view.)
        </p>
      </section>

      <section className="side-section">
        <h3>Quick actions</h3>
        <div className="side-actions">
          <button type="button" onClick={actions.onRegenerate} disabled={actions.busy}>
            Regenerate
          </button>
          <button type="button" onClick={actions.onUndo} disabled={actions.busy}>
            Undo turn
          </button>
          <button
            type="button"
            onClick={actions.onEndScene}
            disabled={actions.busy || !scene || scene.closed}
          >
            End scene
          </button>
          <button type="button" onClick={actions.onSkipTime} disabled={actions.busy || !scene}>
            Skip time
          </button>
          <button type="button" onClick={actions.onManualFact} disabled={actions.busy}>
            Manual fact
          </button>
        </div>
      </section>
    </aside>
  );
}
