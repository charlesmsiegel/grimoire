import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type EntityScope, type ModuleDetail, type PCSummary } from "../api/client";
import { errorText } from "../api/errors";
import { THUMB } from "../api/thumbs";
import { Field } from "./Field";
import { useCreationRequest } from "./useCreationRequest";
import CreationWizard from "./CreationWizard";
import { Portrait } from "./Portrait";

/** The section root is now only an index and creator. Record reads and writes
 * belong to PCPage at the row's own URL, including newly created records. */
export function PCEditor({ scope, wid, recordHref, module = null }: {
  scope: EntityScope; wid: string;
  recordHref: (rid: string) => string; module?: ModuleDetail | null;
}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const creating = params.get("new") === "1";
  const [newName, setNewName] = useState("");
  const { busy: creatingBusy, begin: beginCreation } = useCreationRequest(
    creating ? `${scope.kind}:${scope.id}:${wid}` : null);
  useEffect(() => { setNewName(""); }, [creating, scope.kind, scope.id]);
  const worldScope = scope.kind === "world";
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  useEffect(() => {
    let active = true;
    api.listPCs(scope).then((list) => { if (active) setPCs(list); })
      .catch((err: unknown) => { if (active) setError(errorText(err)); });
    setWizardOpen(false);
    return () => { active = false; };
  }, [scope.kind, scope.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function newPC() {
    const name = newName.trim();
    if (!name || creatingBusy) return;
    const request = beginCreation();
    if (!request) return;
    setError(null);
    try {
      const { pc } = worldScope ? await api.createPC(wid, { name })
        : await api.createCampaignPC(scope.id, { name });
      if (request.current()) void navigate(recordHref(pc), { state: { newPC: true } });
    } catch (err: unknown) { if (request.current()) setError(errorText(err)); }
    finally { request.finish(); }
  }
  return <div className="editor">
    <div className="editor-list">
      <Link className="primary new" to="?new=1">+ New PC</Link>
      {worldScope && module && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters") &&
        <button className="subtle" onClick={() => setWizardOpen(true)}>+ New PC with sheet…</button>}
      {pcs.map((pc) => <Link key={pc.id} className="row" to={recordHref(pc.id)}>
        <span className="pc-row-portrait" aria-hidden><Portrait name={pc.name} focus={pc.avatar_focus}
          src={pc.has_avatar ? api.actorImageUrl(scope, "pcs", pc.id, pc.default_version, "avatar",
            { w: THUMB.row, v: pc.avatar_v }) : null} /></span>
        <span className="row-name">{pc.name}</span>
      </Link>)}
    </div>
    <div className="editor-body">
      {error && <div className="banner">{error}</div>}
      {creating ? <div className="form">
        <h3>New PC</h3>
        <Field label="PC name"><input type="text" value={newName} disabled={creatingBusy} onChange={(e) => setNewName(e.target.value)} /></Field>
        <div className="form-actions"><Link className="subtle" to="?">Cancel</Link>
          <button className="primary" disabled={creatingBusy || !newName.trim()} onClick={() => void newPC()}>Create PC</button>
        </div>
      </div> : wizardOpen && module && worldScope
        ? <CreationWizard scope={scope} kind="pcs" module={module}
            createRecord={(name) => api.createPC(wid, { name }).then((r) => r.pc)}
            deleteRecord={(id) => api.deletePC(scope, id).then(() => {})}
            onDone={(id) => { setWizardOpen(false); void navigate(recordHref(id), { state: { newPC: true } }); }}
            onCancel={() => setWizardOpen(false)} />
        : <div className="editor-empty">Select or create a PC.</div>}
    </div>
  </div>;
}
