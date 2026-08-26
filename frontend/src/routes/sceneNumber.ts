/** The scene's number in STORY order, off its own id.
 *
 *  A scene id's filename stem is `<NNN>--<date>--<slug>`, and that leading
 *  number is the only thing that says where the scene sits in the campaign.
 *  List position cannot: `list_scenes` sorts by `updated`, so re-editing any
 *  earlier scene reorders the list while the story stays where it was.
 *
 *  `null` for an id that carries no number — a store written before ids were
 *  padded, or one hand-renamed. The caller renders a dash rather than a
 *  position that would be wrong for exactly those scenes.
 *
 *  Shared by the play view's rail and the scenes list, which have to agree:
 *  two places deriving "scene 14" separately is two places for it to drift.
 */
export function sceneNumber(id: string): number | null {
  const m = /^(\d+)--/.exec(id);
  return m ? parseInt(m[1], 10) : null;
}
