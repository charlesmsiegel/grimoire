import { useEffect, useRef, useState } from "react";
import { api, type ModuleDetail, type ModuleSummary } from "../api/client";
import ModuleEditor from "../components/ModuleEditor";

export default function ModulesView() {
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);
  const [dupName, setDupName] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const reloadList = () => api.listModules().then(setMods).catch((e) => setError(String(e)));

  useEffect(() => {
    reloadList();
  }, []);

  const select = (mid: string) => {
    setMode("view");
    setDupName(null);
    api.readModule(mid).then(setDetail).catch((e) => setError(String(e)));
  };

  async function createNew() {
    const name = window.prompt("New module name?")?.trim();
    if (!name) return;
    setError(null);
    try {
      const { id } = await api.createModule(name);
      await reloadList();
      const d = await api.readModule(id);
      setDetail(d);
      setMode("edit");
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file later
    if (!file) return;
    setError(null);
    try {
      await api.importModule(file);
      await reloadList();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  function startDuplicate() {
    if (!detail) return;
    const name = mods.find((m) => m.id === detail.id)?.name ?? detail.manifest.name;
    setDupName(`${name} copy`);
  }

  async function confirmDuplicate() {
    if (!detail || dupName === null) return;
    setError(null);
    try {
      const { id } = await api.duplicateModule(detail.id, dupName);
      setDupName(null);
      await reloadList();
      select(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={createNew}>+ New module</button>
        <button className="subtle new" onClick={() => fileRef.current?.click()}>+ Import</button>
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          aria-label="Import module zip"
          style={{ display: "none" }}
          onChange={onImportFile}
        />
        {mods.map((m) => (
          <button
            key={m.id}
            className={"row" + (detail?.id === m.id ? " active" : "")}
            onClick={() => select(m.id)}
          >
            {m.name}
            {m.display_ok === false && <span className="field-hint"> · display issues</span>}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "edit" && detail ? (
          <ModuleEditor detail={detail} onDone={() => { setMode("view"); select(detail.id); }} />
        ) : detail ? (
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
              <div className="form-actions">
                {detail.source === "user" ? (
                  <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
                ) : (
                  <span className="field-hint">built-in — duplicate to customize</span>
                )}
                <button className="subtle" onClick={startDuplicate}>Duplicate</button>
                <a className="row" href={api.exportModuleUrl(detail.id)} download>Export</a>
              </div>
              {dupName !== null && (
                <div className="side-section">
                  <h4>Duplicate as</h4>
                  <input
                    type="text"
                    value={dupName}
                    onChange={(e) => setDupName(e.target.value)}
                  />
                  <div className="form-actions">
                    <button className="subtle" onClick={() => setDupName(null)}>Cancel</button>
                    <button className="primary" onClick={confirmDuplicate}>Create copy</button>
                  </div>
                </div>
              )}
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
              {(Object.keys(detail.layout?.sheet_types ?? {}).length > 0
                || Object.keys(detail.theme ?? {}).length > 0
                || (detail.display_errors ?? []).length > 0) && (
                <div className="side-section">
                  <h4>Display</h4>
                  <div className="chips">
                    {Object.keys(detail.layout?.sheet_types ?? {}).map((tid) => (
                      <span key={tid} className="chip on">{tid} layout</span>
                    ))}
                    {Object.keys(detail.theme ?? {}).length > 0 && (
                      <span className="chip on">theme</span>
                    )}
                  </div>
                  {(detail.display_errors ?? []).map((e, i) => (
                    <div key={i} className="field-hint">{e.message}</div>
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
