import { useState } from "react";
import {
  api, type Card, type CharacterDetail, type EntityScope, type Greeting,
  type LoreEntryDraft, type ModuleDetail,
} from "../../api/client";
import { HtmlNote } from "../HtmlNote";
import { LoreReviewTable } from "../LoreReviewTable";
import SheetPanel from "../SheetPanel";
import { EditableField } from "./EditableField";
import { describeChubResult, estimateTokens, TEXT_FIELDS } from "./shared";

/** Which card fields sit side by side.
 *
 *  Main is ~1030px at a 1600px viewport and prose caps at 72ch (~660px), so the
 *  remainder is either a gutter or a second column. The long fields take the
 *  full measure; the short ones pair up, which is most of what turns 2370px of
 *  scrolling into roughly one screen. `card-field-pair` is a `repeat(auto-fit,
 *  minmax(320px, 1fr))` grid, so this degrades to one column on its own when
 *  there is no room for two — nothing here has to know the viewport.
 */
const PAIRED = [["personality", "scenario"], ["system_prompt", "post_history_instructions"]];
const SOLO = ["mes_example", "creator_notes"];

/** `TEXT_FIELDS` is the card's PROSE fields; the name is not one of them — it
 *  had its own control above the form, and now it is a field like any other and
 *  needs a label of its own or it renders as its key. */
const EXTRA_LABELS: Record<string, string> = { name: "Name", creator: "Creator" };

const labelOf = (key: string) =>
  TEXT_FIELDS.find((f) => f.key === key)?.label ?? EXTRA_LABELS[key] ?? key;

