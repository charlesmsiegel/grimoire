import { useCallback, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { ApiError, libraryApi } from "../../api/library";
import type { StyleGuideEditPayload } from "../../api/library";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { Markdown } from "../../components/Markdown";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { useDestructiveConfirm } from "../../hooks/useDestructiveConfirm";

const SECTION_LABELS = {
  pacing: "Pacing",
  voice: "Voice",
  themes: "Themes",
  avoid: "Avoid",
} as const;
type SectionKey = keyof typeof SECTION_LABELS;
const SECTION_KEYS: SectionKey[] = ["pacing", "voice", "themes", "avoid"];

const SECTION_PLACEHOLDERS: Record<SectionKey, string> = {
  pacing: "e.g. Let scenes breathe before the reveal.",
  voice: "e.g. Warm third-limited, plain diction.",
  themes: "e.g. Found family; the cost of mercy.",
  avoid: "e.g. Quippy modern slang.",
};

type Sections = Record<SectionKey, string[]>;

const emptySections = (): Sections => ({ pacing: [], voice: [], themes: [], avoid: [] });

export function StyleGuidesView() {
  return (
    <Routes>
      <Route index element={<StyleGuideList />} />
      <Route path="new" element={<StyleGuideCreate />} />
      <Route path=":guideId" element={<StyleGuideDetail />} />
      <Route path=":guideId/edit" element={<StyleGuideEdit />} />
    </Routes>
  );
}

function StyleGuideList() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.listStyleGuides(), []),
  );

  const del = useDestructiveConfirm<{ id: string; name: string }>(async ({ id }) => {
    await libraryApi.deleteStyleGuide(id);
    reload();
  });

  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Style guides</h3>
        <button onClick={() => navigate("/library/style-guides/new")}>+ New style guide</button>
      </header>
      {del.target && (
        <ConfirmDestructiveDialog
          open
          title={`Delete style guide "${del.target.name}"?`}
          body={<p>This cannot be undone.</p>}
          busy={del.busy}
          error={del.error}
          onConfirm={del.confirm}
          onCancel={del.cancel}
        />
      )}
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No style guides yet."
        onRetry={reload}
      >
        <ul className="grid-cards">
          {data?.map((g) => (
            <li key={g.id} className="library-card">
              <Link to={`/library/style-guides/${encodeURIComponent(g.asset_id)}`}>
                <h4>{g.name || g.asset_id}</h4>
                <small>{g.asset_id}</small>
                {g.tags.length > 0 && <p className="library-card-meta">{g.tags.join(" · ")}</p>}
              </Link>
              <div className="library-card-actions">
                <Link to={`/library/style-guides/${encodeURIComponent(g.asset_id)}/edit`}>
                  Edit
                </Link>
              </div>
              <CardIconBar
                actions={[
                  deleteAction({
                    onClick: () => del.request({ id: g.asset_id, name: g.name || g.asset_id }),
                    label: `Delete style guide ${g.name || g.asset_id}`,
                  }),
                ]}
              />
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function StyleGuideCreate() {
  const navigate = useNavigate();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsStr, setTagsStr] = useState("");
  const [sections, setSections] = useState<Sections>(emptySections);
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const cleanSection = (key: SectionKey) => sections[key].map((b) => b.trim()).filter(Boolean);
      const created = await libraryApi.createStyleGuide({
        id: id.trim(),
        name: name.trim(),
        description: description.trim(),
        tags,
        pacing: cleanSection("pacing"),
        voice: cleanSection("voice"),
        themes: cleanSection("themes"),
        avoid: cleanSection("avoid"),
      });
      navigate(`/library/style-guides/${encodeURIComponent(created.asset_id)}`);
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/style-guides">Style guides</Link> / new
      </p>
      <header className="library-section-header">
        <h3>New style guide</h3>
      </header>
      <form onSubmit={submit} className="library-form" aria-label="Create style guide">
        <label>
          <span>ID</span>
          <input
            required
            value={id}
            pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*"
            title="letters, digits, dots, underscores, hyphens; must start with a letter or digit"
            onChange={(e) => setId(e.target.value)}
          />
          <small>
            Used as the filename (e.g. <code>cozy-mystery</code>).
          </small>
        </label>
        <label>
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="One-line summary shown in pickers"
          />
        </label>
        <label>
          <span>Tags</span>
          <input
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
        {SECTION_KEYS.map((key) => (
          <BulletEditor
            key={key}
            label={SECTION_LABELS[key]}
            placeholder={SECTION_PLACEHOLDERS[key]}
            items={sections[key]}
            onChange={(next) => setSections((s) => ({ ...s, [key]: next }))}
          />
        ))}
        <div className="library-form-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          <button type="button" onClick={() => navigate("/library/style-guides")} disabled={busy}>
            Cancel
          </button>
        </div>
        {submitErr && (
          <p className="library-error" role="alert">
            {submitErr}
          </p>
        )}
      </form>
    </section>
  );
}

function StyleGuideEdit() {
  const { guideId = "" } = useParams();
  const { data, loading, error } = useResource(
    useCallback(() => libraryApi.getStyleGuideEdit(guideId), [guideId]),
  );

  if (loading) return <p>Loading…</p>;
  if (error)
    return (
      <p className="library-error" role="alert">
        {error.message}
      </p>
    );
  if (!data) return null;

  return <StyleGuideEditForm guideId={guideId} initial={data} />;
}

function StyleGuideEditForm({
  guideId,
  initial,
}: {
  guideId: string;
  initial: StyleGuideEditPayload;
}) {
  const navigate = useNavigate();
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [tagsStr, setTagsStr] = useState(initial.tags.join(", "));
  const [sections, setSections] = useState<Sections>({
    pacing: initial.pacing,
    voice: initial.voice,
    themes: initial.themes,
    avoid: initial.avoid,
  });
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const cleanSection = (key: SectionKey) => sections[key].map((b) => b.trim()).filter(Boolean);
      await libraryApi.updateStyleGuide(guideId, {
        name: name.trim(),
        description: description.trim(),
        tags,
        pacing: cleanSection("pacing"),
        voice: cleanSection("voice"),
        themes: cleanSection("themes"),
        avoid: cleanSection("avoid"),
      });
      navigate(`/library/style-guides/${encodeURIComponent(guideId)}`);
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/style-guides">Style guides</Link> /{" "}
        <Link to={`/library/style-guides/${encodeURIComponent(guideId)}`}>{guideId}</Link> / edit
      </p>
      <header className="library-section-header">
        <h3>Edit style guide</h3>
      </header>
      <form onSubmit={submit} className="library-form" aria-label="Edit style guide">
        <label>
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          <span>Tags</span>
          <input
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
        {SECTION_KEYS.map((key) => (
          <BulletEditor
            key={key}
            label={SECTION_LABELS[key]}
            placeholder={SECTION_PLACEHOLDERS[key]}
            items={sections[key]}
            onChange={(next) => setSections((s) => ({ ...s, [key]: next }))}
          />
        ))}
        <div className="library-form-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => navigate(`/library/style-guides/${encodeURIComponent(guideId)}`)}
            disabled={busy}
          >
            Cancel
          </button>
        </div>
        {submitErr && (
          <p className="library-error" role="alert">
            {submitErr}
          </p>
        )}
      </form>
    </section>
  );
}

