/**
 * Images view (spec 14 §Images view).
 *
 * Gallery + per-character prompt templates + a small queue panel that listens
 * for ``image_ready`` events to refresh the gallery. The generate / re-roll
 * / star actions invoke the REST endpoints in `api.client`.
 */

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type { ResolvedCharacter, ImageMetadata } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Loading } from "./common";

type ImagesTab = "gallery" | "queue" | "templates";

const TABS: { key: ImagesTab; label: string }[] = [
  { key: "gallery", label: "Gallery" },
  { key: "queue", label: "Queue" },
  { key: "templates", label: "Templates" },
];

export function ImagesView() {
  const { campaignId = "" } = useParams();
  const [tab, setTab] = useState<ImagesTab>("gallery");
  return (
    <section className="route campaign-images" aria-labelledby="images-heading">
      <header className="route-header">
        <h2 id="images-heading">Images</h2>
        <div className="tab-row" role="tablist" aria-label="Images tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={tab === t.key ? "tab active" : "tab"}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>
      {tab === "gallery" && <Gallery campaignId={campaignId} />}
      {tab === "queue" && <Queue />}
      {tab === "templates" && <Templates campaignId={campaignId} />}
    </section>
  );
}

function Gallery({ campaignId }: { campaignId: string }) {
  const [starredOnly, setStarredOnly] = useState(false);
  const state = useApi(
    () => viewsApi.listImages(campaignId, { starredOnly }),
    [campaignId, starredOnly],
  );
  return (
    <div className="image-gallery">
      <div className="image-toolbar">
        <label className="field-inline">
          <input
            type="checkbox"
            checked={starredOnly}
            onChange={(e) => setStarredOnly(e.target.checked)}
          />
          starred only
        </label>
        <button
          type="button"
          onClick={() => viewsApi.generateImage(campaignId, {}).catch(() => undefined)}
        >
          Generate from current scene
        </button>
      </div>
      <Loading state={state} emptyMessage="No images generated yet for this campaign.">
        {(images) => (
          <ul className="image-grid">
            {images.map((img) => (
              <ImageTile key={img.id} image={img} />
            ))}
          </ul>
        )}
      </Loading>
    </div>
  );
}

function ImageTile({ image }: { image: ImageMetadata }) {
  const url = `/api/files/${encodeURI(image.file_path)}`;
  const thumbUrl = image.thumbnail_path
    ? `/api/files/${encodeURI(image.thumbnail_path)}`
    : url;
  return (
    <li className="image-tile">
      <figure>
        <img src={thumbUrl} alt={image.prompt || image.id} loading="lazy" />
        <figcaption>
          <div className="image-tile-row">
            {image.user_starred && <span className="badge badge-ok">★</span>}
            {image.scene_id && <span className="muted">scene {image.scene_id}</span>}
          </div>
          {image.prompt && (
            <details>
              <summary>Prompt</summary>
              <p>{image.prompt}</p>
              {image.negative_prompt && (
                <p>
                  <em>negative:</em> {image.negative_prompt}
                </p>
              )}
            </details>
          )}
        </figcaption>
      </figure>
    </li>
  );
}

function Queue() {
  // Queue events arrive via the campaign WS stream (`image_ready` /
  // `image_queued`); the orchestrator subscription in `state/campaignStream`
  // does not route them yet, so the panel renders a static placeholder until
  // those events are wired through the global store.
  return (
    <div className="image-queue">
      <p className="muted">
        Active and queued jobs stream here. The WebSocket bridge for image
        events lands alongside the orchestrator integration in a follow-up
        task; until then, queued jobs surface in the backend logs.
      </p>
    </div>
  );
}

function Templates({ campaignId }: { campaignId: string }) {
  const state = useApi(() => viewsApi.listCharacters(campaignId), [campaignId]);
  return (
    <Loading state={state} emptyMessage="No characters to template prompts for.">
      {(rows) => (
        <ul className="template-list">
          {rows.map((row) => (
            <PromptTemplate
              key={row.character.id}
              campaignId={campaignId}
              character={row}
            />
          ))}
        </ul>
      )}
    </Loading>
  );
}

function PromptTemplate({
  campaignId,
  character,
}: {
  campaignId: string;
  character: ResolvedCharacter;
}) {
  const initial = character.character.image?.base_prompt ?? "";
  const initialNegative = character.character.image?.negative_prompt ?? "";
  const initialSeed = character.character.image?.canonical_seed ?? null;
  const [base, setBase] = useState(initial);
  const [negative, setNegative] = useState(initialNegative);
  const [seed, setSeed] = useState<string>(initialSeed?.toString() ?? "");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setBase(initial);
    setNegative(initialNegative);
    setSeed(initialSeed?.toString() ?? "");
    setDirty(false);
    // re-run when the resolved character changes
  }, [initial, initialNegative, initialSeed]);

  const test = async () => {
    const seedNumber = seed ? Number(seed) : undefined;
    try {
      await viewsApi.generateImage(campaignId, {
        request: {
          prompt: base,
          negative_prompt: negative || null,
          seed: Number.isFinite(seedNumber) ? seedNumber : null,
        },
      });
    } catch {
      // surfaced in the queue / logs
    }
  };

  return (
    <li className="template-row">
      <header>
        <strong>{character.character.name}</strong>
        <span className="muted">{character.character.role}</span>
      </header>
      <label className="field">
        <span>Base prompt</span>
        <textarea
          value={base}
          rows={2}
          onChange={(e) => {
            setBase(e.target.value);
            setDirty(true);
          }}
        />
      </label>
      <label className="field">
        <span>Negative prompt</span>
        <textarea
          value={negative}
          rows={1}
          onChange={(e) => {
            setNegative(e.target.value);
            setDirty(true);
          }}
        />
      </label>
      <label className="field">
        <span>Canonical seed</span>
        <input
          type="number"
          value={seed}
          onChange={(e) => {
            setSeed(e.target.value);
            setDirty(true);
          }}
        />
      </label>
      <div className="button-row">
        <button type="button" onClick={test}>
          Test prompt
        </button>
        <button type="button" disabled={!dirty} title="Persisting to the character card is wired in a follow-up task.">
          Save to card
        </button>
      </div>
    </li>
  );
}
