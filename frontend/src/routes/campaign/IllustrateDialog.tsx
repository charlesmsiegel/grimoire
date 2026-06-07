/**
 * Illustrate dialog — the "preview prompt first" half of the sidebar
 * Illustrate action. On open it asks the backend to compose a prompt from the
 * last few posts, the in-scene character prompts, and the campaign style
 * (via the light LLM), shows it for editing, then submits it to the image
 * generate endpoint. The resulting image streams back over the WebSocket
 * `image_ready` event and renders under its post.
 */

import { useEffect, useState } from "react";

import { viewsApi, type ComposedImagePrompt } from "../../api/views";

interface Props {
  open: boolean;
  campaignId: string;
  sceneId: string | null;
  /** Post the image attaches to (so it renders under that post in chat). */
  postId?: string | null;
  onClose: () => void;
}

export function IllustrateDialog({ open, campaignId, sceneId, postId, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [composed, setComposed] = useState<ComposedImagePrompt | null>(null);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setComposed(null);
    setPrompt("");
    setNegative("");
    setError(null);
    setLoading(true);
    let cancelled = false;
    viewsApi
      .composeImagePrompt(campaignId, {
        scene_id: sceneId ?? undefined,
        post_id: postId ?? undefined,
      })
      .then((res) => {
        if (cancelled) return;
        setComposed(res);
        setPrompt(res.prompt);
        setNegative(res.negative_prompt);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, campaignId, sceneId, postId]);

  if (!open) return null;

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await viewsApi.generateImage(campaignId, {
        scene_id: sceneId ?? undefined,
        post_id: postId ?? undefined,
        request: {
          prompt: prompt.trim(),
          negative_prompt: negative.trim() || null,
          ...(composed
            ? {
                width: composed.width,
                height: composed.height,
                steps: composed.steps,
                cfg_scale: composed.cfg_scale,
                sampler: composed.sampler,
                ...(composed.seed != null ? { seed: composed.seed } : {}),
              }
            : {}),
        },
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="illustrate-dialog-title"
    >
      <form className="modal illustrate-dialog" onSubmit={handleGenerate}>
        <h2 id="illustrate-dialog-title">Illustrate scene</h2>
        <p className="muted">
          Drafted by the light model from the last few posts, the in-scene characters, and the
          campaign style. Edit before generating.
        </p>
        {loading ? (
          <p className="muted">Composing prompt…</p>
        ) : (
          <>
            <label className="illustrate-field">
              <span>Prompt</span>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={5}
                autoFocus
              />
            </label>
            <label className="illustrate-field">
              <span>Negative prompt</span>
              <textarea value={negative} onChange={(e) => setNegative(e.target.value)} rows={2} />
            </label>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary"
            disabled={loading || submitting || !prompt.trim()}
          >
            {submitting ? "Generating…" : "Generate"}
          </button>
        </div>
      </form>
    </div>
  );
}
