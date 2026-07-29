import { useCallback, useState } from "react";
import { api, type ModuleDetail } from "../api/client";
import { Field } from "./Field";
import { ErrorList, ImpactConfirm, useModuleDryRun, type SaveFn } from "./moduleEditShared";
import { GroupsSection, SheetTypesSection } from "./ModuleSchemaEditor";
import { ChecksSection, RulesSection } from "./ModuleRulesEditor";
import { ContentSection } from "./ModuleContentEditor";
import { LayoutSection, ThemeSection } from "./ModuleDisplayEditor";

const SECTIONS = ["Manifest", "Groups", "Sheet types", "Checks", "Rules",
                  "Content", "Layout", "Theme"] as const;
type Section = (typeof SECTIONS)[number];

export default function ModuleEditor({ detail, onDone }: {
  detail: ModuleDetail; onDone: () => void;
}) {
  const [pack, setPack] = useState(detail);
  const [section, setSection] = useState<Section>("Manifest");
  const reload = useCallback(
    () => api.readModule(pack.id).then(setPack), [pack.id]);
  return (
    <div className="module-editor">
      <div className="chips">
        {SECTIONS.map((s) => (
          <button key={s}
                  className={"chip" + (s === section ? " on" : "")}
                  onClick={() => setSection(s)}>{s}</button>
        ))}
        <button className="chip" onClick={onDone}>Done</button>
      </div>
      {section === "Manifest" && <ManifestSection pack={pack} reload={reload} />}
      {section === "Groups" && <GroupsSection pack={pack} reload={reload} />}
      {section === "Sheet types" && <SheetTypesSection pack={pack} reload={reload} />}
      {section === "Checks" && <ChecksSection pack={pack} reload={reload} />}
      {section === "Rules" && <RulesSection pack={pack} reload={reload} />}
      {section === "Content" && <ContentSection pack={pack} reload={reload} />}
      {section === "Layout" && <LayoutSection pack={pack} reload={reload} />}
      {section === "Theme" && <ThemeSection pack={pack} reload={reload} />}
    </div>
  );
}

function ManifestSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const m = pack.manifest;
  const [form, setForm] = useState({
    name: m.name ?? "", description: m.description ?? "",
    version: m.version ?? "", dice: m.dice ?? "", notes: m.notes ?? "",
  });
  const save: SaveFn = (dryRun) =>
    api.putModuleManifest(pack.id, { ...form, dry_run: dryRun });
  const dr = useModuleDryRun(save, [form]);
  return (
    <div className="detail-main">
      {dr.confirming && dr.result?.impact && (
        <ImpactConfirm impact={dr.result.impact}
                       onConfirm={() => { dr.setConfirming(false); void dr.commit(() => void reload()); }}
                       onCancel={() => dr.setConfirming(false)} />
      )}
      <ErrorList result={dr.result} />
      <Field label="Name">
        <input value={form.name}
               onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <Field label="Description">
        <input value={form.description}
               onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </Field>
      <Field label="Version">
        <input value={form.version}
               onChange={(e) => setForm({ ...form, version: e.target.value })} />
      </Field>
      <Field label="Dice">
        <input value={form.dice}
               onChange={(e) => setForm({ ...form, dice: e.target.value })} />
      </Field>
      <Field label="Notes">
        <textarea value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={dr.saving}
                onClick={() => void dr.requestSave(() => void reload())}>Save</button>
      </div>
    </div>
  );
}
