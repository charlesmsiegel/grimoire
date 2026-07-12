import { useEffect, useState } from "react";
import { api, type ModuleDetail, type ModuleSummary } from "../api/client";

export default function ModulesView() {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listModules().then(setMods).catch((e) => setError(String(e)));
  }, []);

  const select = (mid: string) => {
    api.readModule(mid).then(setDetail).catch((e) => setError(String(e)));
  };

  return (
    <div className="editor">
      <div className="editor-list">
        {mods.map((m) => (
          <button
            key={m.id}
            className={"row" + (detail?.id === m.id ? " active" : "")}
            onClick={() => select(m.id)}
          >
            {m.name}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {detail ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{detail.manifest.name}</h3>
              {detail.manifest.description && <p>{detail.manifest.description}</p>}
              {Object.entries(detail.sheets.sheet_types).map(([tid, st]) => {
                if (!st || typeof st !== "object") return null;
                const groups = Array.isArray(st.groups) ? st.groups : [];
                const fields = Array.isArray(st.fields) ? st.fields : [];
                return (
                  <div key={tid} className="side-section">
                    <h4>
                      {st.label} <span className="field-hint">({st.kind})</span>
                    </h4>
                    <div className="chips">
                      {groups.map((g) => (
                        <span key={g} className="chip on">
                          {detail.sheets.groups[g]?.label ?? g}
                        </span>
                      ))}
                      {fields.map((f) => (
                        <span key={f.key} className="chip">
                          {f.label ?? f.key}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
            <aside className="detail-sidebar">
              <div className="side-section">
                <h4>Module</h4>
                <span className="chip on">{detail.source}</span>
                {detail.manifest.version && (
                  <span className="chip on">v{detail.manifest.version}</span>
                )}
                {detail.manifest.dice && (
                  <div className="field-hint">Dice: {detail.manifest.dice}</div>
                )}
              </div>
              {Object.keys(detail.checks).length > 0 && (
                <div className="side-section">
                  <h4>Checks</h4>
                  <div className="chips">
                    {Object.entries(detail.checks).map(([id, c]) => (
                      <span key={id} className="chip on">{c.label}</span>
                    ))}
                  </div>
                </div>
              )}
              {detail.rules.length > 0 && (
                <div className="side-section">
                  <h4>Rules</h4>
                  {detail.rules.map((r) => (
                    <div key={r.id} className="field-hint">
                      {r.id}
                      {r.always ? " · always" : ""}
                      {r.on_roll ? " · on roll" : ""}
                      {r.keys.length ? ` · keys: ${r.keys.join(", ")}` : ""}
                      {r.sheet_types.length ? ` · types: ${r.sheet_types.join(", ")}` : ""}
                    </div>
                  ))}
                </div>
              )}
              {detail.errors.length > 0 && (
                <div className="side-section">
                  <h4>Problems</h4>
                  {detail.errors.map((e, i) => (
                    <div key={i} className="field-hint">{e}</div>
                  ))}
                </div>
              )}
            </aside>
          </div>
        ) : (
          <p className="field-hint">Select a module to view its contract.</p>
        )}
      </div>
    </div>
  );
}
