import { useState, type ChangeEvent } from "react";
import type { NavigateFunction } from "react-router-dom";
import { api, type ChubImportResult, type EntityScope } from "../../api/client";
import { errorText } from "../../api/errors";
import type { ImportChoice } from "../character/ImportVersionDialog";
import { characterHref, formatOf } from "../character/shared";

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
  return parts.join(" · ") + (t.failures.length ? ` · failed — ${t.failures.join("; ")}` : "");
}

/** Every way a card gets into a world from the roster -- files, one at a time
 *  or in a drop, and chub.ai URLs -- with the localize pass that follows each,
 *  their progress lines and reports, and the taglines asked for afterwards. */
export function useCharacterImports(
  { wid, scope, reload, setError, navigate }: {
    wid: string;
    scope: EntityScope;
    reload: () => Promise<void>;
    setError: (err: unknown) => void;
    navigate: NavigateFunction;
  },
) {
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [bulkLocalize, setBulkLocalize] = useState<{ current: number; cards: number } | null>(null);
  const [bulkUrl, setBulkUrl] =
    useState<{ current: number; total: number; name: string; step: string } | null>(null);
  const [taglineQueue, setTaglineQueue] = useState<{ cid: string; name: string }[]>([]);
  /** A single-file import waiting on the dialog that says where it lands. */
  const [pendingImport, setPendingImport] = useState<File | null>(null);

  function onPickFiles(e: ChangeEvent<HTMLInputElement>) {
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
    setError(null);
    setImportMsg(null);
    const failures: string[] = [];
    const imported: { cid: string; version: string }[] = [];
    for (const file of files) {
      try {
        const { character, version } = await api.importCharacter(wid, file, formatOf(file));
        imported.push({ cid: character, version });
      } catch (err: unknown) {
        failures.push(`${file.name}: ${errorText(err)}`);
      }
    }
    await reload();
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    if (imported.length) await runBulkLocalize(imported);
  }

  async function confirmImport(choice: ImportChoice) {
    const file = pendingImport;
    setPendingImport(null);
    if (!file) return;
    setError(null);
    setImportMsg(null);
    try {
      const { character, version } = await api.importCharacter(
        wid, file, formatOf(file), choice.into ?? undefined, choice.versionName || undefined);
      await reload();
      await runLocalize(character, version);
      if (choice.into === null) {
        const name = await api.readCharacter(scope, character)
          .then((d) => d.meta.name).catch(() => character);
        setTaglineQueue([{ cid: character, name }]);
      }
      void navigate(characterHref(scope, character, version));
    } catch (err: unknown) { setError(err); }
  }

  async function runLocalize(cid: string, version: string) {
    try {
      await api.localizeImages(wid, cid, version, () => {});
    } catch { /* reported by the character's own page, which shows the images */ }
  }

  /** Localize a batch of freshly-imported cards back to back, accumulating one
   *  aggregate summary. Nothing is open, so progress renders in the toolbar. */
  async function runBulkLocalize(cards: { cid: string; version: string }[]) {
    setImportMsg(null);
    let localized = 0, skipped = 0, failed = 0;
    for (let i = 0; i < cards.length; i++) {
      setBulkLocalize({ current: i + 1, cards: cards.length });
      try {
        await api.localizeImages(wid, cards[i].cid, cards[i].version, (e) => {
          if (e.summary) {
            localized += e.summary.localized;
            skipped += e.summary.skipped;
            failed += e.summary.failed;
          }
        });
      } catch {
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
    step(url, "importing");
    let result: ChubImportResult;
    try {
      result = await api.importCharacterFromChub(wid, url);
    } catch (err: unknown) {
      tally.failures.push(`${url}: ${errorText(err)}`);
      return;
    }
    tally.gallery += result.gallery.stored;
    tally.lore += result.lore.created.length;
    let name = result.character;
    try {
      name = (await api.readCharacter(scope, result.character)).meta.name;
    } catch { /* fall back to the id */ }
    step(name, "localizing images");
    try {
      await api.localizeImages(wid, result.character, result.version, (e) => {
        if (e.summary) tally.localized += e.summary.localized;
      });
    } catch (err: unknown) {
      tally.failures.push(`${name}: localize failed (${errorText(err)})`);
    }
    step(name, "importing lorebook");
    try {
      const { created } = await api.importCharacterBook(wid, result.character, result.version);
      tally.lore += created.length;
    } catch (err: unknown) {
      tally.failures.push(`${name}: lorebook import failed (${errorText(err)})`);
    }
    tally.added.push({ cid: result.character, name });
    await reload();  // the new card appears in the grid as it lands
  }

  /** Per URL: import (the backend downloads the avatar, chub gallery and
   *  related lorebooks inside that one call), localize, then import the card's
   *  embedded book. Failures record and continue — one bad URL shouldn't sink
   *  the batch. */
  async function runBulkUrlImport(urls: string[]) {
    setError(null);
    setImportMsg(null);
    const tally: UrlImportTally = { added: [], failures: [], localized: 0, gallery: 0, lore: 0 };
    for (let i = 0; i < urls.length; i++) {
      const step = (name: string, s: string) =>
        setBulkUrl({ current: i + 1, total: urls.length, name, step: s });
      await importOneUrl(urls[i], step, tally);
    }
    setBulkUrl(null);
    setImportMsg(urlImportReport(urls.length, tally));
    setTaglineQueue(tally.added);
  }

  return {
    importMsg, bulkLocalize, bulkUrl, pendingImport, taglineQueue,
    onPickFiles, confirmImport, runBulkUrlImport,
    cancelPendingImport: () => setPendingImport(null),
    nextTagline: () => setTaglineQueue((q) => q.slice(1)),
  };
}

export type CharacterImports = ReturnType<typeof useCharacterImports>;
