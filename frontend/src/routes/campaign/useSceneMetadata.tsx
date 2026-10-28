import { useMemo } from "react";
import type { SceneMeta } from "../../api/client";

export function useSceneMetadata({ scenes, activeId }: {
  scenes: SceneMeta[];
  activeId: string | null;
}) {

  // offscreen scenes take director notes instead of PC dialogue
  const activePcless = useMemo(
    () => scenes.find((s) => s.id === activeId)?.pcless ?? false,
    [scenes, activeId]);

  // A scene absorbed into the chronicle is finished: its summary is written and
  // its changes are applied, so anything appended now sits outside the record
  // that was taken of it. The composer goes entirely rather than being disabled
  // -- a greyed-out entry box still says "you could type here".
  const activeDone = useMemo(
    () => scenes.find((s) => s.id === activeId)?.done ?? false,
    [scenes, activeId]);

  // A closed branch (play controls III): a sibling in its branch group was
  // absorbed, so the campaign's files now hold THAT past and this one is
  // read-only. Rendered like an absorbed scene and further still -- no
  // composer, no gutter actions, no swipes, no reroll keys -- because every
  // write here is refused `branch_closed` anyway. The server resolves which
  // sibling, so the banner can link it.
  const activeClosed = useMemo(
    () => scenes.find((s) => s.id === activeId)?.closed_by ?? null,
    [scenes, activeId]);
  // Whether the active scene has a sibling: its group has another row.
  const activeBranched = useMemo(() => {
    const group = scenes.find((s) => s.id === activeId)?.branch_group;
    return !!group && scenes.filter((s) => s.branch_group === group).length > 1;
  }, [scenes, activeId]);

  const sceneTitle = scenes.find((s) => s.id === activeId)?.title ?? "";
  return { sceneTitle, activePcless, activeDone, activeClosed, activeBranched };
}