function StyleGuideDetail() {
  const { guideId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getStyleGuide(guideId), [guideId]),
  );

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/style-guides">Style guides</Link> / {guideId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && (
          <div className="style-guide-detail">
            <header className="library-section-header">
              <h3>{data.name || data.asset_id}</h3>
              <Link
                to={`/library/style-guides/${encodeURIComponent(data.asset_id)}/edit`}
                className="button-link"
              >
                Edit
              </Link>
            </header>
            <p>
              <code>{data.path}</code>
            </p>
            {data.tags.length > 0 && <p className="library-card-meta">{data.tags.join(" · ")}</p>}
            <article className="style-guide-body">
              <Markdown>{data.body}</Markdown>
            </article>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}

function BulletEditor({
  label,
  placeholder,
  items,
  onChange,
}: {
  label: string;
  placeholder?: string;
  items: string[];
  onChange: (next: string[]) => void;
}) {
  function update(idx: number, value: string) {
    const next = items.slice();
    next[idx] = value;
    onChange(next);
  }
  function remove(idx: number) {
    onChange(items.filter((_, i) => i !== idx));
  }
  function add() {
    onChange([...items, ""]);
  }
  return (
    <fieldset className="bullet-editor">
      <legend>{label}</legend>
      {items.length === 0 && <p className="empty-state">No bullets yet.</p>}
      {items.map((value, idx) => (
        <div key={idx} className="bullet-editor-row">
          <span className="bullet-editor-marker" aria-hidden>
            •
          </span>
          <input
            value={value}
            placeholder={placeholder}
            onChange={(e) => update(idx, e.target.value)}
          />
          <button
            type="button"
            className="bullet-editor-remove"
            aria-label={`Remove ${label} bullet ${idx + 1}`}
            onClick={() => remove(idx)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="bullet-editor-add" onClick={add}>
        + Add bullet
      </button>
    </fieldset>
  );
}
