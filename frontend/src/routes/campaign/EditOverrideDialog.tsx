/**
 * Structured campaign-override editor (issue #601), shared between the Cast
 * detail panel and the World view's entity cards.
 *
 * The form starts from the cascade-resolved frontmatter and renders through
 * the same descriptor-driven EntityForm the library uses (generic
 * FrontmatterEditor for kinds without a descriptor). On save, only the keys
 * that changed are submitted; the backend shallow-merges them into the
 * existing override, so editing one key never drops earlier overrides.
 */

import { useState } from "react";

import { errorMessage } from "../../api/client";
import { ENTITY_KIND_PLURAL, type EntityKind } from "../../api/library";
import { viewsApi } from "../../api/views";
import { Dialog } from "../../components/Dialog";
import { EntityForm } from "../library/EntityForm";
import { getDescriptor } from "../library/entitySchemas";
import { ensureFrontmatter, type Frontmatter } from "../library/frontmatter";
import { FrontmatterEditor } from "../library/FrontmatterEditor";
import { overridePatch } from "./overridePatch";

interface EditOverrideDialogProps {
  campaignId: string;
  /** Singular entity kind: "character", "item", "location", … */
  kind: string;
  entityId: string;
  worldId: string;
  name?: string;
  /** Cascade-resolved frontmatter (campaign overlay applied) the form starts from. */
  initialFrontmatter: Record<string, unknown>;
  onClose: () => void;
  onSaved: () => void;
}

export function EditOverrideDialog({
  campaignId,
  kind,
  entityId,
  worldId,
  name,
  initialFrontmatter,
  onClose,
  onSaved,
}: EditOverrideDialogProps) {
  const [initial] = useState<Frontmatter>(() => ensureFrontmatter(initialFrontmatter));
  const [draft, setDraft] = useState<Frontmatter>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const descriptor = getDescriptor(kind);

  async function save() {
    const patch = overridePatch(initial, draft);
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (kind === "character") {
        await viewsApi.patchCharacterOverride(campaignId, entityId, {
          override: patch,
          world_id: worldId,
        });
      } else {
        const plural = ENTITY_KIND_PLURAL[kind as EntityKind] ?? `${kind}s`;
        await viewsApi.patchEntityOverride(campaignId, plural, entityId, {
          override: patch,
          world_id: worldId,
        });
      }
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={onClose} title={`Edit override — ${name || entityId}`}>
      <p className="muted">
        Campaign-local changes to <code>{entityId}</code> in world <code>{worldId}</code>. Only the
        fields you change are saved; the library entity itself is untouched.
      </p>
      <div className="override-dialog-form">
        {descriptor ? (
          <EntityForm
            descriptor={descriptor}
            worldId={worldId}
            frontmatter={draft}
            body=""
            onFrontmatterChange={setDraft}
            onBodyChange={() => {}}
            hideBody
          />
        ) : (
          <FrontmatterEditor value={draft} onChange={setDraft} hiddenKeys={["id"]} />
        )}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" onClick={() => void save()} disabled={busy}>
          {busy ? "Saving…" : "Save override"}
        </button>
      </div>
    </Dialog>
  );
}
