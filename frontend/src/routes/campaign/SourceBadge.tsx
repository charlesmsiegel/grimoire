/**
 * Source badge for entity references in the scene pane and headers.
 *
 * Spec 14 §Source badges: every reference is annotated with where it
 * resolved from — library (📚), emergent (🌿), or override (✏️). Click
 * support for the source chain is left for task 34 (Cast / World views).
 */

export type ResolutionSource = "library" | "emergent" | "override";

const META: Record<ResolutionSource, { label: string; glyph: string }> = {
  library: { label: "from library", glyph: "📚" },
  emergent: { label: "emergent", glyph: "🌿" },
  override: { label: "campaign override", glyph: "✏️" },
};

interface Props {
  source: ResolutionSource;
  detail?: string;
}

export function SourceBadge({ source, detail }: Props) {
  const meta = META[source];
  return (
    <span
      className={`source-badge source-badge-${source}`}
      title={detail ? `${meta.label} — ${detail}` : meta.label}
      aria-label={detail ? `${meta.label}: ${detail}` : meta.label}
    >
      <span aria-hidden>{meta.glyph}</span>
    </span>
  );
}