export function CardTab(
  { scope, wid, cid, vid, card, detail, worldGreetings, module, editing, onEditingChange, busy,
    onSaveField, onRefresh, onError, galleryProg, setGalleryProg, setImportMsg, onOpenGreeting,
    bookCount, bookReview, bookKinds, bookMsg, onReviewBook, onCommitBook, onPatchBook,
    onCancelBook }: {
    scope: EntityScope;
    wid: string;
    cid: string;
    vid: string;
    card: Card;
    detail: CharacterDetail;
    worldGreetings: Greeting[];
    module: ModuleDetail | null;
    editing: string | null;
    onEditingChange: (key: string | null) => void;
    /** A whole-card write is in flight — every other field's control is held
     *  off until it has landed, or the second write is built from a card that
     *  predates the first and silently drops it. */
    busy: boolean;
    onSaveField: (patch: Record<string, unknown>) => Promise<boolean>;
    onRefresh: () => Promise<void>;
    onError: (err: unknown) => void;
    galleryProg: { done: number; total: number } | null;
    setGalleryProg: (p: { done: number; total: number } | null) => void;
    setImportMsg: (m: string | null) => void;
    onOpenGreeting: (gid: string) => void;
    bookCount: number;
    bookReview: LoreEntryDraft[] | null;
    bookKinds: readonly string[];
    bookMsg: string | null;
    onReviewBook: () => void;
    onCommitBook: () => void;
    onPatchBook: (i: number, patch: Partial<LoreEntryDraft>) => void;
    onCancelBook: () => void;
  },
) {
  const worldScope = scope.kind === "world";
  const version = detail.versions.find((v) => v.id === vid);
  const chubSource = version?.chub_source ?? "";
  const isChub = version?.is_chub ?? false;
  const [chubBusy, setChubBusy] = useState(false);

  const text = (key: string) => (card.data[key] as string) ?? "";
  const tags: string[] = card.data.tags ?? [];

  const field = (key: string, extra?: Partial<Parameters<typeof EditableField>[0]>) => (
    <EditableField
      key={key}
      label={labelOf(key)}
      value={text(key)}
      editing={editing === `card:${key}`}
      onEditingChange={(open) => onEditingChange(open ? `card:${key}` : null)}
      onSave={(next) => onSaveField({ [key]: next })}
      disabled={busy}
      {...extra}
    />
  );

  async function linkChub() {
    const url = window.prompt("Card URL (chub.ai link or a direct URL)?")?.trim();
    if (!url) return;
    try {
      await api.setCharacterChubSource(wid, cid, vid, url);
      await onRefresh();
    } catch (err: unknown) { onError(err); }
  }

  async function unlinkChub() {
    try {
      await api.clearCharacterChubSource(wid, cid, vid);
      await onRefresh();
    } catch (err: unknown) { onError(err); }
  }

  /** One-click re-import from the version's stored link — the backend matches
   *  the source and overwrites this version in place rather than forking one. */
  async function redownload() {
    if (!chubSource) return;
    setChubBusy(true);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, chubSource, cid, vid);
      await onRefresh();
      setImportMsg(describeChubResult(result));
    } catch (err: unknown) { onError(err); } finally { setChubBusy(false); }
  }

  async function downloadGallery() {
    setImportMsg(null);
    setGalleryProg({ done: 0, total: 0 });
    let finalMsg = "";
    try {
      await api.downloadCharacterChubGallery(wid, cid, vid, (e) => {
        if (e.error) finalMsg = `Gallery download failed: ${e.error.detail}`;
        else if (e.summary) {
          const s = e.summary;
          finalMsg = s.attempted === 0 ? "No gallery images found on chub.ai"
            : `${s.stored}/${s.attempted} gallery image${s.attempted === 1 ? "" : "s"} downloaded`;
        } else if (typeof e.done === "number") setGalleryProg({ done: e.done, total: e.total ?? 0 });
        else if (typeof e.total === "number") setGalleryProg({ done: 0, total: e.total });
      });
      await onRefresh();
    } catch (err: unknown) { onError(err); } finally {
      setGalleryProg(null);
      if (finalMsg) setImportMsg(finalMsg);
    }
  }

  async function downloadLorebooks() {
    setImportMsg(null);
    try {
      const result = await api.downloadCharacterChubLorebooks(wid, cid, vid);
      const n = result.created.length;
      setImportMsg(result.lorebooks_found === 0
        ? "No linked lorebooks found on chub.ai"
        : `${result.lorebooks_found} lorebook${result.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`);
    } catch (err: unknown) { onError(err); }
  }

  // World greetings that feature this character: separate world records, not
  // card content, which is why they do not count toward the Greetings tab. The
  // ★ marks the ones they are the primary of. Chips that navigate, per the
  // list/detail rule for metadata referencing other records.
  const featuring = worldGreetings.filter((g) => (g.present ?? []).includes(cid));

  return <>
    <div className="card-field-pair">
      {field("name", { multiline: false })}
      {field("creator", { multiline: false, placeholder: "Nobody credited." })}
    </div>

    {field("description", {
      // The cost of this one field, where it is being read. It is the largest
      // thing on the card and it goes out every single turn they are on stage —
      // which is the fact the stamp exists to make legible before it is paid.
      stamp: text("description").trim() ? (
        <span className="card-field-cost">
          ≈ {estimateTokens(text("description")).toLocaleString()} tokens · sent every turn in scene
        </span>
      ) : undefined,
    })}

    {PAIRED.map((pair) => (
      <div className="card-field-pair" key={pair.join("+")}>{pair.map((k) => field(k))}</div>
    ))}
    {SOLO.map((k) => field(k, k === "creator_notes" && text(k).trim()
      ? { rendered: <HtmlNote html={text(k)} title="Creator notes" /> }
      : undefined))}

    <EditableField
      label="Tags"
      value={tags.join(", ")}
      multiline={false}
      hint="comma-separated"
      placeholder="No tags."
      rendered={<div className="chip-row">
        {tags.map((t) => <span className="chip on" key={t}>{t}</span>)}
      </div>}
      editing={editing === "card:tags"}
      onEditingChange={(open) => onEditingChange(open ? "card:tags" : null)}
      disabled={busy}
      onSave={(next) => onSaveField({
        tags: next.split(",").map((t) => t.trim()).filter(Boolean),
      })}
    />

    {(card.data.extensions?.sd_prompt) ? (
      <div className="card-field">
        <div className="card-field-head"><span className="data-label">Image prompt</span></div>
        <div className="field-hint">{String(card.data.extensions.sd_prompt)}</div>
      </div>
    ) : null}

    {featuring.length > 0 && (
      <div className="card-field">
        <div className="card-field-head"><span className="data-label">World greetings</span></div>
        <div className="chip-row">
          {featuring.map((g) => (
            <button key={g.id} className="chip on" onClick={() => onOpenGreeting(g.id)}>
              {g.character === cid ? `★ ${g.name}` : g.name}
            </button>
          ))}
        </div>
      </div>
    )}

    {worldScope && (
      <div className="chub-source-block">
        {chubSource ? <>
          <a className="field-hint"
             href={chubSource.startsWith("http") ? chubSource : `https://chub.ai/characters/${chubSource}`}
             target="_blank" rel="noreferrer">{chubSource}</a>
          <button className="subtle" type="button" disabled={chubBusy}
                  onClick={() => void redownload()}>Re-download</button>
          <button className="subtle" type="button" onClick={() => void unlinkChub()}>Unlink</button>
          {isChub && <>
            <button className="subtle" type="button" disabled={!!galleryProg}
                    onClick={() => void downloadGallery()}>
              {galleryProg ? "Downloading…" : "Download gallery"}
            </button>
            <button className="subtle" type="button"
                    onClick={() => void downloadLorebooks()}>Download linked lorebooks</button>
            {galleryProg && (
              <div className="localize-progress">
                <progress value={galleryProg.done} max={galleryProg.total || 1} />
                <span className="field-hint">{galleryProg.done}/{galleryProg.total}</span>
              </div>
            )}
          </>}
        </> : (
          <button className="subtle" type="button" onClick={() => void linkChub()}>Link to URL</button>
        )}
      </div>
    )}

    {worldScope && bookCount > 0 && (
      <div className="book-import">
        {bookReview === null ? <>
          <button className="subtle" type="button" onClick={onReviewBook}>
            Review {bookCount} embedded lore {bookCount === 1 ? "entry" : "entries"} to import
          </button>
          {bookMsg && <span className="field-hint">{bookMsg}</span>}
        </> : <>
          <div className="field-hint">
            Review and route each entry, then import — nothing is written until then.
          </div>
          <LoreReviewTable entries={bookReview} kinds={bookKinds}
                           onPatch={onPatchBook} onCommit={onCommitBook} />
          <div className="form-actions">
            <button className="subtle" type="button" onClick={onCancelBook}>Cancel</button>
          </div>
        </>}
      </div>
    )}

    {module && (
      /* onOpenRef intentionally unset: no cross-editor navigation target exists
         yet from a character sheet's ref chips (entity-form refs only; module-
         content ref chips still preview correctly without it) */
      <SheetPanel scope={scope} module={module} kind="characters" eid={cid} />
    )}
  </>;
}

export default CardTab;
