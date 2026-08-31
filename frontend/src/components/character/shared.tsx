/** The pieces the character grid and the character page both need.
 *
 *  Extracted when `CharacterEditor` was split into a grid that lives inside the
 *  world page and a `CharacterPage` that owns its own route — everything here
 *  was a top-level helper in that one file, and is a helper in two now.
 */
import { api, type Card, type CardFormat, type ChubImportResult, type EntityScope } from "../../api/client";

/** The V3 card's prose fields in editing order, with the control each wants.
 *
 *  `components/cardFields.ts` keeps a second, label-only list in *reading*
 *  order for the incoming-change diff. Keyed the same, so a prose field added
 *  to the card belongs in both.
 */
export const TEXT_FIELDS: { key: string; label: string; area?: boolean }[] = [
  { key: "description", label: "Description", area: true },
  { key: "personality", label: "Personality", area: true },
  { key: "scenario", label: "Scenario", area: true },
  { key: "first_mes", label: "First message", area: true },
  { key: "mes_example", label: "Example dialogue", area: true },
  { key: "system_prompt", label: "System prompt", area: true },
  { key: "post_history_instructions", label: "Post-history instructions", area: true },
  { key: "creator_notes", label: "Creator notes", area: true },
];

export function describeChubResult(result: ChubImportResult): string {
  const parts: string[] = [];
  if (result.gallery.attempted > 0) {
    parts.push(`${result.gallery.stored}/${result.gallery.attempted} gallery image${result.gallery.attempted === 1 ? "" : "s"}`);
  }
  if (result.lore.lorebooks_found > 0) {
    const n = result.lore.created.length;
    parts.push(`${result.lore.lorebooks_found} lorebook${result.lore.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`);
  }
  const lead = result.updated ? "Updated this version from URL" : "Downloaded from URL";
  return parts.length ? `${lead} — ${parts.join(", ")}` : lead;
}

/** A scene's story number, read out of its own id (`<NNN>--<date>--<slug>`).
 *  The same read `CampaignView.sceneNumber` makes, and for the same reason: the
 *  number belongs to the file, never to a list's ordering, which drifts the
 *  moment an earlier scene is re-edited. */
export function sceneOrdinal(id: string): string {
  const m = /^(\d+)--/.exec(id);
  return m ? String(parseInt(m[1], 10)) : id;
}

/** A rough size for the description's cost stamp.
 *
 *  There is no tokenizer in the browser: the only real token counts grimoire
 *  has come from the backend's context builder, which measures a whole
 *  assembled prompt once per turn and never an individual field. So this is the
 *  usual four-characters-a-token estimate, and it is rendered behind a `≈` so
 *  it reads as the order of magnitude it is. Its job is to make the size of a
 *  field legible *before* it costs a turn, not to be added up. */
export function estimateTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

export function focusStyle(f?: number | null): React.CSSProperties | undefined {
  return f == null ? undefined : { objectPosition: `${f}% ${f}%` };
}

export function formatOf(file: File): string {
  const ext = file.name.split(".").pop()?.toLowerCase();
  return ext === "png" ? "png" : ext === "charx" ? "charx" : "json";
}

/** `?v=` names the exact content state, so these cache immutable; an upload, a
 *  remove or a promote refreshes the tokens through the character read. The
 *  token must come from the STORE: a session counter reset to its initial value
 *  on every mount pinned the pre-upload image in the browser cache for a year
 *  (an immutable URL is never revalidated). */
export const withToken = (url: string, v?: string | null) => (v ? `${url}?v=${v}` : url);

export const avatarSrc = (scope: EntityScope, cid: string, version: string, v?: string | null) =>
  withToken(api.actorImageUrl(scope, "characters", cid, version, "avatar"), v);

/** Two initials, for a record with no avatar. */
export function initialsOf(name: string): string {
  return name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("");
}

const EXPORT_FORMATS: { format: CardFormat; label: string; hint: string }[] = [
  { format: "json", label: "JSON", hint: "card text plus the avatar, embedded" },
  { format: "png", label: "PNG", hint: "the avatar, with the card written into it" },
  { format: "charx", label: "CHARX", hint: "card and avatar in one zip" },
];

/** Download the viewed version as a card. Plain links, like the campaign
 *  exports: the response is binary and the route names the file, so there is
 *  nothing for the client to assemble. World scope only — the export route
 *  hangs off /worlds. */
export function ExportMenu({ wid, cid, vid }: { wid: string; cid: string; vid: string }) {
  return (
    <details className="export-menu">
      <summary className="export-toggle">Export</summary>
      <div className="export-options">
        {EXPORT_FORMATS.map(({ format, label, hint }) => (
          <a key={format} href={api.exportUrl(wid, cid, vid, format)} download title={hint}>
            {label}
          </a>
        ))}
      </div>
    </details>
  );
}

/** The card a save sends: the editor's `card` with the greeting list folded
 *  back in and the name trimmed, so the card, the container and the text
 *  `bake_char_name` bakes with all hold the same string. */
export function buildCard(card: Card, greetings: string[]): Card {
  return {
    ...card,
    data: {
      ...card.data,
      name: (card.data.name ?? "").trim(),
      alternate_greetings: greetings.filter((g) => g.trim() !== ""),
    },
  };
}

/** Where a character's page lives, per scope. One place, because the grid, the
 *  world index's redirect and every cross-link have to agree on it. */
export function characterHref(scope: EntityScope, cid: string, vid?: string): string {
  const base = scope.kind === "world"
    ? `/worlds/${scope.id}/characters/${cid}`
    : `/campaigns/${scope.id}/characters/${cid}`;
  return vid ? `${base}?v=${encodeURIComponent(vid)}` : base;
}

/** Where its grid lives — the page a character's `‹ All characters` goes back
 *  to, which is a section of the world view in both scopes. */
export function charactersHref(scope: EntityScope): string {
  return scope.kind === "world"
    ? `/worlds/${scope.id}?section=characters`
    : `/campaigns/${scope.id}/world?section=characters`;
}
