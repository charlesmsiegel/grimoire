import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EntityScope, type ModuleDetail, type PCSummary } from "../api/client";
import { errorText } from "../api/errors";
import { THUMB } from "../api/thumbs";
import CreationWizard from "./CreationWizard";
import { Portrait } from "./Portrait";

/** The section root is now only an index and creator. Record reads and writes
 * belong to PCPage at the row's own URL, including newly created records. */
export function PCEditor({ scope, wid, recordHref, module = null }: {
  scope: EntityScope; wid: string;
  recordHref: (rid: string) => string; module?: ModuleDetail | null;
}) {
  const navigate = useNavigate();
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
    const name = window.prompt("New PC name?")?.trim();
    if (!name) return;
    try {
      const { pc } = worldScope ? await api.createPC(wid, { name })
        : await api.createCampaignPC(scope.id, { name });
      navigate(recordHref(pc), { state: { newPC: true } });
    } catch (err: unknown) { setError(errorText(err)); }
  }
  return <div className="editor">
    <div className="editor-list">
      <button className="primary new" onClick={() => void newPC()}>+ New PC</button>
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
      {wizardOpen && module && worldScope
        ? <CreationWizard scope={scope} kind="pcs" module={module}
            createRecord={(name) => api.createPC(wid, { name }).then((r) => r.pc)}
            deleteRecord={(id) => api.deletePC(scope, id).then(() => {})}
            onDone={(id) => { setWizardOpen(false); navigate(recordHref(id), { state: { newPC: true } }); }}
            onCancel={() => setWizardOpen(false)} />
        : <div className="editor-empty">Select or create a PC.</div>}
    </div>
  </div>;
}
