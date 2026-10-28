import { useState, type ChangeEvent } from "react";
import type { NavigateFunction } from "react-router-dom";
import { api, type ChubImportResult, type EntityScope } from "../../api/client";
import { errorText } from "../../api/errors";
import type { ImportChoice } from "../character/ImportVersionDialog";
import { characterHref, formatOf, type CharacterImportNotice } from "../character/shared";
import { useResetOnKey, type VisitFence } from "./useCharacterRoster";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The toolbar's report on a batch of localized cards. */
function localizeReport(cards: number, localized: number, skipped: number, failed: number): string {
  return `Localized ${localized} image${plural(localized, "", "s")} across ${cards} cards`
    + (skipped ? `, skipped ${skipped}` : "") + (failed ? `, ${failed} failed` : "");
}

/** What a URL import added up to, across the batch. */
type UrlImportTally = {
  added: { cid: string; name: string }[];
  failures: string[];
  refreshWarnings: string[];
  localized: number;
  gallery: number;
  lore: number;
};

/** The toolbar's report on a batch of URL imports. */
function urlImportReport(total: number, t: UrlImportTally): string {
  const parts = [`Added ${t.added.length}/${total} character${plural(total, "", "s")}`];
  if (t.gallery) parts.push(`${t.gallery} gallery image${plural(t.gallery, "", "s")}`);
  if (t.localized) parts.push(`${t.localized} image${plural(t.localized, "", "s")} localized`);
  if (t.lore) parts.push(`${t.lore} lore entr${plural(t.lore, "y", "ies")} imported`);
  if (t.failures.length) parts.push(`failed — ${t.failures.join("; ")}`);
  parts.push(...t.refreshWarnings);
  return parts.join(" · ");
}

/** Every way a card gets into a world from the roster -- files, one at a time
 *  or in a drop, and chub.ai URLs -- with the localize pass that follows each,
 *  their progress lines and reports, and the taglines asked for afterwards. */
