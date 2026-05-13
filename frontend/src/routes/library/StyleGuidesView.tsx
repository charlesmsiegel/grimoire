import { Link, Route, Routes, useParams } from "react-router-dom";

import { libraryApi } from "../../api/library";
import { useResource } from "../../api/useResource";
import { Markdown } from "../../components/Markdown";
import { AsyncBoundary } from "./AsyncBoundary";

export function StyleGuidesView() {
  return (
    <Routes>
      <Route index element={<StyleGuideList />} />
      <Route path=":guideId" element={<StyleGuideDetail />} />
    </Routes>
  );
}

function StyleGuideList() {
  const { data, loading, error, reload } = useResource(() => libraryApi.listStyleGuides(), []);

  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Style guides</h3>
      </header>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No style guides yet."
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {data?.map((g) => (
            <li key={g.id} className="library-card">
              <Link to={`/library/style-guides/${encodeURIComponent(g.asset_id)}`}>
                <h4>{g.name || g.asset_id}</h4>
                <small>{g.asset_id}</small>
                {g.tags.length > 0 && <p className="library-card-meta">{g.tags.join(" · ")}</p>}
              </Link>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function StyleGuideDetail() {
  const { guideId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    () => libraryApi.getStyleGuide(guideId),
    [guideId],
  );

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/style-guides">Style guides</Link> / {guideId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && (
          <div className="style-guide-detail">
            <h3>{data.name || data.asset_id}</h3>
            <p>
              <code>{data.path}</code>
            </p>
            {data.tags.length > 0 && <p className="library-card-meta">{data.tags.join(" · ")}</p>}
            <article className="style-guide-body">
              <Markdown>{data.body}</Markdown>
            </article>
            <p className="library-status">
              Style-guide editing is read-only via REST; edit the underlying file directly for now
              (the watcher reindexes on save).
            </p>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}
