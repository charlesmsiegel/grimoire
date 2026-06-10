import { useCallback, useState } from "react";

import { libraryApi, type CharacterVariant } from "../../api/library";
import { errorMessage } from "../../api/client";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { PromptDialog } from "../../components/PromptDialog";
import { AsyncBoundary } from "./AsyncBoundary";
import { slugify } from "../../lib/slugify";

interface Props {
  worldId: string;
  characterId: string;
}

/**
 * In-world variants of a character: each variant is a diff overlay file
 * (`characters/<id>/variants/<variant-id>.md`) holding only the frontmatter
 * fields that differ from the base, plus an optional replacement body. A
 * campaign picks one variant per character (Cast view → Variant).
 */
export function VariantsPanel({ worldId, characterId }: Props) {
  const { data, loading, error, reload } = useResource(
    useCallback(
      () => libraryApi.listCharacterVariants(worldId, characterId),
      [worldId, characterId],
    ),
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);

  async function handleCreate(label: string) {
    const id = slugify(label);
    if (!id) {
      setCreateErr("Label must contain at least one letter or digit.");
      return;
    }
    // PUT is an upsert — creating over an existing id would silently erase
    // that variant's diff and body, so collisions need an explicit edit.
    if (data?.some((v) => v.id === id)) {
      setCreateErr(`A variant with id "${id}" already exists — edit it instead.`);
      return;
    }
    setCreateBusy(true);
    setCreateErr(null);
    try {
      await libraryApi.putCharacterVariant(worldId, characterId, id, { label });
      setCreateOpen(false);
      reload();
    } catch (err) {
      setCreateErr(errorMessage(err));
    } finally {
      setCreateBusy(false);
    }
  }

  return (
    <section className="variants-panel">
      <p className="variants-intro">
        Alternate takes on <code>{characterId}</code> within this world. A variant stores only the
        fields it changes (plus an optional replacement description); campaigns pick one per
        character from the Cast view, and unselected campaigns keep the base.
      </p>
      <div className="variants-controls">
        <button type="button" onClick={() => setCreateOpen(true)}>
          New variant
        </button>
      </div>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No variants yet — the base card is the only take on this character."
        onRetry={reload}
      >
        <ul className="variants-list">
          {data?.map((variant) => (
            <li key={variant.id}>
              <VariantCard
                worldId={worldId}
                characterId={characterId}
                variant={variant}
                onChanged={reload}
              />
            </li>
          ))}
        </ul>
      </AsyncBoundary>
      {createOpen && (
        <PromptDialog
          open
          title="New variant"
          label="Variant label"
          placeholder="Young Alistair"
          hint="The id is the slugified label; the variant starts empty and overrides nothing until you add fields."
          confirmLabel="Create"
          busy={createBusy}
          error={createErr}
          onSubmit={(value) => void handleCreate(value)}
          onCancel={() => {
            setCreateOpen(false);
            setCreateErr(null);
          }}
        />
      )}
    </section>
  );
}

interface VariantCardProps {
  worldId: string;
  characterId: string;
  variant: CharacterVariant;
  onChanged: () => void;
}

function VariantCard({ worldId, characterId, variant, onChanged }: VariantCardProps) {
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState<{ busy: boolean; err: string | null } | null>(null);

  const diffKeys = Object.keys(variant.frontmatter).filter((k) => k !== "label");

  async function confirmDelete() {
    if (!deleting) return;
    setDeleting({ busy: true, err: null });
    try {
      await libraryApi.deleteCharacterVariant(worldId, characterId, variant.id);
      setDeleting(null);
      onChanged();
    } catch (err) {
      setDeleting({ busy: false, err: errorMessage(err) });
    }
  }

  return (
    <article className="card variant-card">
      <header className="variant-card-head">
        <div>
          <strong>{variant.label}</strong> <code className="variant-card-id">{variant.id}</code>
        </div>
        {!variant.error && (
          <button type="button" onClick={() => setEditing((prev) => !prev)}>
            {editing ? "Close" : "Edit"}
          </button>
        )}
      </header>
      {variant.error && (
        <p className="library-error" role="alert">
          This variant file can’t be parsed and is ignored at resolve time — fix it on disk or
          delete it. ({variant.error})
        </p>
      )}
      {!editing && !variant.error && (
        <p className="variant-card-summary">
          {diffKeys.length > 0 ? (
            <>
              Overrides:{" "}
              {diffKeys.map((k, i) => (
                <span key={k}>
                  {i > 0 && ", "}
                  <code>{k}</code>
                </span>
              ))}
            </>
          ) : (
            "No field overrides yet."
          )}
          {variant.body.trim() && " Replaces the description."}
        </p>
      )}
      {editing && (
        <VariantEditor
          worldId={worldId}
          characterId={characterId}
          variant={variant}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}
      <CardIconBar
        actions={[
          deleteAction({
            onClick: () => setDeleting({ busy: false, err: null }),
            label: `Delete variant ${variant.label}`,
          }),
        ]}
      />
      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete variant "${variant.label}"?`}
          body={
            <p>
              Campaigns selecting this variant fall back to the base character. The overlay file is
              removed from the library.
            </p>
          }
          confirmLabel="Delete"
          busy={deleting.busy}
          error={deleting.err}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
    </article>
  );
}

interface VariantEditorProps {
  worldId: string;
  characterId: string;
  variant: CharacterVariant;
  onSaved: () => void;
}

function VariantEditor({ worldId, characterId, variant, onSaved }: VariantEditorProps) {
  const [label, setLabel] = useState(variant.label);
  // JSON text mirrors the override editor in the campaign Cast view — the
  // diff is a small set of frontmatter keys, not a full character form.
  const [diffText, setDiffText] = useState(() => {
    const diff: Record<string, unknown> = { ...variant.frontmatter };
    delete diff.label;
    return JSON.stringify(diff, null, 2);
  });
  const [body, setBody] = useState(variant.body);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    let frontmatter: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(diffText || "{}");
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("the diff must be a JSON object of frontmatter keys");
      }
      frontmatter = parsed as Record<string, unknown>;
    } catch (parseErr) {
      setErr(errorMessage(parseErr));
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await libraryApi.putCharacterVariant(worldId, characterId, variant.id, {
        label,
        frontmatter,
        body,
      });
      onSaved();
    } catch (saveErr) {
      setErr(errorMessage(saveErr));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="variant-editor">
      <label className="form-field">
        Label
        <input value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label className="form-field">
        Overridden fields (JSON object; only keys that differ from the base)
        <textarea
          value={diffText}
          onChange={(e) => setDiffText(e.target.value)}
          rows={6}
          spellCheck={false}
        />
      </label>
      <label className="form-field">
        Replacement description (empty keeps the base prose)
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} />
      </label>
      {err && (
        <p className="library-error" role="alert">
          {err}
        </p>
      )}
      <div className="variant-editor-actions">
        <button type="button" onClick={() => void save()} disabled={busy}>
          {busy ? "Saving…" : "Save variant"}
        </button>
      </div>
    </div>
  );
}
