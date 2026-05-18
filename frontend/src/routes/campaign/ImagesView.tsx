/**
 * Images view (spec 14 §Images view).
 *
 * Gallery + per-character prompt templates + a small queue panel that listens
 * for ``image_ready`` events to refresh the gallery. The generate / re-roll
 * / star actions invoke the REST endpoints in `api.client`.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, libraryApi } from "../../api/library";
import { viewsApi } from "../../api/views";
import type {
  ImageMetadata,
  ResolutionSource,
  ResolvedCharacter,
} from "../../api/types";
import { useApi } from "../../api/useApi";
import type { ImageJobEntry } from "../../state/storeContext";
import { useStore } from "../../state/useStore";
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
      {tab === "queue" && <Queue campaignId={campaignId} />}
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
  const thumbUrl = image.thumbnail_path ? `/api/files/${encodeURI(image.thumbnail_path)}` : url;
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

function Queue({ campaignId }: { campaignId: string }) {
  // §6 — image queue live panel. We rely on the active CampaignStreamProvider
  // (mounted in CampaignLayout) which feeds `imagegen_*` and `image_ready`
  // events into `state.imageJobs` via `routeToStore`. This component just
  // renders the current snapshot; per-job cancel calls the existing REST
  // endpoint at DELETE /api/campaigns/{id}/images/jobs/{job_id}.
  const { state } = useStore();
  const jobs = useMemo<ImageJobEntry[]>(
    () => Object.values(state.imageJobs).sort((a, b) => a.created_at - b.created_at),
    [state.imageJobs],
  );

  if (jobs.length === 0) {
    return (
      <div className="image-queue">
        <p className="muted">No active or queued image jobs.</p>
      </div>
    );
  }

  return (
    <div className="image-queue">
      <ul className="image-queue-list">
        {jobs.map((job) => (
          <ImageQueueRow key={job.job_id} campaignId={campaignId} job={job} />
        ))}
      </ul>
    </div>
  );
}

function ImageQueueRow({ campaignId, job }: { campaignId: string; job: ImageJobEntry }) {
  const [cancelling, setCancelling] = useState(false);
  const canCancel = job.status === "queued" || job.status === "running";

  async function cancel() {
    setCancelling(true);
    try {
      await fetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/images/jobs/${encodeURIComponent(job.job_id)}`,
        { method: "DELETE" },
      );
    } catch {
      // Swallow; the backend emits `imagegen_job_failed` if cancel hit the
      // server, and the row will transition to "failed" with a reason.
    } finally {
      setCancelling(false);
    }
  }

  return (
    <li className={`image-queue-row image-queue-row-${job.status}`}>
      <span className="image-queue-status">{job.status}</span>
      <span className="image-queue-job-id" title={job.job_id}>
        {job.job_id.slice(0, 12)}
      </span>
      {job.prompt_preview && <span className="image-queue-prompt">{job.prompt_preview}</span>}
      {job.scene_id && <span className="muted">scene {job.scene_id}</span>}
      {job.reason && <span className="image-queue-reason">{job.reason}</span>}
      {canCancel && (
        <button
          type="button"
          className="image-queue-cancel"
          onClick={cancel}
          disabled={cancelling}
        >
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      )}
    </li>
  );
}

function Templates({ campaignId }: { campaignId: string }) {
  const state = useApi(() => viewsApi.listCharacters(campaignId), [campaignId]);
  return (
    <Loading state={state} emptyMessage="No characters to template prompts for.">
      {(rows) => (
        <ul className="template-list">
          {rows.map((row) => (
            <PromptTemplate key={row.character.id} campaignId={campaignId} character={row} />
          ))}
        </ul>
      )}
    </Loading>
  );
}

// A character is a library character when its resolution chain contains a
// ``library_*`` layer with a concrete ``world_id``. Pure-emergent
// characters and override-only resolutions don't have a library home we can
// PATCH today, so the Save-to-card button stays disabled for them.
function libraryHomeFromSourceChain(
  chain: ResolutionSource[],
): { world_id: string; library_id: string } | null {
  for (const src of chain) {
    if (
      (src.layer === "library_live" || src.layer === "library_snapshot") &&
      src.world_id &&
      src.library_id
    ) {
      // ``library_id`` looks like "worlds/<world>/characters/<id>"; we
      // need the trailing entity id only.
      const parts = src.library_id.split("/");
      const entityId = parts[parts.length - 1];
      if (entityId) return { world_id: src.world_id, library_id: entityId };
    }
  }
  return null;
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
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "ok" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const libraryHome = useMemo(
    () => libraryHomeFromSourceChain(character.source_chain),
    [character.source_chain],
  );

  useEffect(() => {
    setBase(initial);
    setNegative(initialNegative);
    setSeed(initialSeed?.toString() ?? "");
    setDirty(false);
    setSaveStatus("idle");
    setSaveError(null);
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

  const save = async () => {
    if (!libraryHome) return;
    setSaving(true);
    setSaveStatus("idle");
    setSaveError(null);
    const seedNumber = seed ? Number(seed) : null;
    try {
      await libraryApi.patchWorldCharacterImageTemplate(
        libraryHome.world_id,
        libraryHome.library_id,
        {
          base_prompt: base,
          negative_prompt: negative,
          canonical_seed:
            seedNumber !== null && Number.isFinite(seedNumber) ? seedNumber : null,
        },
      );
      setDirty(false);
      setSaveStatus("ok");
      setTimeout(() => setSaveStatus("idle"), 2500);
    } catch (err) {
      setSaveStatus("error");
      setSaveError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const saveTitle = libraryHome
    ? "Persist the prompt template to the library character card."
    : "Override save coming soon";

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
        <button
          type="button"
          disabled={!dirty || saving || !libraryHome}
          title={saveTitle}
          onClick={save}
        >
          {saving ? "Saving…" : "Save to card"}
        </button>
        {saveStatus === "ok" && (
          <span className="badge badge-ok" role="status">
            Saved
          </span>
        )}
        {saveStatus === "error" && saveError && (
          <span className="badge badge-warn" role="alert" title={saveError}>
            Save failed
          </span>
        )}
      </div>
    </li>
  );
}
