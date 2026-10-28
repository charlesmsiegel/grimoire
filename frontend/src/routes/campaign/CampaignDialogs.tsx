import type { Dispatch, SetStateAction } from "react";
import { NewSceneChooser } from "../../components/NewSceneChooser";
import { PassageCharacterDialog } from "../../components/PassageCharacterDialog";
import type { RegexTestTarget } from "../../components/play/RegexTestDialog";
import { RegexTestDialog } from "../../components/play/RegexTestDialog";
import type { PickerTarget } from "../../components/PostImagePicker";
import { PostImagePicker } from "../../components/PostImagePicker";
import type { DrawerTarget } from "../../components/RecordDrawer";
import { RecordDrawer } from "../../components/RecordDrawer";

export function CampaignDialogs({
  characterPassage, cid, activeId, setCharacterPassage, currentCampaign, selected, refreshSavedScene, drawer, setDrawer, chooserOpen, ready, closeChooser, sceneCreated, fail, picking, insertImage, setPicking, regexTest, setRegexTest
}: {
  characterPassage: { cid: string; sid: string; rid: string; source: string; } | null;
  cid: string;
  activeId: string | null;
  setCharacterPassage: Dispatch<SetStateAction<{ cid: string; sid: string; rid: string; source: string; } | null>>;
  currentCampaign: () => string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
  refreshSavedScene: (id: string, operation: string) => void;
  drawer: DrawerTarget | null;
  setDrawer: Dispatch<SetStateAction<DrawerTarget | null>>;
  chooserOpen: boolean;
  ready: boolean;
  closeChooser: (createdSid?: string) => void;
  sceneCreated: (id: string, initialPrompt?: string) => Promise<void>;
  fail: (e: unknown, retryable?: boolean, from?: string) => void;
  picking: { index: number; target: PickerTarget; } | null;
  insertImage: (index: number, markdown: string) => void;
  setPicking: Dispatch<SetStateAction<{ index: number; target: PickerTarget; } | null>>;
  regexTest: RegexTestTarget | null;
  setRegexTest: Dispatch<SetStateAction<RegexTestTarget | null>>;
}) {
  return (
    <>        {characterPassage && characterPassage.cid === cid && characterPassage.sid === activeId && (
      <PassageCharacterDialog key={characterPassage.rid} cid={cid} sid={characterPassage.sid}
        responseId={characterPassage.rid} sourceText={characterPassage.source}
        onClose={() => setCharacterPassage(null)} onSaved={() => {
          const saved = characterPassage;
          setCharacterPassage(null);
          if (currentCampaign() === saved.cid && (selected()?.sid ?? null) === saved.sid) refreshSavedScene(saved.sid, "Character saved");
        }} />
    )}
      {drawer && activeId && (
        <RecordDrawer cid={cid} sid={activeId} target={drawer} onClose={() => setDrawer(null)} />
      )}
      {chooserOpen && (
        <NewSceneChooser cid={cid} afterSid={activeId} ready={ready}
          onClose={closeChooser} onCreated={(id, prompt) => { sceneCreated(id, prompt).catch((err: unknown) => fail(err, false)); }} />
      )}
      {picking && (
        <PostImagePicker cid={cid} target={picking.target}
          onInsert={(md) => insertImage(picking.index, md)}
          onClose={() => setPicking(null)} />
      )}
      {regexTest && (
        <RegexTestDialog cid={cid} target={regexTest} onClose={() => setRegexTest(null)} />
      )}
    </>
  );
}
