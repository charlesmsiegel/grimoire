import { useCallback, useState } from "react";

import {
  type ImportPreviewResponse,
  type ImportProgress,
  importSceneApi,
} from "../../api/campaign/importScene";
import { FilePathPicker } from "../../components/FilePathPicker";

type Phase = "pick" | "metadata" | "importing" | "done" | "error";

interface Props {
  campaignId: string;
  onClose: () => void;
  onImported: () => void;
}

export function ImportSceneDialog({ campaignId, onClose, onImported }: Props) {
  const [phase, setPhase] = useState<Phase>("pick");
  const [filePath, setFilePath] = useState("");
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [title, setTitle] = useState("");
  const [locationRef, setLocationRef] = useState("");
  const [inGameStart, setInGameStart] = useState("");
  const [inGameEnd, setInGameEnd] = useState("");
  const [mood, setMood] = useState("");
  const [tags, setTags] = useState("");
  const [pcRefs, setPcRefs] = useState("");
  const [npcRefs, setNpcRefs] = useState("");

  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);

  const handleFileSelected = useCallback(
    async (path: string) => {
      setFilePath(path);
      if (!path) return;
      setLoading(true);
      setPreviewErr(null);
      try {
        const res = await importSceneApi.preview(campaignId, path);
        setPreview(res);
        const s = res.sidecar;
        if (s) {
          if (typeof s.title === "string") setTitle(s.title);
          if (typeof s.location_ref === "string") setLocationRef(s.location_ref);
          if (typeof s.in_game_start === "string") setInGameStart(s.in_game_start);
          if (typeof s.in_game_end === "string") setInGameEnd(s.in_game_end);
          if (typeof s.mood === "string") setMood(s.mood);
          if (Array.isArray(s.tags)) setTags((s.tags as string[]).join(", "));
          if (Array.isArray(s.present_pc_refs))
            setPcRefs((s.present_pc_refs as string[]).join(", "));
          if (Array.isArray(s.present_character_refs)) {
            const all = s.present_character_refs as string[];
            const pcs = new Set(
              Array.isArray(s.present_pc_refs) ? (s.present_pc_refs as string[]) : [],
            );
            setNpcRefs(all.filter((r) => !pcs.has(r)).join(", "));
          }
        }
        if (!s?.present_pc_refs && res.detected_characters.pc_refs.length) {
          setPcRefs(res.detected_characters.pc_refs.join(", "));
        }
        if (!s?.present_character_refs && res.detected_characters.npc_refs.length) {
          setNpcRefs(res.detected_characters.npc_refs.join(", "));
        }
        if (!s?.title) {
          const stem = path.replace(/\\/g, "/").split("/").pop()?.replace(/\.md$/, "") ?? "";
          const cleaned = stem.replace(/^\d+-/, "").replace(/[-_]/g, " ");
          setTitle(cleaned || "Imported Scene");
        }
        setPhase("metadata");
      } catch (err) {
        setPreviewErr(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [campaignId],
  );

  const splitRefs = (s: string) =>
    s
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean);

  const handleImport = useCallback(async () => {
    setPhase("importing");
    setImportErr(null);
    const allPcRefs = splitRefs(pcRefs);
    const allNpcRefs = splitRefs(npcRefs);
    try {
      await importSceneApi.import(
        campaignId,
        {
          path: filePath,
          title,
          location_ref: locationRef || null,
          in_game_start: inGameStart || null,
          in_game_end: inGameEnd || null,
          mood: mood || null,
          tags: splitRefs(tags),
          present_character_refs: [...allPcRefs, ...allNpcRefs],
          present_pc_refs: allPcRefs,
        },
        (p) => setProgress(p),
      );
      setPhase("done");
      onImported();
    } catch (err) {
      setImportErr(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, [
    campaignId,
    filePath,
    title,
    locationRef,
    inGameStart,
    inGameEnd,
    mood,
    tags,
    pcRefs,
    npcRefs,
    onImported,
  ]);

  if (phase === "importing" || phase === "done" || phase === "error") {
    const pct = progress ? Math.round((progress.current / progress.total) * 100) : 0;
    return (
      <div className="import-overlay">
        <div className="import-progress-card">
          <h3>
            {phase === "done"
              ? "Import Complete"
              : phase === "error"
                ? "Import Failed"
                : `Importing: ${title}`}
          </h3>
          <div className="import-progress-bar-track">
            <div className="import-progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <p className="import-progress-detail">
            {phase === "error" ? importErr : (progress?.detail ?? "Starting…")}
          </p>
          {progress && phase === "importing" && (
            <p className="import-progress-count">
              {progress.current} / {progress.total}
            </p>
          )}
          {(phase === "done" || phase === "error") && (
            <button type="button" className="primary" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    );
  }

  if (phase === "metadata" && preview) {
    return (
      <div className="import-overlay">
        <div className="import-dialog">
          <h3>Import Scene</h3>
          <p className="import-post-count">{preview.post_count} posts detected</p>
          <label>
            <span>
              Title <em>*</em>
            </span>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            <span>Location</span>
            <input
              type="text"
              value={locationRef}
              onChange={(e) => setLocationRef(e.target.value)}
              placeholder="e.g. blackspire-tower"
            />
          </label>
          <label>
            <span>In-game start</span>
            <input
              type="text"
              value={inGameStart}
              onChange={(e) => setInGameStart(e.target.value)}
              placeholder="e.g. 1247-10-31T22:00:00"
            />
          </label>
          <label>
            <span>Mood</span>
            <input
              type="text"
              value={mood}
              onChange={(e) => setMood(e.target.value)}
              placeholder="e.g. tense"
            />
          </label>
          <label>
            <span>Tags</span>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="comma-separated"
            />
          </label>
          <label>
            <span>PC characters</span>
            <input
              type="text"
              value={pcRefs}
              onChange={(e) => setPcRefs(e.target.value)}
              placeholder="comma-separated"
            />
          </label>
          <label>
            <span>NPC characters</span>
            <input
              type="text"
              value={npcRefs}
              onChange={(e) => setNpcRefs(e.target.value)}
              placeholder="comma-separated"
            />
          </label>
          <div className="import-form-actions">
            <button
              type="button"
              onClick={() => {
                setPhase("pick");
                setPreview(null);
                setTitle("");
                setLocationRef("");
                setInGameStart("");
                setInGameEnd("");
                setMood("");
                setTags("");
                setPcRefs("");
                setNpcRefs("");
              }}
            >
              Back
            </button>
            <button
              type="button"
              className="primary"
              onClick={handleImport}
              disabled={!title.trim()}
            >
              Import
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="import-overlay">
      <div className="import-dialog">
        <div className="import-dialog-header">
          <h3>Import Scene</h3>
          <button type="button" onClick={onClose}>
            &times;
          </button>
        </div>
        <p>Select a grimoire-format scene file (.md) to import.</p>
        <FilePathPicker
          label="Scene file"
          description="Path to a .md scene file"
          required
          value={filePath}
          glob="*.md"
          onChange={setFilePath}
        />
        {previewErr && <p className="import-error">{previewErr}</p>}
        <div className="import-form-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={!filePath || loading}
            onClick={() => handleFileSelected(filePath)}
          >
            {loading ? "Parsing…" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
