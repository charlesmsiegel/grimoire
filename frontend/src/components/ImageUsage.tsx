import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { errorText } from "../api/errors";
import type { EntityScope, ImageUsageReport } from "../api/types";
import { RECORD_SECTIONS, sectionHref, type RecordSection } from "../worldPaths";

/** `world:<wid>` or `campaign:<cid>`, as the usage route spells a scope. */
function parseScope(scope: string): EntityScope | null {
  const at = scope.indexOf(":");
  if (at < 0) return null;
  const kind = scope.slice(0, at);
  const id = scope.slice(at + 1);
  return id && (kind === "world" || kind === "campaign") ? { kind, id } : null;
}

const scopeTitle = (scope: string) => {
  const s = parseScope(scope);
  return s ? `${s.kind === "world" ? "World" : "Campaign"} ${s.id}` : scope;
};

/** One chip to draw: its text, where it goes (`null` = nowhere), and what it
 *  says on hover. */
type Chip = { key: string; label: string; to: string | null; title?: string };

/** A record of a world or a campaign's copy of one. The address is the one
 *  `worldPaths` builds, so it cannot drift from what `App.tsx` routes. */
function recordTo(scope: string, at: RecordSection, rid: string): string | null {
  const s = parseScope(scope);
  return s ? sectionHref(s, { kind: "record", at, rid }) : null;
}

/** The report as an ordered list of buckets, each with the chips it draws.
 *
 *  A bucket whose entries have a page links there; the rest have none today and
 *  say so by being plain chips: a cover belongs to no page of its own, a
 *  collection is only a name, and a campaign's own library lives in the post
 *  picker, a modal with no address. */
function buckets(r: ImageUsageReport): { id: string; heading: string; chips: Chip[] }[] {
  return [
    { id: "characters", heading: "Characters", chips: r.characters.map((e) => ({
        key: `${e.scope}/${e.id}/${e.vid}/${e.name}`, label: `${e.id} · ${e.name}`,
        to: recordTo(e.scope, "characters", e.id), title: scopeTitle(e.scope) })) },
    { id: "pcs", heading: "Player characters", chips: r.pcs.map((e) => ({
        key: `${e.scope}/${e.id}/${e.vid}/${e.name}`, label: `${e.id} · ${e.name}`,
        to: recordTo(e.scope, "pcs", e.id), title: scopeTitle(e.scope) })) },
    { id: "entities", heading: "Records", chips: r.entities.map((e) => ({
        key: `${e.scope}/${e.kind}/${e.id}/${e.vid}/${e.name}`, label: `${e.id} · ${e.name}`,
        // An unknown kind is not guessed at: it is a chip with nowhere to go.
        to: (RECORD_SECTIONS as readonly string[]).includes(e.kind)
          && e.kind !== "characters" && e.kind !== "pcs"
          ? recordTo(e.scope, e.kind as RecordSection, e.id) : null,
        title: `${scopeTitle(e.scope)} · ${e.kind}` })) },
    { id: "greetings", heading: "Greetings", chips: r.greetings.map((e) => ({
        key: `${e.scope}/${e.id}/${e.name}`, label: `${e.id} · ${e.name}`,
        to: recordTo(e.scope, "greetings", e.id), title: scopeTitle(e.scope) })) },
    { id: "world_images", heading: "World images", chips: r.world_images.map((e) => ({
        key: `${e.wid}/${e.name}`, label: e.name,
        to: sectionHref({ kind: "world", id: e.wid }, { kind: "section", at: "images" }),
        title: `World ${e.wid}` })) },
    { id: "campaign_images", heading: "Campaign images", chips: r.campaign_images.map((e) => ({
        key: `${e.cid}/${e.name}`, label: e.name, to: null, title: `Campaign ${e.cid}` })) },
    { id: "covers", heading: "Covers", chips: r.covers.map((e) => ({
        key: e.scope, label: `${parseScope(e.scope)?.kind === "campaign" ? "Campaign" : "World"} cover`,
        to: null, title: scopeTitle(e.scope) })) },
    { id: "collections", heading: "Collections", chips: r.collections.map((e) => ({
        key: `${e.wid}/${e.collection}`, label: e.collection, to: null, title: `World ${e.wid}` })) },
  ].filter((b) => b.chips.length > 0);
}

/** The only part that needs a router, so a surface that never navigates (the
 *  post picker, a modal over the composer) can render `ImageUsage` without
 *  one. */
function NavChip({ chip }: { chip: Chip & { to: string } }) {
  const navigate = useNavigate();
  return (
    <button type="button" className="chip" title={chip.title}
            onClick={() => navigate(chip.to)}>{chip.label}</button>
  );
}

/** Where one image object is placed: a "Used in…" button that, once opened,
 *  reads `GET /api/images/{id}/usage` and lists the records that hold it.
 *
 *  Collapsed it asks nothing -- most visits to an art tab never want this, and
 *  the report scans every scope. `navigable` is false where a navigation would
 *  tear down what the reader is in the middle of. */
export function ImageUsage({ imageId, navigable = true }: {
  imageId: string; navigable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<ImageUsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A different image under the same mounted control starts collapsed again.
  useEffect(() => { setOpen(false); setReport(null); setError(null); }, [imageId]);

  useEffect(() => {
    if (!open || report || error) return;
    let live = true;
    api.getImageUsage(imageId)
      .then((r) => { if (live) setReport(r); })
      .catch((err: unknown) => { if (live) setError(errorText(err)); });
    return () => { live = false; };
  }, [open, report, error, imageId]);

  if (!open) {
    return (
      <button className="subtle image-usage-toggle" type="button"
              onClick={() => setOpen(true)}>Used in…</button>
    );
  }

  const groups = report ? buckets(report) : [];
  return (
    <div className="side-section image-usage">
      <h4>Used in</h4>
      {error && <p className="field-hint">{error}</p>}
      {!error && !report && <p className="field-hint">Reading…</p>}
      {report && groups.length === 0 && <p className="field-hint">Not used anywhere else.</p>}
      {groups.map((g) => (
        <div key={g.id}>
          <p className="field-hint">{g.heading}</p>
          <div className="chips">
            {g.chips.map((c) => navigable && c.to
              ? <NavChip key={c.key} chip={{ ...c, to: c.to }} />
              : <span key={c.key} className="chip on" title={c.title}>{c.label}</span>)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default ImageUsage;
