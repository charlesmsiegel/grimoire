import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ModuleDetail, type ModuleSummary } from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "../components/Field";
import { useCreationRequest } from "../components/useCreationRequest";
import ModuleEditor from "../components/ModuleEditor";
import LibraryPage from "../components/LibraryPage";

export default function ModulesView() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const creating = params.get("new") === "1";
  const [newName, setNewName] = useState("");
  const { busy: creatingBusy, begin: beginCreation } = useCreationRequest(creating ? "module" : null);
  const [mods, setMods] = useState<ModuleSummary[]>([]);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [dupName, setDupName] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const listTicket = useRef(0);

  // Cancel can retire one creation while its list read is still pending and a
  // new creation asks again. An older snapshot must not hide the newer record.
  const reloadList = (canReport = () => true) => {
    const mine = ++listTicket.current;
    return api.listModules()
      .then((list) => { if (listTicket.current === mine) { setMods(list); setListError(null); } })
      .catch((e) => { if (listTicket.current === mine && canReport()) setListError(errorText(e)); });
  };

  useEffect(() => {
    reloadList();
  }, []);

  const select = (mid: string) => setParams({ id: mid });

  useEffect(() => {
    let live = true;
    setDetail(null);
    setError(null);
    setMode("view");
    setDupName(null);
    setNewName("");
    if (selected) api.readModule(selected)
      .then((d) => { if (live) setDetail(d); })
      .catch((err: unknown) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [selected, creating, detailRevision]);

  async function createNew() {
    const name = newName.trim();
    if (!name || creatingBusy) return;
    const request = beginCreation();
    if (!request) return;
    setError(null);
    try {
      const { id } = await api.createModule(name);
      // Cancel retires the form, but the server's new module still belongs in
      // the list. Only its navigation and failure report need the form fence.
      await reloadList(request.current);
      if (request.current()) select(id);
    } catch (err: unknown) {
      if (request.current()) setError(errorText(err));
    } finally { request.finish(); }
  }

  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file later
    if (!file) return;
    setError(null);
    try {
      const { id } = await api.importModule(file);
      await reloadList();
      select(id);
    } catch (err: unknown) {
      setError(errorText(err));
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
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  return (
    <LibraryPage>
      <div className="page-wide view-anim">
      <div className="eyebrow">The Library</div>
      <h1 className="screen-title">Modules</h1>
      <div className="editor">
        <div className="editor-list">
          <Link className="primary new" to="?new=1">+ New module</Link>
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
            <Link
              key={m.id}
              className={"row" + (detail?.id === m.id ? " active" : "")}
              to={`?${new URLSearchParams({ id: m.id })}`}
            >
              {m.name}
              {m.display_ok === false && <span className="field-hint"> · display issues</span>}
            </Link>
          ))}
        </div>
        <div className="editor-body">
          {listError && <div className="banner">Could not refresh modules: {listError}
            <button className="subtle" onClick={() => { void reloadList(); }}>Retry module list</button>
          </div>}
          {error && <div className="banner">{error}</div>}
          {creating ? <div className="form">
            <h3>New module</h3>
            <Field label="Module name"><input type="text" value={newName} disabled={creatingBusy}
              onChange={(e) => setNewName(e.target.value)} /></Field>
            <div className="form-actions">
              <Link className="subtle" to="?">Cancel</Link>
              <button className="primary" disabled={creatingBusy || !newName.trim()} onClick={() => void createNew()}>Create module</button>
            </div>
          </div> : mode === "edit" && detail ? (
            <ModuleEditor detail={detail} onDone={() => {
              setMode("view");
              setDetailRevision((n) => n + 1);
            }} />
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
                {detail.source !== "user" && <p className="field-hint">built-in — duplicate to customize</p>}
                <div className="form-actions module-actions">
                  {detail.source === "user" ? (
                    <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
                  ) : null}
                  <button className="subtle" onClick={startDuplicate}>Duplicate</button>
                  <a className="subtle" href={api.exportModuleUrl(detail.id)} download>Export</a>
                </div>
                {dupName !== null && (
                  <div className="side-section">
                    <h4>Duplicate as</h4>
                    <Field label="Copy name"><input
                      type="text"
                      value={dupName}
                      onChange={(e) => setDupName(e.target.value)}
                    /></Field>
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
                {Object.keys(detail.checks).some((id) => id !== "_defaults") && (
                  <div className="side-section">
                    <h4>Checks</h4>
                    <div className="chips">
                      {Object.entries(detail.checks).filter(([id]) => id !== "_defaults").map(([id, c]) => (
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
      </div>
    </LibraryPage>
  );
}
