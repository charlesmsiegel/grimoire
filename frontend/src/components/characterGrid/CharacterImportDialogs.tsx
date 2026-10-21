import type { CharacterSummary } from "../../api/client";
import { TaglinePrompt } from "../TaglinePrompt";
import { UrlImportPrompt } from "../UrlImportPrompt";
import { ImportVersionDialog } from "../character/ImportVersionDialog";
import type { CharacterImports } from "./useCharacterImports";

/** The import dialogs the roster opens over itself: the tagline asked of each
 *  newly added character in turn, the URL list, and where a single card lands. */
export function CharacterImportDialogs(
  { wid, chars, imports, urlPromptOpen, onCloseUrlPrompt, onTaglineSaved }: {
    wid: string;
    chars: CharacterSummary[];
    imports: CharacterImports;
    urlPromptOpen: boolean;
    onCloseUrlPrompt: () => void;
    onTaglineSaved: () => void;
  },
) {
  const { taglineQueue, pendingImport } = imports;
  return <>
    {taglineQueue.length > 0 && (
      <TaglinePrompt key={taglineQueue[0].cid} wid={wid} cid={taglineQueue[0].cid}
                     name={taglineQueue[0].name}
                     onSaved={onTaglineSaved}
                     onClose={imports.nextTagline} />
    )}
    {urlPromptOpen && (
      <UrlImportPrompt onClose={onCloseUrlPrompt}
                       onSubmit={(urls) => void imports.runBulkUrlImport(urls)} />
    )}
    {pendingImport && (
      <ImportVersionDialog fileName={pendingImport.name} characters={chars}
                           onCancel={imports.cancelPendingImport}
                           onConfirm={(c) => void imports.confirmImport(c)} />
    )}
  </>;
}
