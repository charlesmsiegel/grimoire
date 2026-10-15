import { useEffect, useState } from "react";
import { api, type GreetingAvailabilityPreview as PreviewRow } from "../api/client";
import { errorText } from "../api/errors";

type Preview =
  | { status: "loading" }
  | { status: "ready"; rows: PreviewRow[] }
  | { status: "error"; error: unknown };

/** A draft's world availability, never a campaign write or a scene start (#68).
 *  The caller keys this by world and tag contents: a changed choice immediately
 *  discards the old rows, even before its debounced request is sent. Cleanup
 *  also ignores already-sent requests, which can finish after that remount. */
export function GreetingAvailabilityPreview({ world, tags }: { world: string; tags: string[] }) {
  const [preview, setPreview] = useState<Preview>({ status: "loading" });

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      void api.previewGreetings(world, tags).then(
        (rows) => { if (live) setPreview({ status: "ready", rows }); },
        (error: unknown) => { if (live) setPreview({ status: "error", error }); },
      );
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [world, tags]);

  const available = preview.status === "ready" ? preview.rows.filter((g) => g.available) : [];
  const blocked = preview.status === "ready" ? preview.rows.filter((g) => !g.available) : [];

  return (
    <section className="field greeting-preview" aria-label="Opening preview">
      <div className="role">Matching openings</div>
      {preview.status === "loading" && <p className="field-hint" role="status">Loading openings…</p>}
      {preview.status === "error" && (
        <p className="field-hint" role="status">Could not preview openings. {errorText(preview.error)}</p>
      )}
      {preview.status === "ready" && (preview.rows.length === 0
        ? <p className="field-hint">No greetings in this world.</p>
        : <>
          <p className="field-hint">Openings that match these tags and plot requirements:</p>
          {available.length === 0
            ? <p className="field-hint">No matching openings yet.</p>
            : <div className="chips">
              {available.map((g) => (
                <span key={g.id} className="chip on">{g.name}{g.pcless ? " (offscreen)" : ""}</span>
              ))}
            </div>}
          {blocked.length > 0 && <>
            <div className="role">Blocked openings</div>
            <ul className="field-hint">
              {blocked.map((g) => (
                <li key={g.id}>
                  <span>{g.name}</span>{g.pcless ? " (offscreen)" : ""} — {g.reasons.join("; ")}
                </li>
              ))}
            </ul>
          </>}
        </>)}
    </section>
  );
}