export function useCharacterImports(
  { wid, scope, reload, setError, navigate, fence: { visit, liveVisit } }: {
    wid: string;
    scope: EntityScope;
    reload: () => Promise<void>;
    setError: (err: unknown) => void;
    navigate: NavigateFunction;
    fence: VisitFence;
  },
) {
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [bulkLocalize, setBulkLocalize] = useState<{ current: number; cards: number } | null>(null);
  const [bulkUrl, setBulkUrl] =
    useState<{ current: number; total: number; name: string; step: string } | null>(null);
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
  /** A single-file import waiting on the dialog that says where it lands. */
  const [pendingImport, setPendingImport] = useState<File | null>(null);

  // A dialog, progress line or report belongs to one stay on the roster.
  // Returning to the same world must not revive its old continuations.
  useResetOnKey(visit.key, () => {
    setImportMsg(null);
    setBulkLocalize(null);
    setBulkUrl(null);
    setTaglineQueue([]);
    setPendingImport(null);
  });
  const current = () => liveVisit.current === visit;

  /** The import is already committed. A failed listing is a refresh warning,
   *  never a reason to retry that import or abandon the remaining cards. */
  async function refreshImported(): Promise<string | null> {
    if (!current()) return null;
    try {
      await reload();
    } catch (err: unknown) {
      if (current()) return `Roster refresh failed — ${errorText(err)}`;
    }
    return null;
  }

  function onPickFiles(e: ChangeEvent<HTMLInputElement>) {
    if (!current()) return;
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    // A single card is asked where it lands — a new character, or a named
    // version of one you already have. A thirty-card drop is answered by making
    // thirty characters, because a dialog per file is worse than no dialog.
    if (files.length === 1) setPendingImport(files[0]);
    else void importMany(files);
  }

  async function importMany(files: File[]) {
    if (!current()) return;
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const imported: { cid: string; version: string }[] = [];
    for (const file of files) {
      if (!current()) return;
      try {
        const { character, version } = await api.importCharacter(wid, file, formatOf(file));
        if (!current()) return;
        imported.push({ cid: character, version });
      } catch (err: unknown) {
        if (!current()) return;
        failures.push(`${file.name}: ${errorText(err)}`);
      }
    }
    const warning = await refreshImported();
    if (!current()) return;
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    if (imported.length) await runBulkLocalize(imported);
    if (!current()) return;
    if (warning) setImportMsg((msg) => [msg, warning].filter(Boolean).join(" · "));
  }

  async function confirmImport(choice: ImportChoice) {
    if (!current()) return;
    const file = pendingImport;
    setPendingImport(null);
    if (!file) return;
    setError(null);
    setImportMsg(null);
    try {
      const { character, version } = await api.importCharacter(
        wid, file, formatOf(file), choice.into ?? undefined, choice.versionName || undefined);
      if (!current()) return;
      const warning = await refreshImported();
      if (!current()) return;
      if (warning) setImportMsg(warning);
      await runLocalize(character, version);
      if (!current()) return;
      if (choice.into === null) {
        const name = await api.readCharacter(scope, character)
          .then((d) => d.meta.name).catch(() => character);
        if (!current()) return;
        setTaglineQueue([{ cid: character, name }]);
      }
      const notice: CharacterImportNotice | null = warning
        ? { scope, character, version, warning } : null;
      void navigate(characterHref(scope, character, version),
                    notice ? { state: { characterImportNotice: notice } } : undefined);
    } catch (err: unknown) { if (current()) setError(err); }
  }

  async function runLocalize(cid: string, version: string) {
    if (!current()) return;
    try {
      await api.localizeImages(wid, cid, version, () => {});
    } catch { /* reported by the character's own page, which shows the images */ }
  }

  /** Localize a batch of freshly-imported cards back to back, accumulating one
   *  aggregate summary. Nothing is open, so progress renders in the toolbar. */
  async function runBulkLocalize(cards: { cid: string; version: string }[]) {
    if (!current()) return;
    setImportMsg(null);
    let localized = 0, skipped = 0, failed = 0;
    for (let i = 0; i < cards.length; i++) {
      if (!current()) return;
      setBulkLocalize({ current: i + 1, cards: cards.length });
      try {
        await api.localizeImages(wid, cards[i].cid, cards[i].version, (e) => {
          if (current() && e.summary) {
            localized += e.summary.localized;
            skipped += e.summary.skipped;
            failed += e.summary.failed;
          }
        });
        if (!current()) return;
      } catch {
        if (!current()) return;
        failed += 1;  // a whole card's localize failing shouldn't abort the batch
      }
    }
    setBulkLocalize(null);
    setImportMsg(localizeReport(cards.length, localized, skipped, failed));
  }

  /** One URL of a batch: import it, then localize its images and import its
   *  embedded book, tallying what each step added and naming what failed. */
  async function importOneUrl(url: string, step: (name: string, step: string) => void,
                              tally: UrlImportTally) {
    if (!current()) return;
    step(url, "importing");
    let result: ChubImportResult;
    try {
      result = await api.importCharacterFromChub(wid, url);
    } catch (err: unknown) {
      if (!current()) return;
      tally.failures.push(`${url}: ${errorText(err)}`);
      return;
    }
    if (!current()) return;
    tally.gallery += result.gallery.stored;
    tally.lore += result.lore.created.length;
    let name = result.character;
    try {
      name = (await api.readCharacter(scope, result.character)).meta.name;
    } catch { /* fall back to the id */ }
    if (!current()) return;
    step(name, "localizing images");
    try {
      await api.localizeImages(wid, result.character, result.version, (e) => {
        if (current() && e.summary) tally.localized += e.summary.localized;
      });
    } catch (err: unknown) {
      if (!current()) return;
      tally.failures.push(`${name}: localize failed (${errorText(err)})`);
    }
    if (!current()) return;
    step(name, "importing lorebook");
    try {
      const { created } = await api.importCharacterBook(wid, result.character, result.version);
      if (!current()) return;
      tally.lore += created.length;
    } catch (err: unknown) {
      if (!current()) return;
      tally.failures.push(`${name}: lorebook import failed (${errorText(err)})`);
    }
    tally.added.push({ cid: result.character, name });
    const warning = await refreshImported();  // the new card appears in the grid as it lands
    if (current() && warning) tally.refreshWarnings.push(warning);
  }

  /** Per URL: import (the backend downloads the avatar, chub gallery and
   *  related lorebooks inside that one call), localize, then import the card's
   *  embedded book. Failures record and continue — one bad URL shouldn't sink
   *  the batch. */
  async function runBulkUrlImport(urls: string[]) {
    if (!current()) return;
    setError(null);
    setImportMsg(null);
    const tally: UrlImportTally = {
      added: [], failures: [], refreshWarnings: [], localized: 0, gallery: 0, lore: 0,
    };
    for (let i = 0; i < urls.length; i++) {
      if (!current()) return;
      const step = (name: string, s: string) => {
        if (current()) setBulkUrl({ current: i + 1, total: urls.length, name, step: s });
      };
      await importOneUrl(urls[i], step, tally);
      if (!current()) return;
    }
    setBulkUrl(null);
    setImportMsg(urlImportReport(urls.length, tally));
    setTaglineQueue(tally.added);
  }

  return {
    importMsg, bulkLocalize, bulkUrl, pendingImport, taglineQueue,
    onPickFiles, confirmImport, runBulkUrlImport,
    cancelPendingImport: () => { if (current()) setPendingImport(null); },
    nextTagline: () => { if (current()) setTaglineQueue((q) => q.slice(1)); },
  };
}

export type CharacterImports = ReturnType<typeof useCharacterImports>;
